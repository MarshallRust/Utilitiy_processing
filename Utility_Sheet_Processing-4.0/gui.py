"""
The window the office uses. Double-click the .exe and this is what opens.

    Start      - sign in once per computer, pick the scanned PDF, "Read bills"
    Step 1     - every page's address next to the picture it was read from,
                 grouped: will be entered / already in the sheet / won't be.
                 Fix any address, tick "I've checked every address", Continue.
    Step 2     - each bill that will be entered, one at a time: the page with
                 a box around every value, a close-up of each, and the value
                 in an editable box. Tick "looks right" for each (editing a
                 value ticks it), then Confirm - or Skip the bill.
    Step 3     - exactly what will be written. "Write to the sheet".
    Done       - the report, the stamped PDF, the folder.

Slow work (OCR, Google) runs in a background thread so the window stays
responsive; it reports back through a queue the window checks every 100 ms.
The rules behind steps 1-3 live in review.py, not here.
"""

import os
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
import tkinter as tk

# A windowed .exe has no console - anything that prints would crash.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from PIL import Image, ImageDraw, ImageFont, ImageTk

import settings
from app_paths import APP_NAME, configure_tesseract, user_dir

PAD = 8
FIELD_COLORS = {  # box color on the page for each value
    "amount": "#1f6fd1",
    "bill_date": "#e07b00",
    "effective_dates": "#1e9e4a",
    "due_date": "#8e44ad",
}
CHECK_COLOR = "#8a8a8a"  # the other numbers used to check the amount
GREEN, RED, ORANGE, BLUE, GRAY = "#1e7d32", "#c62828", "#b35c00", "#1f5fbf", "#666666"
GROUP_TITLES = {
    "enter": "Will be entered",
    "already": "Already in the sheet",
    "skip": "Won't be entered",
}


def open_path(path):
    """Open a file or folder with whatever Windows/macOS would use."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)


def fit(image, max_w, max_h):
    """Scale a PIL image down (never up) to fit max_w x max_h."""
    if image is None:
        return None
    scale = min(max_w / image.width, max_h / image.height, 1.0)
    if scale < 1.0:
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.LANCZOS)
    return image


def _font(size):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


class Scrollable(ttk.Frame):
    """A frame that scrolls vertically, with the mouse wheel working while
    the pointer is over it. Put widgets in .inner."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

    def _unbind_wheel(self, _event=None):
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(sequence)

    def _on_wheel(self, event):
        if sys.platform == "darwin":
            self.canvas.yview_scroll(-event.delta, "units")
        else:
            self.canvas.yview_scroll(int(-event.delta / 120) * 3, "units")

    def destroy(self):
        self._unbind_wheel()
        super().destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.settings = settings.load()
        self.creds = None
        self.busy = False
        self.messages = queue.Queue()
        self.session = None
        self.last_summary = None
        self.photos = []        # keep PhotoImages alive while shown
        self.page_cache = {}    # page number -> 300 DPI image (only the bill being reviewed)
        self.screen = None
        self.on_return = None   # what Enter does on the current screen

        root.title(APP_NAME)
        root.minsize(1000, 680)
        root.geometry("1280x860")
        if sys.platform == "win32":
            try:
                root.state("zoomed")  # review needs the room
            except tk.TclError:
                pass
        root.bind("<Return>", self._return_pressed)
        root.after(100, self._poll)
        self.show_start()
        root.after(200, self._startup)

    # --- plumbing --------------------------------------------------------

    def _clear(self):
        self.bill_page = None
        self.field_vars = {}
        if self.screen is not None:
            self.screen.destroy()
        self.photos = []
        self.on_return = None
        self.screen = ttk.Frame(self.root, padding=PAD * 2)
        self.screen.pack(fill="both", expand=True)
        return self.screen

    def _photo(self, image):
        if image is None:
            return None
        photo = ImageTk.PhotoImage(image)
        self.photos.append(photo)
        return photo

    def _return_pressed(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Entry)):
            return  # the entry's own binding handles Enter
        if self.on_return and not self.busy:
            self.on_return()

    def _in_background(self, work, on_done=None):
        """Run work() off the main thread. Its return value goes to on_done
        on the main thread; an exception goes to _on_error."""
        self.busy = True

        def wrapper():
            try:
                result = work()
            except Exception as e:
                self.messages.put(("error", e, traceback.format_exc()))
            else:
                self.messages.put(("done", on_done, result))
        threading.Thread(target=wrapper, daemon=True).start()

    def _progress(self, text):
        self.messages.put(("progress", text))

    def _poll(self):
        try:
            while True:
                kind, *payload = self.messages.get_nowait()
                if kind == "progress":
                    self._show_progress_text(payload[0])
                elif kind == "done":
                    self.busy = False
                    on_done, result = payload
                    if on_done:
                        on_done(result)
                elif kind == "error":
                    self.busy = False
                    self._on_error(*payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _show_progress_text(self, text):
        if getattr(self, "progress_var", None) is not None:
            try:
                self.progress_var.set(text)
                if text.startswith("Reading page ") and " of " in text:
                    page, total = text.split()[2], text.split()[4].rstrip(".")
                    self.progress_bar.configure(maximum=int(total), value=int(page))
            except (tk.TclError, ValueError, IndexError, AttributeError):
                pass

    def _on_error(self, error, details):
        import google_sheets
        if isinstance(error, google_sheets.SheetError):
            message = str(error)
        else:
            log = user_dir() / "logs" / f"{datetime.now():%Y-%m-%d_%H%M%S}_error.txt"
            try:
                log.parent.mkdir(exist_ok=True)
                log.write_text(details, encoding="utf-8")
            except OSError:
                pass
            message = f"Something went wrong: {error}\n\nThe details were saved to:\n{log}"
        messagebox.showerror(APP_NAME, message)
        retry, self.after_error = getattr(self, "after_error", None), None
        if retry:
            retry()  # back to the screen the work started from, so it can be retried
        elif self.session is None or self.last_summary is not None:
            self.show_start()

    # --- start screen ------------------------------------------------------

    def show_start(self):
        self.session = None
        self.page_cache = {}
        frame = self._clear()
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Utility bills → billing sheet", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, PAD * 2))

        ttk.Label(frame, text="1. Google account").grid(row=1, column=0, sticky="w", pady=PAD)
        self.account_label = ttk.Label(frame, text="Checking...")
        self.account_label.grid(row=1, column=1, sticky="w", padx=PAD)
        self.account_button = ttk.Button(frame, text="Sign in with Google", command=self._sign_in_or_out)
        self.account_button.grid(row=1, column=2, sticky="ew")

        ttk.Label(frame, text="2. Billing sheet").grid(row=2, column=0, sticky="w", pady=PAD)
        self.sheet_var = tk.StringVar(value=self.settings["sheet_link"])
        ttk.Entry(frame, textvariable=self.sheet_var).grid(row=2, column=1, sticky="ew", padx=PAD)
        ttk.Button(frame, text="Open sheet", command=lambda: webbrowser.open(self.sheet_var.get())).grid(
            row=2, column=2, sticky="ew")

        ttk.Label(frame, text="3. Scanned bills (PDF)").grid(row=3, column=0, sticky="w", pady=PAD)
        self.pdf_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pdf_var).grid(row=3, column=1, sticky="ew", padx=PAD)
        ttk.Button(frame, text="Browse...", command=self._browse).grid(row=3, column=2, sticky="ew")

        self.read_button = ttk.Button(frame, text="Read bills", command=self._start_read)
        self.read_button.grid(row=4, column=0, columnspan=3, sticky="w", pady=(PAD * 2, PAD))
        ttk.Label(frame, foreground=GRAY, text=(
            "Reading changes nothing. You'll check every address and every bill before anything is "
            "written, and nothing already in the sheet is ever overwritten.")).grid(
            row=5, column=0, columnspan=3, sticky="w")

        self.progress_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.progress_var).grid(row=6, column=0, columnspan=3, sticky="w", pady=(PAD, 0))
        self.progress_bar = ttk.Progressbar(frame, mode="determinate")
        self.progress_bar.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(PAD // 2, PAD))

        ttk.Label(frame, foreground=GRAY, text=f"Run logs: {user_dir() / 'logs'}").grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(PAD * 2, 0))
        self._refresh_account()
        self.on_return = self._start_read

    def _startup(self):
        try:
            configure_tesseract()
        except RuntimeError as e:
            messagebox.showerror(APP_NAME, str(e))

        def work():
            import google_sheets
            creds = google_sheets.load_saved_credentials()
            return creds, (google_sheets.signed_in_email(creds) if creds else None)
        self._in_background(work, self._signed_in)

    def _sign_in_or_out(self):
        import google_sheets
        if self.creds:
            google_sheets.sign_out()
            self._signed_in((None, None))
            return
        self.progress_var.set("A browser window opened - sign in with your Catalyst Google account there.")

        def work():
            creds = google_sheets.sign_in()
            return creds, google_sheets.signed_in_email(creds)
        self._in_background(work, self._signed_in)

    def _signed_in(self, result):
        self.creds, self.email = result
        self._refresh_account()
        if getattr(self, "progress_var", None) is not None:
            self.progress_var.set("")

    def _refresh_account(self):
        try:
            if self.creds:
                self.account_label.configure(text=f"Signed in as {getattr(self, 'email', None) or 'your Google account'}")
                self.account_button.configure(text="Sign out")
            else:
                self.account_label.configure(text="Not signed in" if hasattr(self, "email") else "Checking...")
                self.account_button.configure(text="Sign in with Google")
        except tk.TclError:
            pass  # start screen isn't showing

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Pick the scanned utility bills",
            initialdir=self.settings.get("last_folder") or None,
            filetypes=[("PDF files", "*.pdf")],
        )
        if path:
            self.pdf_var.set(path)
            self.settings["last_folder"] = str(Path(path).parent)
            settings.save(self.settings)

    def _start_read(self):
        if self.busy:
            return
        if not self.creds:
            messagebox.showinfo(APP_NAME, "Sign in with Google first (step 1).")
            return
        pdf = Path(self.pdf_var.get().strip().strip('"'))
        if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
            messagebox.showinfo(APP_NAME, "Pick the scanned PDF first (step 3).")
            return
        import google_sheets
        try:
            sheet_id = google_sheets.sheet_id_from_link(self.sheet_var.get())
        except google_sheets.SheetError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        self.settings["sheet_link"] = self.sheet_var.get().strip()
        settings.save(self.settings)
        self.read_button.configure(state="disabled")
        creds = self.creds

        def work():
            import pipeline
            import review
            sheet = google_sheets.Sheet(creds, sheet_id)
            title, tabs = sheet.title_and_tabs()
            pages = pipeline.read_pages(pdf, tabs, self._progress)
            self._progress("Checking the sheet...")
            grids = sheet.read_tabs(p.tab for p in pages if p.tab)
            return review.ReviewSession(pdf, sheet, title, tabs, pages, grids)

        def done(session):
            self.session = session
            self.last_summary = None
            self.show_addresses()
        self._in_background(work, done)

    def start_review(self, session):
        """Jump straight to step 1 with an already-read session (self-test)."""
        self.session = session
        self.last_summary = None
        self.show_addresses()

    # --- step 1: addresses ---------------------------------------------------

    def show_addresses(self):
        s = self.session
        frame = self._clear()
        groups = s.groups()
        counts = {g: len(items) for g, items in groups.items()}

        ttk.Label(frame, text="Step 1 of 3 - Check the addresses", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, wraplength=1100, text=(
            f"Compare each address with the picture next to it. {counts['enter']} bill(s) will be entered, "
            f"{counts['already']} are already in the sheet, {counts['skip']} page(s) won't be entered. "
            "Make sure everything under \"Will be entered\" is a property we split, and nothing under "
            "\"Won't be entered\" should be. Fix an address by typing in its box and pressing Enter.")).pack(
            anchor="w", pady=(PAD // 2, PAD))

        list_area = Scrollable(frame)
        list_area.pack(fill="both", expand=True)
        self.address_rows = {}
        for group in ("enter", "already", "skip"):
            header = ttk.Label(list_area.inner, text=f"{GROUP_TITLES[group]} ({counts[group]})",
                               font=("Segoe UI", 13, "bold"))
            header.pack(anchor="w", pady=(PAD * 2, PAD // 2))
            if not groups[group]:
                ttk.Label(list_area.inner, text="(none)", foreground=GRAY).pack(anchor="w", padx=PAD)
            for page, decision in groups[group]:
                self._address_row(list_area.inner, page, decision)

        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(PAD, 0))
        self.addresses_checked_var = tk.BooleanVar(value=s.addresses_checked)
        ttk.Checkbutton(bottom, text="I've compared every address with its picture",
                        variable=self.addresses_checked_var, command=self._addresses_ticked).pack(side="left")
        ttk.Button(bottom, text="Start over", command=self._start_over).pack(side="right")
        self.continue_button = ttk.Button(bottom, text="Continue to the bills  >", command=self._addresses_done)
        self.continue_button.pack(side="right", padx=PAD)
        self._addresses_ticked()
        self.progress_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.progress_var, foreground=GRAY).pack(anchor="w")
        self.progress_bar = ttk.Progressbar(frame)  # not shown; keeps _show_progress_text happy

    def _address_row(self, parent, page, decision):
        row = ttk.Frame(parent, padding=(PAD, PAD // 2))
        row.pack(fill="x")
        ttk.Label(row, text=f"Page {page.page}", width=8).grid(row=0, column=0, rowspan=2, sticky="nw")
        crop = fit(page.address_crop, 520, 90)
        photo = self._photo(crop)
        ttk.Label(row, image=photo, relief="solid", borderwidth=1).grid(row=0, column=1, rowspan=2, sticky="w")
        if page.is_bill:
            var = tk.StringVar(value=page.address or "")
            entry = ttk.Entry(row, textvariable=var, width=34, font=("Segoe UI", 11))
            entry.grid(row=0, column=2, sticky="w", padx=PAD)
            entry.bind("<Return>", lambda e, n=page.page, v=var: self._address_edited(n, v) or "break")
            entry.bind("<FocusOut>", lambda e, n=page.page, v=var: self._address_edited(n, v))
        else:
            ttk.Label(row, text="(not a Logan City or Enbridge bill)", foreground=GRAY).grid(
                row=0, column=2, sticky="w", padx=PAD)
        status = ttk.Label(row, wraplength=520)
        status.grid(row=1, column=2, sticky="w", padx=PAD)
        self.address_rows[page.page] = status
        self._set_address_status(page.page, decision)

    def _set_address_status(self, page_num, decision):
        page = self.session.by_page[page_num]
        label = self.address_rows[page_num]
        edited = " (edited)" if page.is_bill and page.address_edited else ""
        if decision.group == "enter":
            text, color = f"Will be entered on tab \"{page.tab}\"{edited}", GREEN
        elif decision.group == "already":
            text, color = f"Already in the sheet on tab \"{page.tab}\"{edited}", BLUE
        else:
            text, color = f"Won't be entered: {decision.reason}{edited}", GRAY if not edited else ORANGE
        label.configure(text=text, foreground=color)

    def _address_edited(self, page_num, var):
        page = self.session.by_page[page_num]
        if (var.get().strip() or None) == page.address:
            return
        self.session.set_address(page_num, var.get())
        decisions = self.session.decisions()
        for num in self.address_rows:  # an edit can change other rows (duplicates)
            self._set_address_status(num, decisions[num])
        self.addresses_checked_var.set(False)
        self._addresses_ticked()

    def _addresses_ticked(self):
        self.session.addresses_checked = bool(self.addresses_checked_var.get())
        self.continue_button.configure(state="normal" if self.session.addresses_checked else "disabled")
        self.on_return = self._addresses_done if self.session.addresses_checked else None

    def _addresses_done(self):
        if not self.session.addresses_checked or self.busy:
            return
        self.continue_button.configure(state="disabled")
        s = self.session

        def work():
            s.prepare_bills(self._progress)
            return s.bills()

        def done(bills):
            self.after_error = None
            if bills:
                self.show_bill(0)
            else:
                self.show_summary()
        self.after_error = self.show_addresses
        self._in_background(work, done)

    def _start_over(self):
        if messagebox.askyesno(APP_NAME, "Start over? Nothing has been written, and any fixes you made here will be lost."):
            self.show_start()

    # --- step 2: one bill at a time -----------------------------------------

    def _page_image(self, page_num):
        if page_num not in self.page_cache:
            from pdf_io import render_page
            self.page_cache = {page_num: render_page(self.session.pdf_path, page_num)}
        return self.page_cache[page_num]

    def show_bill(self, index):
        s = self.session
        bills = s.bills()
        if not bills:
            return self.show_summary()
        index = max(0, min(index, len(bills) - 1))
        page = bills[index]
        frame = self._clear()
        self.bill_index = index
        self.bill_page = page.page  # the page these widgets belong to

        title = ttk.Frame(frame)
        title.pack(fill="x")
        company = {"logan": "Logan City", "enbridge": "Enbridge"}[page.company]
        ttk.Label(title, text=f"Step 2 of 3 - Bill {index + 1} of {len(bills)}: {page.tab} ({company})",
                  font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(title, text=f"page {page.page} of the scan", foreground=GRAY).pack(side="left", padx=PAD)
        status = ("Confirmed" if page.page in s.confirmed else
                  "Skipped" if page.page in s.skipped else "Not checked yet")
        ttk.Label(title, text=status, foreground=GREEN if status == "Confirmed" else ORANGE).pack(side="right")

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True, pady=(PAD, 0))
        image = self._page_image(page.page)

        # Left: the page with every value boxed and numbered.
        self.root.update_idletasks()
        available_h = max(480, self.root.winfo_height() - 190)
        page_view = self._annotated_page(image, page, available_h)
        ttk.Label(body, image=self._photo(page_view), relief="solid", borderwidth=1).pack(side="left", anchor="n")

        # Right: one panel per value.
        right = Scrollable(body)
        right.pack(side="left", fill="both", expand=True, padx=(PAD * 2, 0))
        self.field_vars, self.tick_vars, self.field_msgs = {}, {}, {}
        import pipeline
        for number, name in enumerate(pipeline.FIELD_ORDER, start=1):
            self._field_panel(right.inner, page, number, name, image)

        self.problems_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.problems_var, foreground=RED, wraplength=1100).pack(anchor="w", pady=(PAD, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(PAD // 2, 0))
        ttk.Button(buttons, text="<  Back", command=self._bill_back).pack(side="left")
        ttk.Button(buttons, text="Skip this bill (don't enter it)", command=self._bill_skip).pack(side="left", padx=PAD)
        ttk.Button(buttons, text="Confirm and next  >", command=self._bill_confirm).pack(side="right")
        ttk.Label(buttons, foreground=GRAY, text="Tab moves between boxes, Space ticks, Enter confirms").pack(side="right", padx=PAD)
        self.on_return = self._bill_confirm
        self._update_problems()

    def _annotated_page(self, image, page, max_h):
        if image is None:
            placeholder = Image.new("RGB", (int(max_h * 2550 / 3300), max_h), "white")
            ImageDraw.Draw(placeholder).text((20, 20), "This page couldn't be shown", fill="black")
            return placeholder
        page_img = image.convert("RGB")
        draw = ImageDraw.Draw(page_img)
        font = _font(46)
        amount = page.fields["amount"]
        def around(box, pad):
            # Frame the text instead of drawing over it.
            return (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)

        for label, box in amount.check_boxes.items():
            if box and label != "Current Charges":
                draw.rectangle(around(box, 10), outline=CHECK_COLOR, width=4)
        import pipeline
        for number, name in enumerate(pipeline.FIELD_ORDER, start=1):
            f = page.fields[name]
            if not f.box:
                continue
            color = FIELD_COLORS[name]
            draw.rectangle(around(f.box, 16 if f.found else 4), outline=color, width=7 if f.found else 4)
            x, y = f.box[0] - 86, max(0, f.box[1] - 16)
            x = max(0, x)
            draw.ellipse((x, y, x + 62, y + 62), fill=color)
            draw.text((x + 31, y + 31), str(number), fill="white", font=font, anchor="mm")
        return fit(page_img, int(max_h * 2550 / 3300), max_h)

    def _field_panel(self, parent, page, number, name, image):
        import pipeline
        s = self.session
        f = page.fields[name]
        color = FIELD_COLORS[name]
        panel = tk.Frame(parent, highlightthickness=3, highlightbackground=color, padx=PAD, pady=PAD // 2)
        panel.pack(fill="x", pady=(0, PAD))
        tk.Label(panel, text=f" {number} ", bg=color, fg="white", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(panel, text=pipeline.FIELD_LABELS[name], font=("Segoe UI", 12, "bold")).grid(row=0, column=1, sticky="w", padx=PAD)

        if image is not None and f.box:
            close_up = fit(image.crop(_padded(f.box, image, 20)), 560, 110)
            tk.Label(panel, image=self._photo(close_up), relief="solid", borderwidth=1).grid(
                row=1, column=0, columnspan=3, sticky="w", pady=(PAD // 2, PAD // 2))

        var = tk.StringVar(value=_display(name, f.value))
        entry = ttk.Entry(panel, textvariable=var, width=30, font=("Segoe UI", 12))
        entry.grid(row=2, column=0, columnspan=2, sticky="w")
        entry.bind("<Return>", lambda e, p=page.page, n=name: self._commit_field(p, n) or "break")
        entry.bind("<FocusOut>", lambda e, p=page.page, n=name: self._commit_field(p, n))
        self.field_vars[name] = var

        tick = tk.BooleanVar(value=name in s.ticks.get(page.page, set()))
        ttk.Checkbutton(panel, text="Looks right", variable=tick,
                        command=lambda p=page.page, n=name: self._ticked(p, n)).grid(row=2, column=2, sticky="w", padx=PAD)
        self.tick_vars[name] = tick

        message, message_color = _field_status(f)
        msg = tk.Label(panel, text=message, fg=message_color, wraplength=560, justify="left")
        msg.grid(row=3, column=0, columnspan=3, sticky="w")
        self.field_msgs[name] = msg

    def _current_bill(self):
        return self.session.by_page[self.bill_page]

    def _commit_field(self, page_num, name):
        """Save what's typed in one value box. Ignored if the box belongs to
        a bill that's no longer on screen (a late focus-out while moving to
        the next bill must never write into the new bill)."""
        if page_num != getattr(self, "bill_page", None) or name not in self.field_vars:
            return None
        try:
            text = self.field_vars[name].get()
        except tk.TclError:
            return None
        page = self.session.by_page[page_num]
        error = self.session.set_field(page_num, name, text)
        f = page.fields[name]
        try:
            if error:
                self.field_msgs[name].configure(text=error, fg=RED)
            else:
                message, color = _field_status(f)
                self.field_msgs[name].configure(text=message, fg=color)
                self.field_vars[name].set(_display(name, f.value))
                self.tick_vars[name].set(name in self.session.ticks.get(page_num, set()))
            self._update_problems()
        except tk.TclError:
            pass
        return error

    def _ticked(self, page_num, name):
        if page_num != getattr(self, "bill_page", None):
            return
        self.session.tick(page_num, name, bool(self.tick_vars[name].get()))
        self._update_problems()

    def _commit_all(self):
        errors = [self._commit_field(self.bill_page, name) for name in list(self.field_vars)]
        return not any(errors)

    def _update_problems(self):
        problems = self.session.confirm_problems(self.bill_page)
        self.problems_var.set(("Before confirming: " + "; ".join(problems)) if problems else "")

    def _bill_confirm(self):
        if not self._commit_all():
            return
        page = self._current_bill()
        problems = self.session.confirm(page.page)
        if problems:
            self._update_problems()
            return
        self._next_bill()

    def _bill_skip(self):
        page = self._current_bill()
        if messagebox.askyesno(APP_NAME, f"Skip {page.tab} (page {page.page})? It won't be entered - "
                                         "you'd need to enter it by hand if it should go in."):
            self.session.skip(page.page)
            self._next_bill()

    def _next_bill(self):
        bills = self.session.bills()
        index = next((i for i, b in enumerate(bills) if b.page == self.bill_page), self.bill_index)
        for i in range(index + 1, len(bills)):
            if bills[i].page not in self.session.confirmed and bills[i].page not in self.session.skipped:
                return self.show_bill(i)
        if self.session.unresolved():
            first = self.session.bills().index(self.session.unresolved()[0])
            return self.show_bill(first)
        self.show_summary()

    def _bill_back(self):
        self._commit_all()
        bills = self.session.bills()
        index = next((i for i, b in enumerate(bills) if b.page == self.bill_page), 0)
        if index == 0:
            self.show_addresses()
        else:
            self.show_bill(index - 1)

    # --- step 3: summary + write ----------------------------------------------

    def show_summary(self):
        s = self.session
        summary, cells = s.summary()
        unresolved = s.unresolved()
        frame = self._clear()
        ttk.Label(frame, text="Step 3 of 3 - Write to the sheet", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        if unresolved:
            text = (f"{len(unresolved)} bill(s) still need Confirm or Skip: "
                    + ", ".join(f"{p.tab} (page {p.page})" for p in unresolved))
            ttk.Label(frame, text=text, foreground=RED, wraplength=1100).pack(anchor="w", pady=(PAD // 2, PAD))
        else:
            ttk.Label(frame, wraplength=1100, text=(
                f"This is exactly what will happen. {len(summary.entered)} bill(s) will be written to the sheet "
                "and stamped ENTERED. Nothing else in the sheet is changed.")).pack(anchor="w", pady=(PAD // 2, PAD))
        box = scrolledtext.ScrolledText(frame, wrap="word", font=("Consolas", 10))
        box.pack(fill="both", expand=True)
        summary.dry_run = True  # word it as "would be entered" until it's written
        box.insert("end", summary.report())
        box.configure(state="disabled")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(PAD, 0))
        ttk.Button(buttons, text="<  Back to the bills",
                   command=lambda: self.show_bill(len(s.bills()) - 1) if s.bills() else self.show_addresses()).pack(side="left")
        write = ttk.Button(buttons, text=f"Write {len(summary.entered)} bill(s) to the sheet", command=self._write)
        write.pack(side="right")
        if unresolved:
            write.configure(state="disabled")
            first = s.bills().index(unresolved[0])
            ttk.Button(buttons, text="Go to the first unchecked bill",
                       command=lambda: self.show_bill(first)).pack(side="right", padx=PAD)
        self.progress_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.progress_var, foreground=GRAY).pack(anchor="w")
        self.progress_bar = ttk.Progressbar(frame)
        self.write_button = write

    def _write(self):
        if self.busy or self.session.unresolved():
            return
        self.write_button.configure(state="disabled")
        self.after_error = self.show_summary
        self._in_background(lambda: self.session.write(self._progress), self.show_done)

    # --- done -----------------------------------------------------------------

    def show_done(self, summary):
        self.after_error = None
        self.last_summary = summary
        frame = self._clear()
        ttk.Label(frame, text=f"Done - {len(summary.entered)} bill(s) entered", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, wraplength=1100, text=(
            "Next: do the split in Propertyware for each bill that was entered, then email the stamped PDF "
            "to James.")).pack(anchor="w", pady=(PAD // 2, PAD))
        box = scrolledtext.ScrolledText(frame, wrap="word", font=("Consolas", 10))
        box.pack(fill="both", expand=True)
        box.insert("end", summary.report())
        box.configure(state="disabled")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(PAD, 0))
        ttk.Button(buttons, text="Open stamped PDF", state="normal" if summary.output_pdf else "disabled",
                   command=lambda: open_path(summary.output_pdf)).pack(side="left")
        ttk.Button(buttons, text="Open folder", command=lambda: open_path(summary.pdf_path.parent)).pack(side="left", padx=PAD)
        ttk.Button(buttons, text="Open sheet", command=lambda: webbrowser.open(self.settings["sheet_link"])).pack(side="left")
        ttk.Button(buttons, text="Do another PDF", command=self.show_start).pack(side="right")


def _padded(box, image, pad):
    left, top, right, bottom = box
    return (max(0, left - pad), max(0, top - pad), min(image.width, right + pad), min(image.height, bottom + pad))


def _display(name, value):
    if value is None:
        return ""
    if name == "amount" and isinstance(value, (int, float)):
        return f"{value:.2f}"
    return str(value)


def _field_status(f):
    """The line under each value telling the reviewer how much to trust it."""
    if f.edited:
        if f.read_value is None:
            return "Typed in by you (the app couldn't read it).", BLUE
        return f"Changed by you - the app read {f.read_value}.", BLUE
    if f.value is None:
        why = f" - {f.note}" if f.note else ""
        return (f"Not found{why}. The box on the page shows where the app looked. Type it from the bill, "
                "or skip this bill."), RED
    if f.check:
        return f"Checked against the bill's own math: {f.check}", GREEN
    return "Compare with the picture above.", "#000000"


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
