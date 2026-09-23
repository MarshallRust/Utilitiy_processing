# Utility Processing

Every month there's a big stack of scanned utility bills (Enbridge and Logan City) that have to be entered into a spreadsheet by hand. Each tenant's address has its own tab, and for every bill you type in the bill date, amount, service period, and due date under the right month. This reads the scanned PDF, OCRs each page, figures out which tenant and month it belongs to, and fills in the spreadsheet. It also stamps "Entered" on every page it handled so I can see what's done.

There are four versions in here because I kept rewriting it. V3 is the one that works. V4 is the rewrite I'm working on now to fix V3's problems. V1 and V2 are only here for history.

Real bills and the billing spreadsheet aren't in the repo (they're gitignored) since they have tenant info in them.

## Setup

```
pip install pytesseract pymupdf pdf2image pillow openpyxl pandas numpy opencv-python pyyaml
brew install tesseract
```

V1 and V2 also need poppler. V3 only uses poppler on Windows. Put the PDF in `PDFs/` and the spreadsheet in the same folder as the script, then run `python Main.py` (V3) or `python main.py` (V4). The file names are hardcoded right now (see the problems list at the bottom).

---

## V3 (`3Utility_sheet_processing_V3/`)

Every field (address, bill date, amount, etc.) is its own class that inherits from `SheetData`. `SheetData` does the OCR and the retrying, and each subclass says where on the page to look (`CORDS`), what label to search for (`SEARCH_TERM`), and how to pull the value out of the line it finds. Anything that's a list of two is `[Enbridge, Logan]`, and the company gets passed around as an index: 0 is Enbridge, 1 is Logan, 2 is unknown.

### Main.py

The run itself.

- `main()` - loads the PDF, sets up Tesseract, processes every page, prints what it found, and saves the stamped PDF
- `load_pdf_images(pdf_path)` - turns each page into an image at 300 DPI. On Windows it tries pdf2image with my poppler path first. Everywhere else, or if that fails, it uses PyMuPDF.
- `configure_tesseract()` - points pytesseract at the Tesseract exe on Windows. On Mac it's already on the PATH.
- `get_utility_company(image)` - averages the color of the whole page and checks it against a range for each company. Enbridge pages come out lighter gray than Logan's. It works but it's fragile. A darker scan can throw it off.
- `process_images(images)` - for each page: figure out the company, read the address, and if the address matches a tenant tab, read the rest of the fields and write them to Excel. Returns everything it found and the list of images (stamped if they got entered).
- `process_just_service_address(image, company)` - reads only the address first, so if a page isn't one of our tenants it doesn't waste time on the other four fields
- `process_with_everything(image, company)` - makes the four field objects and runs each one
- `create_objects(image, company)` - `[BillDate, Amount, EffectiveDates, DueDate]`, in the order the spreadsheet row offsets expect
- `add_to_excel_sheet(page, image)` - opens the workbook, goes to the tenant's tab, finds the "Enbridge" or "Logan City" row, finds the column for the bill's month, writes the four values, saves, and returns the stamped image. If anything along the way doesn't match it just returns the page without the stamp.
- `parse_month(page)` - gets the month from the bill date. `10/13/2025` -> `Oct`
- `open_sheet(wb, tab_name)` - returns the tab or None
- `find_starting_cell(ws, targets)` - finds the company label in column A
- `find_month_column(ws, header_row, month_name)` - finds the column with that month in the header row
- `write_values(ws, values, target_rows, col)` - writes each value into its row
- `add_stamp(image)` - pastes `Pictures/Entered_no_bg.png` onto the page
- `make_and_save_pdf(images)` - saves every page, stamped or not, into `PDFs/processed_pdfs/utilities_<date>.pdf`

### Sheet_Data_Class.py

`SheetData` is the base class all the fields use.

- `__init__(image, company)` - reads the crop box for this company from `CORDS`. It throws right away if a subclass forgot to define `CORDS`.
- `find_data_in_image()` - the main loop. Crop, OCR, and look for the search term. If it's not found, make the crop bigger (25px, then 50 more, then 75 more...), loosen the OCR settings, and try again, up to 5 times. Real scans are never lined up exactly the same, so the first crop misses a lot.
- `_crop_image_with_offset(size_offset)` - the crop box grown by the offset on every side, without going past the edge of the page
- `_get_ocr_data(image)` - runs Tesseract and gets word-level data back, which is each word with the line it's on
- `_increase_offset()` / `_retry_or_fail()` - how much to grow the crop each time, and when to give up
- `_search_lines_for_term(data)` - puts the OCR words back together into lines and checks each line for the search term. It uses whole-word matching because a plain substring check let "Date" match inside "BillDate". When it finds the term it hands the line to `extract_data_from_line`.
- `_generate_search_terms(term)` - variations of the term in case OCR drops a word or a space. "Service Address" also tries "ServiceAddress", "Address", and "Service".
- `_assemble_line_text(data, line_num, num_words)` - joins the words on one OCR line
- `get_custom_config()` - the Tesseract settings for each attempt. The first tries only allow letters, digits, `$` and `.`, then it adds `-` and `,`, then spaces, and after that it turns off the whitelist completely and switches to sparse text mode (`--psm 11`). Logan's labels are white text on a colored background, so for Logan it turns on invert.
- `_preprocess(img)` - contrast, threshold, noise removal, and line removal. It's turned off because it wasn't helping.
- `extract_data_from_line(line_text)` - each subclass has its own

### Service_address_class.py

- `ServiceAddress.extract_data_from_line(line_text)` - starts at the first digit in the line and reads until a comma or period (`230 W 100 N, Logan...` -> `230 W 100 N`). Then it checks that against the tab names in the spreadsheet. If it matches, the value is the real tab name. If it doesn't, it's not a tenant we track.
- `ServiceAddress.get_custom_config()` - one fixed config instead of the retry ramp, with digits and address punctuation allowed
- `get_excel_tabs()` - reads the tab names from the workbook once and caches them
- `is_in_list_of_tabs(result)` - checks if any tab name (without spaces or punctuation) shows up in the OCR'd address
- `remove_spaces_and_punctuation(s)` - lowercase and letters/digits only, so `230 W 100 N` and `230w100n` match

### Amount_class.py

- `extract_data_from_line(line_text)` - calls whichever parser fits the company, then converts to a float
- `extract_after_dollar(line_text)` - Enbridge: the digits right after the last `$` on the line, up to 7 characters
- `_extract_after_search_term(line_text)` - Logan: the digits after "Total Amount Due"
- `_clean_and_convert(raw_string)` - takes out commas and makes it a float. If that fails it returns 0.0, which is bad (see problems).

### Dates_Class.py, Bill_date_class.py, Due_date_class.py, Effective_dates_class.py

`Dates` handles the three date fields. Each subclass only sets `DATE_PATTERN`, `CORDS`, and `SEARCH_TERM`.

- `Dates.extract_data_from_line(line_text)` - cuts off everything before the search term, then regex matches the date. If it's supposed to be a single date it checks it's a real one with `strptime`, so an OCR misread like `13/45/2025` gets rejected and retried instead of written to the sheet.
- `Dates.get_custom_config()` - Enbridge uses the normal retry ramp. Logan uses one fixed config that allows `/`, since the normal whitelist doesn't have `/` and a date could never match.
- `BillDate` - "As of" (Enbridge) / "Bill Date" (Logan)
- `DueDate` - "or after" (Enbridge) / "Due Date" (Logan). On Enbridge bills the "Past Due After" label and the date end up on different OCR lines, but "or after" is in another sentence on the same line as the date.
- `EffectiveDates` - "Service from" / "Service Period". This is a date range, so it skips the single-date check.

---

## V4 (`Utility_Sheet_Processing-4.0/`)

The rewrite. `Utility_Sheet_Processing-4.0/README.md` has my notes from going through V3 and everything I want to fix. The big changes so far:

- The crop boxes, search terms, and OCR settings are in `fields.yaml` instead of hardcoded in a class for each field
- The spreadsheet gets opened and saved once per run instead of once per page. That was the slowest part of V3 by far.
- Pages it couldn't handle go into their own "unprocessed" PDF so I know exactly what needs to be done by hand
- PDF loading always uses PyMuPDF now, so there's no poppler to install
- Each file does one thing: PDFs, OCR, Excel, classifying

### main.py

- `PageResult` - dataclass for one page: company, address (the tab name), bill_date, amount, effective_dates, due_date
- `process_page(image)` - classify the page, read the address, match it to a tab, then read every other field in `fields.yaml`. Returns None if it's an unknown company, the address can't be read, or the address isn't a tenant.
- `main()` - loads the PDF and runs `process_page` on each page. It stamps the good pages, writes all the results to Excel at once, and saves two PDFs, `processed_<date>.pdf` and `unprocessed_<date>.pdf`. Then it prints how many of each.

### pdf_io.py

- `load_pdf_images(pdf_path, dpi=300)` - every page to an image with PyMuPDF
- `add_stamp(image, stamp_path, position)` - pastes the stamp where you tell it to
- `make_and_save_pdf(images, name_prefix, output_dir)` - saves the images as one PDF named `<prefix>_<date>.pdf`. If the list is empty it skips saving. No unprocessed pages is a good thing, not an error.

### classify.py

- `classify(image)` - same color-average check as V3, but it returns `"enbridge"`, `"logan"`, or None instead of 0/1/2
- `_in_range(avg, low, high)` - checks each RGB channel is in range

### ocr.py

Loads `fields.yaml` into `FIELDS` when it's imported.

- `extract_field(image, field_name, company)` - same crop, OCR, and grow-and-retry loop as V3, but everything comes from the field's settings in the yaml. Returns the value, or None after 5 tries.
- `get_field_config(field_name, company)` - that field's settings for that company
- `_tesseract_config(attempt, cfg)` - the same loosen-up-each-try settings as V3, unless the field has its own `whitelist` in the yaml, in which case it uses that every time (the date fields need `/`). The base whitelist includes a space on purpose. Without it Tesseract can't put spaces between words, so "Service Address" comes out as "ServiceAddress" and never matches. That was the whole reason the first crop kept failing.
- `_find_matching_line(ocr_data, term)` - rebuilds the lines and returns the first one that has the term as a whole word
- `_extract_value(field_name, line_text, company)` - uses the field's custom parser if it has one, otherwise the regex `pattern` from the yaml, then the validator if there is one
- `validate_date(candidate)` - true if it's a real `MM/DD/YYYY` date
- `parse_amount(line_text, company)` - picks the right company's amount parser
- `_parse_amount_enbridge(...)` - digits after the first `$`
- `_parse_amount_logan(...)` - digits after "total"
- `_to_float(digits)` - float, or None if it can't. Not 0.0 like V3, so a bad read doesn't get written as a real amount.
- `parse_address(line_text, company)` - same first-digit-to-comma logic as V3. It doesn't check the tabs anymore, `excel_writer` does that.

### excel_writer.py

- `resolve_tab_name(address)` - the real tab name the OCR'd address matches, or None
- `_load_tabs()` - reads the tab names once and caches them
- `_clean(s)` - lowercase and only letters and digits
- `write_all_to_excel(results)` - opens the workbook once and, for each result, finds the tab, the company row, and the month column, then writes bill date, due date, service period, and amount at their row offsets. Saves once at the end. Anything that doesn't fit gets skipped with a reason printed out instead of crashing the whole run.
- `_parse_month(bill_date)` - `10/13/2025` -> `Oct`
- `_find_company_row(ws, label)` - finds "Enbridge" or "Logan City" in column A
- `_find_month_column(ws, header_row, month_name)` - finds the month in the header row
- `ROW_OFFSETS` - where each field goes relative to the company row. This replaces V3's `[2, 6, 5, 4]` that had no explanation of what each number was.

### fields.yaml

One entry per field. Each has a `pattern` (regex) or a `parser` (custom function in `ocr.py`), an optional `validate`, and then settings for `enbridge` and `logan`: the crop `box`, the search `term`, `invert`, and optionally `psm`, `whitelist`, and `extra_config`.

### debug_*.py

Scripts I wrote while getting V4 to actually read pages. They don't write anything.
- `debug_pages.py` - prints what classify, extract_field, and resolve_tab_name returned for the first few pages
- `debug_crop.py` - saves the address crop and the full page as images so I can see if the box is in the right spot
- `debug_trace.py` - walks through the retry loop for one page by hand and prints what each attempt saw
- `debug_full.py` - runs `process_page` on every page and prints every field

---

## V1 and V2

- **V1** (`process(V_1).py`) - my first try. One script with hardcoded crop boxes that OCRs a few spots and prints what it found.
- **V2** (`Utility_sheet_processing(V_2).py`, `Page_object(V_2).py`) - added search terms so it looked for a label instead of trusting the crop box, and a `Page` object to hold the fields. It also read the account number and page number, which I dropped later.

## Known problems (V3, and some are still in V4)

- The PDF and spreadsheet names are hardcoded, so I edit the code every month
- Company detection is by page color, which breaks on scans that are darker or lighter than usual
- Crop boxes are exact pixel positions for one scan size. If the scan settings change, they all break without any error.
- V3 writes $0.00 when it can't read an amount
- No check for a bill being processed twice in one run. The second one just overwrites the first.
- Nothing handles the spreadsheet being open in Excel while it runs
- Bills that are more than one page get treated as separate pages

The full list is in the V4 README.
