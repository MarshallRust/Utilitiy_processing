import re
from Sheet_Data_Class import SheetData
import string
import time
from datetime import datetime

class Dates(SheetData):
    VALIDATE_DATE = True  # set False for stuff like EffectiveDates that isn't a single date

    def __init__(self, image, company):
        super().__init__(image, company)

    def extract_data_from_line(self, line_text):
        print(line_text)
        normalized_line = line_text.translate(str.maketrans("", "", string.punctuation + " ")).lower()
        normalized_search = self.SEARCH_TERM[self.utility_company].translate(str.maketrans("", "", string.punctuation + " ")).lower()

        index = normalized_line.find(normalized_search)

        if index != -1:
            approx_start = max(0, index)
            line_text = line_text[approx_start:]  # cut off everything before the search term

        match = re.search(self.DATE_PATTERN, line_text)
        if match:
            candidate = match.group(0)
            if self.VALIDATE_DATE:
                try:
                    datetime.strptime(candidate, "%m/%d/%Y")
                except ValueError:
                    return ""  # OCR misread a digit, not a real date - keep retrying
            self.data = candidate
            print(self.data)
            return candidate
        else:
            return ""

    def get_custom_config(self):
        if self.utility_company == 0:
            return super().get_custom_config()
        else:
            return (
                r'--oem 3 --psm 4 '
                r'-c preserve_interword_spaces=1 '
                r'-c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.:-/ '
                r'-c tessedit_do_invert=1 '
                r'-c classify_bln_numeric_mode=1'
            )
