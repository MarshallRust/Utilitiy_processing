from Dates_Class import Dates

class DueDate(Dates):
    DATE_PATTERN = r'\b\d{2}/\d{2}/\d{4}\b'
    CORDS = [(1120, 550, 2280, 620), (1400, 380, 2350, 460)]
    SEARCH_TERM = ["or after", "Due Date"]

    def __init__(self, image, company):
        super().__init__(image, company)