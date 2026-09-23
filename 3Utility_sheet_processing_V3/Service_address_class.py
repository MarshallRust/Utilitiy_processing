from Sheet_Data_Class import SheetData
import pandas as pd
import time

_EXCEL_TABS = None


def remove_spaces_and_punctuation(s):
    if not s:
        return ""
    return ''.join(ch for ch in s.lower() if ch.isalnum())


# caches tab names so we're not re-reading the workbook for every page
def get_excel_tabs():
    global _EXCEL_TABS
    if _EXCEL_TABS is None:
        excel_file = pd.ExcelFile("Utilities Billed to Tenants - Sept25.xlsx")
        _EXCEL_TABS = [
            (remove_spaces_and_punctuation(tab), tab)
            for tab in excel_file.sheet_names
        ]
    return _EXCEL_TABS


def is_in_list_of_tabs(result):
    if not result:
        return "Empty"

    cleaned_result = remove_spaces_and_punctuation(result)
    for cleaned_tab, original_tab in get_excel_tabs():
        if cleaned_tab in cleaned_result:
            return original_tab  # return the real tab name, not the cleaned version
    return "Empty"


_BASE_CONFIG = (
    r'--oem 3 '
    r'-c preserve_interword_spaces=1 '
    r'-c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.:- '
    r'-c tessedit_do_invert=0 '
    r'-c classify_bln_numeric_mode=1 '
)


class ServiceAddress(SheetData):
    CORDS = [(50, 590, 1250, 720), (1400, 520, 2350, 650)]
    SEARCH_TERM = ["Service Address", "Service Address"]

    def __init__(self, image, company):
        super().__init__(image, company)

    def extract_data_from_line(self, line_text):
        new_string = ""
        recording = False

        for char in line_text:
            if char.isdigit() and not recording:
                recording = True
            if recording:
                if char in [",", "."]:
                    break
                new_string += char

        tab_name = is_in_list_of_tabs(new_string)
        if tab_name != "Empty":
            self.data = tab_name

            return tab_name

        return ""

    def get_custom_config(self):
        if self.utility_company == 0:
            return _BASE_CONFIG + r'--psm 6'
        else:
            return _BASE_CONFIG.replace('tessedit_do_invert=0', 'tessedit_do_invert=1') + r'--psm 6'
