Current plan for 4.0

Notes from going through V3 before starting the rewrite. Not all of these are bugs - some are just things that made V3 slow, fragile, or annoying to run, and are worth designing around from the start this time instead of bolting on fixes later.

## Cross-device / portability

- Poppler and Tesseract paths are hardcoded Windows paths (`C:\poppler\...`, `C:\Program Files\Tesseract-OCR\...`). Only works on one machine as-is. Should detect the OS and/or read the paths from a small config file or environment variable instead of being baked into the code.
- The spreadsheet and PDF filenames are hardcoded literal strings (`"Utilities Billed to Tenants - Sept25.xlsx"`, `"PDFs/Utilis 10-27-2025.pdf"`), so the source has to be hand-edited every month. Should read these from a config file or just pick them up automatically (most recent file matching a pattern, or a command-line argument).
- No requirements.txt anywhere, so setting this up on a new machine means guessing what to `pip install`.
- Assumes it's being run from inside the project folder (relative paths everywhere). Fine for now but worth being deliberate about in V4.

## Speed

- Biggest one: `add_to_excel_sheet` does a full `load_workbook` + `save` of the entire spreadsheet on every single page. For a 100+ page PDF that's 100+ full read/writes of the workbook. Should build up all the rows in memory and save once at the end.
- Each page's 4-5 fields get OCR'd one after another, and each one can retry up to 5-6 times with a growing crop. All of that is single-threaded and serial. Pages are independent of each other, so this is a good candidate for running multiple pages in parallel (multiprocessing) rather than one at a time.
- 300 DPI image conversion for every page of a large PDF is heavy. Worth checking if a lower DPI is good enough for OCR accuracy before defaulting to the highest setting.

## Actually reading everything correctly

- All the crop boxes are hardcoded pixel coordinates tied to one exact scan resolution/layout. Any change in scan DPI, page size, or a slightly different bill template from the same company silently breaks every box - and it fails silently, not with an error. V4 should avoid hardcoded absolute coordinates where possible - e.g., find a reliable anchor point on the page (a logo, a fixed header) and crop relative to that, or search the page text first and crop near the match instead of trusting a fixed box.
- The utility company is currently identified by averaging the color of the entire page and checking if it falls in a narrow RGB range. This is fragile - scan brightness and contrast vary and will misclassify pages. A text-based check (cheap OCR pass looking for the company name/logo) would be far more reliable.
- Matching a field's label to its value assumes they land on the same line of OCR text. Real bills don't always cooperate - found at least one bill where a due-date label and its value were on separate rows of a table, and another where OCR read the label after the value instead of before it, which broke the current "cut everything before the label" logic. V4's matching logic should be more tolerant of layout order instead of assuming one strict format.
- Anything that isn't recognized as one of the known companies gets silently skipped with just an address print - there's no page in, page out accounting so you can't easily tell what was skipped vs. what succeeded without reading the whole console output.
- The row layout in the spreadsheet is tied to a list of magic numbers (`[2, 6, 5, 4]`) with no comment or safety check connecting them to which field is which. If the spreadsheet template ever changes, or someone reorders the fields in code, data silently lands in the wrong cells.

## Handling failures gracefully

- This was the biggest one I found and already patched in V3: failed OCR used to just write `$0.00` or a garbage date straight into your real billing spreadsheet with zero indication anything went wrong. Fixed for dates by validating the result is a real calendar date before accepting it, but the same principle should be designed in from day one in V4, for every field, not bolted on after the fact.
- No logging to a file. Right now the only record of a run is whatever printed to the terminal - if you close the window, it's gone. A log file (per page: what was found for each field, pass/fail) would make it much easier to check a run after the fact.
- No summary at the end telling you which addresses/pages need a manual look. Right now you have to scroll the console and compare it to the spreadsheet yourself.
- No dry-run mode. The only way to check results right now is to run the whole thing against your real spreadsheet. A "process but don't save" mode would let you sanity-check a batch first.
- A debug leftover (`ocr_image.show()`) pops open an image viewer on failure, which is fine sitting at the computer but would hang a run you kicked off and walked away from, or a scheduled run.

## File/repo housekeeping

- `Utilities Billed to Tenants - CURRENT.xlsx` in the V3 folder is empty (46 bytes) - looks like an abandoned attempt at solving the "don't hardcode the filename" problem.
- No `.gitignore` for `__pycache__`, generated PDFs, or the big input PDFs. The git history already has several 10-20MB+ blobs in it from this.

## Also worth deciding on for V4

- Tests. There's nothing that checks "does this still extract the right values" other than running it against a real PDF and eyeballing the output.
- Whether OCR is even the right tool going forward, or if some of these utility companies offer a CSV/API export that would sidestep OCR reliability problems entirely for at least those accounts.

## More problems found on a second, closer pass

Data integrity:

- `Amount` still has no validity check - we fixed the date fields to reject impossible values, but never went back and did the same for `Amount`. A misread digit still writes straight into the spreadsheet with nothing to catch it.
- `Amount._clean_and_convert` still silently defaults to `0.0` on a failed parse - this is the exact "silent wrong data" problem from the very first pass, still live for Amount specifically.
- If the same bill gets processed twice in one run (which happened in testing - "61 W 500 N" showed up twice with two different amounts), the second write silently overwrites the first with no warning a duplicate even happened.
- `write_values` never checks whether the target cell already has something in it before overwriting - no protection against a re-run clobbering a manual correction made by hand later.
- `find_starting_cell` needs an exact string match ("Enbridge" / "Logan City") in column 1 of the sheet - a stray space or slightly different label fails silently.

Error handling gaps:

- `parse_month` can raise on a garbled month read, but it's caught by a bare `except:` in `add_to_excel_sheet` that also swallows any other real bug in that block - a code error and "bad OCR" look identical from the output.
- Nothing handles the spreadsheet being open in Excel when the script runs - that's normally a file-lock error and would crash mid-batch.
- Nothing handles a corrupted/unreadable PDF page - one bad page kills the whole run.
- No record of which pages already got processed if the script crashes partway through a big batch - a rerun starts over from page 1 and risks the duplicate-overwrite problem above.

Other:

- `add_stamp`'s stamp position is another hardcoded-pixel-coordinate spot, same issue as the crop boxes.
- Multi-page bills ("page 1 of 2") aren't handled - each page runs through the same single-page extraction logic independently. V2 had a TODO about this that never got picked back up.
- Console gets flooded with `print(line_text)` / `print(self.data)` on every OCR attempt for every field. Useful for debugging one page, but makes a 100+ page run's real output nearly impossible to scan by eye.
- Pages classified as "unknown company" get silently dropped into the output PDF with no log of which address/page they were.
- The disabled `_preprocess` method is now dead code sitting unused in the file - worth deleting or actually deciding when V4 wants it, instead of leaving it commented out.
- `ServiceAddress` tab matching is plain substring containment - a short tab name could accidentally match inside a longer, unrelated address, with no warning if more than one tab could match.
- The run's final summary only ever exists in the terminal - nothing saves it, so once the window closes that record is gone.
