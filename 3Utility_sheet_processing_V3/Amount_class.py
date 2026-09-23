from Sheet_Data_Class import SheetData
import time
import string

class Amount(SheetData):
    CORDS = [(1100, 485, 2350, 590), (1400, 745, 2350, 830)]
    SEARCH_TERM = ["$", "Total Amount Due"]

    def __init__(self, image, company_index):
        super().__init__(image, company_index)
        self.utility_company = company_index

    def extract_data_from_line(self, line_text):
        if self.utility_company == 0:
            new_string = self.extract_after_dollar(line_text)
        elif self.utility_company == 1:
            new_string = self._extract_after_search_term(line_text)
        else:
            return 0.0
        return self._clean_and_convert(new_string)

    def extract_after_dollar(self, line_text, max_len=7):
        new_string = ""
        current_len = 0
        part_of_string = False

        for char in line_text:
            if char == "$":
                part_of_string = True
                new_string = ""
                current_len = 0
                continue

            if part_of_string and current_len < max_len:
                if char.isdigit() or char in ".,":
                    new_string += char
                    current_len += 1
                else:
                    part_of_string = False

        return new_string

    def _extract_after_search_term(self, line_text, max_len=7):
        new_string = ""
        current_len = 0
        term = self.SEARCH_TERM[self.utility_company]

        normalized_line = line_text.translate(str.maketrans("", "", string.punctuation + " ")).lower()
        normalized_term = term.translate(str.maketrans("", "", string.punctuation + " ")).lower()
        words = normalized_term.split()
        if not words:
            return ""

        match_found = normalized_term in normalized_line or any(word in normalized_line for word in words)
        if not match_found:
            return ""

        first_word = words[0]
        term_index = line_text.lower().find(first_word)
        if term_index == -1:
            return ""

        for char in line_text[term_index:]:
            if char.isdigit() or char in ".,":
                new_string += char
                current_len += 1
                if current_len >= max_len:
                    break
        return new_string

    def _clean_and_convert(self, raw_string):
        cleaned = raw_string.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            value = 0.0
        self.data = value
        return value
