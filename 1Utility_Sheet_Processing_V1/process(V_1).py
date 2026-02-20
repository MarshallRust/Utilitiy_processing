from operator import index

from pytesseract import pytesseract
from PIL import Image
from pdf2image import convert_from_path
import pandas as pd

def add_space(string):
    new_string = str()
    for letter in string:
        if letter != " " and letter != "." and letter != "," and letter !="\n":
            letter.lower()
            new_string += letter
    return new_string

def check(text):
    if "$" in text:
        if "." not in text:
            new_text = text[1:]
            num = int(new_text)
            if num > 1000:
                text = "$" + new_text[:2] + "." + new_text [-2:]
    return text

def find_item(image, item):

    list_of_cords = [
        # service address - 0
        (100,70,470,460),
        # account num - 1
        (1350,40,1560,80),
        # bill date - 2
        (1150,200,1400,235),
        # effective dates - 3
        (915, 450, 1200, 500),
        # due date - 4
        (1350, 1600, 1540, 1635),
        # amount - 5
        (1150,1600, 1350, 1635)


    ]
    new_image = image.crop(list_of_cords[0])
    text = pytesseract.image_to_string(new_image)
    new_string = add_space(text)
    new_string = check(new_string)
    # new_image.show()
    print(new_string)
    return new_string

def main():
    images = convert_from_path("PDFs/Embridge.pdf", poppler_path= r"C:\poppler\poppler-24.08.0\Library\bin")
    pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    list_of_tabs = []
    list_of_values = []
    excel_file = pd.ExcelFile("Test.xlsx")
    excel_tabs = list(excel_file.sheet_names)
    for tab in excel_tabs:
        tab_name = str(add_space(tab))
        list_of_tabs.append(tab_name)

    # for i in range(1):
    for image in images:
        # find service address first - if in list of tabs, set aside, else, ignore and move on
        service_address = find_item(image, 0)
        # if str(service_address) == list_of_tabs[0]:
        if True:
            # account_number = find_item(images[0], 1)
            # bill_date = find_item(images[0], 2)
            # effective_date = find_item(images[0], 3)
            # due_date = find_item(images[0], 4)
            # amount = find_item(images[0], 5)
            # list_of_values.append([effective_date, amount, account_number, bill_date, service_address,due_date])
            pass

    print(list_of_values)
if __name__ == '__main__':
    main()