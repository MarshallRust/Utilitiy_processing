from Dates_Class import Dates

class DueDate(Dates):
    DATE_PATTERN = r'\b\d{2}/\d{2}/\d{4}\b'
    CORDS = [(650, 1480, 1850, 1720),(850, 200, 1550, 400)]
    SEARCH_TERM = ["Past Due After", "Due Date"]

    def __init__(self, image, company):
        super().__init__(image, company)