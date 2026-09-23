from operator import index

from pandas.core.dtypes.inference import is_number
from pytesseract import pytesseract
from PIL import Image
from pdf2image import convert_from_path
import pandas as pd
from Page_object import Page
import re

LIST_OF_SEARCH_ITEMS = [
    "Service Address: ",
    "Account: ",
    "as of ",
    "from ",
    "on or after ",
    "receipt",
    "Page"
]

LIST_OF_CORDS = [
    # left, top, right, bottom
    # service address - 0
    (465, 430, 470, 460),
    # account number - 1
    (1, 1, 1, 1),
    # bill date - 2
    (1, 1, 1, 1),
    # service dates - 3
    (1, 1, 1, 1),
    # due date - 4
    (1, 1, 1, 1),
    # amount - 5
    (1, 1, 1, 1),
    # page_num - 6
    (1300, 0, 2000, 200)
]

def find_data_using_search_term(index_for_cords, image):
    new_image = image.crop(LIST_OF_CORDS[index_for_cords])
    search_term_to_be_found = LIST_OF_SEARCH_ITEMS[index_for_cords]

    custom_config = (
        r'--oem 3 '
        r'--psm 6 '
        r'-c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.:- '
        r'-c preserve_interword_spaces=1 '
        r'-c tessedit_do_invert=0 '
        r'-c classify_bln_numeric_mode=1'
    )

    data = pytesseract.image_to_data(
        new_image, lang='eng', config=custom_config, output_type=pytesseract.Output.DICT
    )

    num_words = len(data['text'])

    # group words by line, see if the search term shows up anywhere in the line
    for i in range(num_words):
        line_num = data['line_num'][i]

        line_words = [
            data['text'][j]
            for j in range(num_words)
            if data['line_num'][j] == line_num and data['text'][j].strip() != ''
        ]
        line_text = " ".join(line_words).strip()

        if search_term_to_be_found.lower() in line_text.lower():
            # bounding box around the whole matched line
            lefts = [data['left'][j] for j in range(num_words) if data['line_num'][j] == line_num]
            tops = [data['top'][j] for j in range(num_words) if data['line_num'][j] == line_num]
            rights = [
                data['left'][j] + data['width'][j]
                for j in range(num_words) if data['line_num'][j] == line_num
            ]
            bottoms = [
                data['top'][j] + data['height'][j]
                for j in range(num_words) if data['line_num'][j] == line_num
            ]
            cords = (min(lefts), min(tops), max(rights), max(bottoms))
            cropped_image = new_image.crop(cords)

            return line_text, cords, cropped_image

    return None, None, image

def process_data(data, image, cords, index_for_processing):
    function_list =[
        handle_service_address,
        handle_account_num,
        handle_bill_date,
        handle_service_dates,
        handle_due_dates,
        handle_amount,
        handle_page_num
    ]


    return function_list[index_for_processing](data, image, cords)

def handle_service_address(data, image, cords):
    pass

def handle_account_num(data, image, cords):
    pass

def handle_bill_date(data, image, cords):
    pass

def handle_service_dates(data, image, cords):
    pass

def handle_amount(data, image, cords):
    pass

def handle_due_dates():
    pass

def handle_page_num(data, image, cords):

    """NEEDS TO BE HANDLED LATER ON - IF PAGE NUMBER = 3OF4
    NEEDS TO RUN IT AGAIN BUT WITH DIFFERENT PARAMATERS FOR THE CORDS FOR EVERYTHING - MAYBE MAKE A LIST OF 1OF4 AND 3OF4 IDK\
    if isinstance(result, list):
    run again
else:
    run as normal
    """
    if not data:
        return None

    allowed_chars = {'1', '2','o','f','3', '4'}
    new_string = ""

    for char in data.lower():
        if char in allowed_chars:
            if len(new_string) < 4:
                new_string += char

    return new_string





def find_item(image, index_for_cords, tab_names, should_offset_both = False):
    """ working here - just starting on how to compare my data to the Excel sheet - addresses"""
    data, cords, new_image = find_data_using_search_term(index_for_cords, image)
    new_image.show(title=f"Index {index_for_cords}")
    processed_data = process_data(data, new_image, cords, index_for_cords)

    # if index_for_cords == 0:
    #     is_on_excel_sheet = compare_sheet_with_data(required_data, tab_names)
    #     if is_on_excel_sheet:
    #         return required_data
    #     else:
    #         return False

    return processed_data


def create_page_object(image, list_of_values, service_address):
    # account_number = find_item(image, 1)
    # bill_date = find_item(image, 2)
    # service_dates = find_item(image, 3)
    # due_date = find_item(image, 4)
    # amount = find_item(image, 5)
    # page = Page(service_address, account_number, bill_date, service_dates, due_date, amount, image)
    # list_of_values.append(page)
    pass

def main():
    images = convert_from_path("PDFs/Embridge.pdf", poppler_path=r"C:\poppler\poppler-24.08.0\Library\bin")
    pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    list_of_tabs = []
    list_of_values = []
    excel_file = pd.ExcelFile("Test.xlsx")
    excel_tabs = list(excel_file.sheet_names)
    for tab in excel_tabs:
        tab_name = str(tab)
        list_of_tabs.append(tab_name)

    for image in images:
        # service_address_without_spaces = find_item(image, 0, list_of_tabs)
        page_number = find_item(image,6,list_of_tabs)
        print(page_number)
        # if service_address_without_spaces:
        #     service_address = split_address_chars(service_address_without_spaces)
        #     print(service_address)
            # create_page_object(image, list_of_values, service_address)

if __name__ == '__main__':
    main()
