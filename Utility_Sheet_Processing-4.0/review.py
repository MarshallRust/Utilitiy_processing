"""
The review between reading and writing - the rules behind the review
screens, kept separate from the window so they can be tested on their own.

Step 1, addresses: every page's service address is shown next to the
picture it was read from, grouped into "will be entered", "already in the
sheet" and "won't be entered". The reviewer can fix an address; it's
matched to the tabs again with the same exact-match rule. They have to tick
"I've checked every address" to move on.

Step 2, bills: each bill that will be entered is shown with its four values
next to where they came from on the page. Each value has a "looks right"
tick. The amount starts ticked when the bill's own arithmetic confirmed it;
everything else has to be ticked by the reviewer (editing a value ticks it).
A bill can only be confirmed when all four are ticked and the values pass
the same checks as always. Or the reviewer can skip it.

Step 3: a summary of exactly what will be written, then write.

Every change a reviewer makes is logged in the run's report.
"""

from pipeline import (FIELD_LABELS, FIELD_ORDER, build_summary, commit, decide, match_tab,
                      parse_amount, parse_service_dates, read_missing_fields, value_problems)
import sheet_layout


class ReviewSession:
    def __init__(self, pdf_path, sheet, sheet_title, tab_names, pages, grids):
        self.pdf_path = pdf_path
        self.sheet = sheet
        self.sheet_title = sheet_title
        self.tab_names = tab_names
        self.pages = pages
        self.by_page = {p.page: p for p in pages}
        self.grids = dict(grids)
        self.addresses_checked = False
        self.ticks = {}          # page -> set of field names ticked "looks right"
        self.confirmed = set()   # pages confirmed in step 2
        self.skipped = {}        # page -> reason the reviewer skipped it

    # --- step 1: addresses ----------------------------------------------

    def decisions(self):
        return decide(self.pages, self.grids)

    def groups(self):
        """{'enter': [...], 'already': [...], 'skip': [...]} of (page, decision)."""
        out = {"enter": [], "already": [], "skip": []}
        for page_num, d in sorted(self.decisions().items()):
            out[d.group].append((self.by_page[page_num], d))
        return out

    def set_address(self, page_num, text):
        """A reviewer fixed an address: match it to the tabs again."""
        page = self.by_page[page_num]
        if not page.is_bill:
            return
        page.address = text.strip() or None
        match_tab(page, self.tab_names)
        self.addresses_checked = False  # something changed - look again

    def prepare_bills(self, progress=print):
        """Between step 1 and step 2: read the values for bills that only
        match a tab because of an address fix, and load any tabs not loaded
        yet. Needs the network (the sheet)."""
        read_missing_fields(self.pdf_path, self.pages, progress)
        missing_tabs = {p.tab for p in self.pages if p.tab and p.tab not in self.grids}
        if missing_tabs:
            progress("Checking the sheet...")
            self.grids.update(self.sheet.read_tabs(missing_tabs))
        for page, _ in self.groups()["enter"]:
            if page.page not in self.ticks:
                self.ticks[page.page] = {"amount"} if page.fields and page.fields["amount"].verified else set()

    # --- step 2: bills --------------------------------------------------

    def bills(self):
        """The bills to review, in page order."""
        return [p for p, _ in self.groups()["enter"]]

    def set_field(self, page_num, name, text):
        """A reviewer typed a value. Returns None if it was accepted, or a
        message saying what's wrong with it (the value isn't changed)."""
        page = self.by_page[page_num]
        f = page.fields[name]
        text = (text or "").strip()
        if not text:
            value = None
        elif name == "amount":
            value = parse_amount(text)
            if value is None:
                return "Type a dollar amount, like 58.59"
        elif name == "effective_dates":
            value = parse_service_dates(text)
            if value is None:
                return "Type it like 9/18/2026 - 10/19/2026"
        else:
            if sheet_layout.parse_date(text) is None:
                return "Type a date like 10/20/2026"
            value = text
        if value == f.value:
            return None
        f.value = value
        f.edited = value != f.read_value
        self.ticks.setdefault(page_num, set()).add(name)
        self.confirmed.discard(page_num)
        return None

    def tick(self, page_num, name, on=True):
        ticks = self.ticks.setdefault(page_num, set())
        ticks.add(name) if on else ticks.discard(name)
        if not on:
            self.confirmed.discard(page_num)

    def confirm_problems(self, page_num):
        """What's stopping this bill from being confirmed (empty = nothing)."""
        page = self.by_page[page_num]
        problems = value_problems(page)
        unticked = [FIELD_LABELS[n] for n in FIELD_ORDER if n not in self.ticks.get(page_num, set())]
        if unticked:
            problems.append("tick \"looks right\" for: " + ", ".join(unticked))
        d = self.decisions()[page_num]
        if d.group != "enter" and not problems:
            problems.append(d.reason or "this bill is already in the sheet")
        return problems

    def confirm(self, page_num):
        problems = self.confirm_problems(page_num)
        if problems:
            return problems
        self.confirmed.add(page_num)
        self.skipped.pop(page_num, None)
        return []

    def skip(self, page_num, reason="skipped by the reviewer - enter by hand if it needs to go in"):
        self.confirmed.discard(page_num)
        page = self.by_page[page_num]
        self.skipped[page_num] = f"{page.tab}: {reason}"

    def unresolved(self):
        """Bills not yet confirmed or skipped."""
        return [p for p in self.bills() if p.page not in self.confirmed and p.page not in self.skipped]

    # --- step 3: summary + write ----------------------------------------

    def edits(self):
        lines = []
        for p in self.pages:
            if p.is_bill and p.address_edited:
                lines.append(f"page {p.page}: address changed from '{p.raw_address}' to '{p.address}'")
            for name in FIELD_ORDER:
                f = (p.fields or {}).get(name)
                if f and f.edited:
                    where = f"page {p.page} ({p.tab})"
                    if f.read_value in (None, ""):
                        lines.append(f"{where}: {FIELD_LABELS[name]} typed in by the reviewer: {f.value} "
                                     "(the app couldn't read it)")
                    else:
                        lines.append(f"{where}: {FIELD_LABELS[name]} changed from {f.read_value} to {f.value}")
        return lines

    def summary(self):
        return build_summary(self.pdf_path, self.sheet_title, self.pages, self.grids,
                             to_enter=set(self.confirmed), reviewed=True,
                             skipped_by_reviewer=self.skipped, edits=self.edits())

    def write(self, progress=print):
        summary, cells = self.summary()
        return commit(summary, cells, self.sheet, progress)
