"""
The window the office uses. Double-click the .exe and this is what opens:

    1. Google account   - sign in once per computer
    2. Billing sheet    - pre-filled with the shared sheet
    3. Scanned bills    - pick the PDF from the scanner
    [Preview]  [Enter bills into sheet]

The work runs in a background thread so the window stays responsive; it
sends messages back through a queue that the window checks every 100 ms.
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

import settings
from app_paths import APP_NAME, configure_tesseract, user_dir

PAD = 8


def open_path(path):
    """Open a file or folder with whatever Windows/macOS would use."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)


class App:
    def __init__(self, root):
        self.root = root
        self.settings = settings.load()
        self.creds = None
        self.busy = False
        self.messages = queue.Queue()
        self.last_summary = None

        root.title(APP_NAME)
        root.minsize(720, 560)
        self._build()
        root.after(100, self._poll)
        root.after(200, self._startup)

    # --- layout --------------------------------------------------------

    def _build(self):
        frame = ttk.Frame(self.root, padding=PAD * 2)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Utility bills → billing sheet", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, PAD * 2))

        # 1. Google account
        ttk.Label(frame, text="1. Google account").grid(row=1, column=0, sticky="w", pady=PAD)
        self.account_label = ttk.Label(frame, text="Checking...")
        self.account_label.grid(row=1, column=1, sticky="w", padx=PAD)
        self.account_button = ttk.Button(frame, text="Sign in with Google", command=self._sign_in_or_out)
        self.account_button.grid(row=1, column=2, sticky="ew")

        # 2. Sheet
        ttk.Label(frame, text="2. Billing sheet").grid(row=2, column=0, sticky="w", pady=PAD)
        self.sheet_var = tk.StringVar(value=self.settings["sheet_link"])
        ttk.Entry(frame, textvariable=self.sheet_var).grid(row=2, column=1, sticky="ew", padx=PAD)
        ttk.Button(frame, text="Open sheet", command=lambda: webbrowser.open(self.sheet_var.get())).grid(
            row=2, column=2, sticky="ew")

        # 3. PDF
        ttk.Label(frame, text="3. Scanned bills (PDF)").grid(row=3, column=0, sticky="w", pady=PAD)
        self.pdf_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pdf_var).grid(row=3, column=1, sticky="ew", padx=PAD)
        ttk.Button(frame, text="Browse...", command=self._browse).grid(row=3, column=2, sticky="ew")

        # Actions
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(PAD * 2, PAD))
        self.preview_button = ttk.Button(actions, text="Preview (changes nothing)",
                                         command=lambda: self._start_run(dry_run=True))
        self.preview_button.pack(side="left")
        self.run_button = ttk.Button(actions, text="Enter bills into sheet",
                                     command=lambda: self._start_run(dry_run=False))
        self.run_button.pack(side="left", padx=PAD)

        self.open_pdf_button = ttk.Button(actions, text="Open stamped PDF", state="disabled",
                                          command=lambda: open_path(self.last_summary.output_pdf))
        self.open_pdf_button.pack(side="right")
        self.open_folder_button = ttk.Button(actions, text="Open folder", state="disabled",
                                             command=lambda: open_path(self.last_summary.pdf_path.parent))
        self.open_folder_button.pack(side="right", padx=PAD)

        self.status_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.status_var).grid(row=5, column=0, columnspan=3, sticky="w")
        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(PAD // 2, PAD))

        self.output = scrolledtext.ScrolledText(frame, height=18, wrap="word", font=("Consolas", 10))
        self.output.grid(row=7, column=0, columnspan=3, sticky="nsew")
        self.output.configure(state="disabled")
        frame.rowconfigure(7, weight=1)

        ttk.Label(frame, foreground="gray",
                  text="Nothing already in the sheet is ever overwritten. Run logs: "
                       f"{user_dir() / 'logs'}").grid(row=8, column=0, columnspan=3, sticky="w", pady=(PAD, 0))

    # --- helpers -------------------------------------------------------

    def _show(self, text):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("end", text)
        self.output.configure(state="disabled")

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.preview_button, self.run_button, self.account_button):
            button.configure(state=state)

    def _in_background(self, work):
        """Run work() off the main thread; it reports back via self.messages."""
        def wrapper():
            try:
                work()
            except Exception as e:
                self.messages.put(("error", e, traceback.format_exc()))
        threading.Thread(target=wrapper, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, *payload = self.messages.get_nowait()
                getattr(self, f"_on_{kind}")(*payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    # --- startup / sign-in ----------------------------------------------

    def _startup(self):
        try:
            configure_tesseract()
        except RuntimeError as e:
            messagebox.showerror(APP_NAME, str(e))
        self._set_busy(True)

        def work():
            import google_sheets
            creds = google_sheets.load_saved_credentials()
            email = google_sheets.signed_in_email(creds) if creds else None
            self.messages.put(("signed_in", creds, email))
        self._in_background(work)

    def _sign_in_or_out(self):
        if self.creds:
            import google_sheets
            google_sheets.sign_out()
            self._on_signed_in(None, None)
            return
        self._set_busy(True)
        self.status_var.set("A browser window opened - sign in with your Catalyst Google account there.")

        def work():
            import google_sheets
            creds = google_sheets.sign_in()
            self.messages.put(("signed_in", creds, google_sheets.signed_in_email(creds)))
        self._in_background(work)

    def _on_signed_in(self, creds, email):
        self.creds = creds
        self._set_busy(False)
        self.status_var.set("")
        if creds:
            self.account_label.configure(text=f"Signed in as {email or 'your Google account'}")
            self.account_button.configure(text="Sign out")
        else:
            self.account_label.configure(text="Not signed in")
            self.account_button.configure(text="Sign in with Google")

    # --- running ---------------------------------------------------------

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

    def _start_run(self, dry_run):
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

        self._set_busy(True)
        self.open_pdf_button.configure(state="disabled")
        self.open_folder_button.configure(state="disabled")
        self._show("Previewing - nothing will be written.\n" if dry_run else "Working...\n")
        self.progress.configure(value=0)
        creds = self.creds

        def work():
            import pipeline
            from pdf_io import page_count
            total = page_count(pdf)
            self.messages.put(("progress", "Starting...", 0, total))

            def progress(message):
                page = 0
                if message.startswith("Reading page "):
                    page = int(message.split()[2])
                self.messages.put(("progress", message, page, total))

            sheet = google_sheets.Sheet(creds, sheet_id)
            summary = pipeline.run(pdf, sheet, dry_run=dry_run, progress=progress)
            self.messages.put(("done", summary))
        self._in_background(work)

    def _on_progress(self, message, page, total):
        self.status_var.set(message)
        if total:
            self.progress.configure(maximum=total, value=page or self.progress["value"])

    def _on_done(self, summary):
        self._set_busy(False)
        self.last_summary = summary
        self.progress.configure(value=self.progress["maximum"])
        self._show(summary.report())
        if summary.dry_run:
            self.status_var.set(f"Preview done: {len(summary.entered)} bill(s) would be entered. "
                                "Nothing was changed.")
        else:
            self.status_var.set(f"Done: {len(summary.entered)} bill(s) entered, "
                                f"{len(summary.not_entered)} page(s) not entered.")
            self.open_folder_button.configure(state="normal")
            if summary.output_pdf:
                self.open_pdf_button.configure(state="normal")

    def _on_error(self, error, details):
        import google_sheets
        self._set_busy(False)
        self.status_var.set("")
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
        self._show(message)
        messagebox.showerror(APP_NAME, message)


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
