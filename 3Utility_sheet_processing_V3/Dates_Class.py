import re
from Sheet_Data_Class import SheetData
import string
import time

class Dates(SheetData):

    def __init__(self, image, company):
        super().__init__(image, company)

    def extract_data_from_line(self, line_text):
        print(line_text)
        # --- Normalize both line and search term ---
        normalized_line = line_text.translate(str.maketrans("", "", string.punctuation + " ")).lower()
        normalized_search = self.SEARCH_TERM[self.utility_company].translate(str.maketrans("", "", string.punctuation + " ")).lower()

        # --- Find where the normalized search term appears ---
        index = normalized_line.find(normalized_search)

        if index != -1:
            # Figure out where in the *original* line this corresponds
            # by mapping character positions approximately
            # (We use index to slice the original line starting near the same place)
            approx_start = max(0, index)
            line_text = line_text[approx_start:]  # cut off everything before the search term

        # --- Now search for the date pattern ---
        match = re.search(self.DATE_PATTERN, line_text)
        if match:
            self.data = match.group(0)
            print(self.data)
            return match.group(0)
        else:
            return ""

    def get_custom_config(self):
        """Use different OCR modes depending on the utility company."""
        if self.utility_company == 0:
            return super().get_custom_config()
        else:
            return (
                r'--oem 3 --psm 4 '
                r'-c preserve_interword_spaces=1 '
                r'-c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.:-/ '
                r'-c tessedit_do_invert=0 '
                r'-c classify_bln_numeric_mode=1'
            )
