from Dates_Class import Dates

class BillDate(Dates):
    DATE_PATTERN = r'\b\d{2}/\d{2}/\d{4}\b'
    CORDS = [(1100, 300, 2000, 410), (1400, 290, 2350, 400)]
    SEARCH_TERM = ["As of", "Bill Date"]

    def __init__(self, image, company):
        super().__init__(image, company)
