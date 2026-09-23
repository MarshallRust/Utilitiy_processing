from Dates_Class import Dates

class EffectiveDates(Dates):
    DATE_PATTERN = r'\d{1,2}/\d{1,2}/\d{4}\s*-\s*\d{1,2}/\d{1,2}/\d{4}'
    CORDS = [(1080, 720, 1850, 840), (1400, 455, 2350, 535)]
    SEARCH_TERM = ["Service from", "Service Period"]
    VALIDATE_DATE = False  # this field matches a date RANGE, not a single calendar date

    def __init__(self, image, company):
        super().__init__(image, company)


