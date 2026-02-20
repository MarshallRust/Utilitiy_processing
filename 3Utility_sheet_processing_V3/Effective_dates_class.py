from Dates_Class import Dates

class EffectiveDates(Dates):
    DATE_PATTERN = r'\d{1,2}/\d{1,2}/\d{4}\s*-\s*\d{1,2}/\d{1,2}/\d{4}'
    CORDS = [(650, 470, 1170, 860),(850, 250, 1550, 450)]
    SEARCH_TERM = ["Service from", "Service Period"]

    def __init__(self, image, company):
        super().__init__(image, company)


