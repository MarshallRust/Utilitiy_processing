from pytesseract import pytesseract
import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageEnhance

class SheetData:
    def __init__(self, image, company):
        if not hasattr(self, "CORDS"):
            raise AttributeError(f"{self.__class__.__name__} must define a CORDS attribute")
        self.left_x, self.top, self.right_x, self.bottom = self.CORDS[company]
        self.image = image
        self.data = None
        self.times_tried = 1
        self.utility_company = company

    def find_data_in_image(self):
        """Main OCR loop. Searches bounding box for search term and extracts correct data."""
        size_offset = 0
        add_offset = 25
        if self.utility_company == 2:
            return None

        while True:
            new_image = self._crop_image_with_offset(size_offset)

            # If we've already tried > 3 times, apply enhanced preprocessing
            if self.times_tried > 3:
                ocr_image = self._preprocess(new_image)
            else:
                ocr_image = new_image

            data = self._get_ocr_data(ocr_image)
            if not data['text']:
                if not self._retry_or_fail():
                    return "empty"
                size_offset, add_offset = self._increase_offset(size_offset, add_offset)
                continue

            result = self._search_lines_for_term(data)
            if result:
                return result

            if not self._retry_or_fail():
                ocr_image.show()
                return "empty"
            size_offset, add_offset = self._increase_offset(size_offset, add_offset)

    def _crop_image_with_offset(self, size_offset):
        """Creates bounding box with current offset amount"""
        width, height = self.image.size
        left = max(0, self.left_x - size_offset)
        top = max(0, self.top - size_offset)
        right = min(width, self.right_x + size_offset)
        bottom = min(height, self.bottom + size_offset)
        new_image = self.image.crop((left, top, right, bottom))
        return new_image


    def _preprocess(self, img):
        """Enhance, binarize, and remove noise/lines for cleaner OCR."""

        # Convert to grayscale (PIL → OpenCV)
        gray = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)

        # Enhance contrast
        pil_img = Image.fromarray(gray)
        contrast = ImageEnhance.Contrast(pil_img).enhance(2.0)
        gray = np.array(contrast)

        # Threshold (binarize)
        _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

        # --- Remove small noise specks ---
        # This erodes small white regions and then restores text thickness
        kernel = np.ones((2, 2), np.uint8)
        cleaned = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)

        # --- Remove tiny connected components (specks smaller than 10x10) ---
        nb_components, output, stats, centroids = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
        sizes = stats[1:, -1]  # skip background
        min_size = 20  # adjust threshold for noise size
        filtered = np.zeros(output.shape, dtype=np.uint8)
        for i in range(nb_components - 1):
            if sizes[i] >= min_size:
                filtered[output == i + 1] = 255

        # --- Remove faint lines (optional) ---
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
        temp = filtered.copy()
        for k in [horiz_kernel, vert_kernel]:
            detected = cv2.morphologyEx(temp, cv2.MORPH_OPEN, k, iterations=1)
            temp = cv2.subtract(temp, detected)

        # Invert back (white background, black text)
        processed = cv2.bitwise_not(temp)

        # Convert back to PIL for Tesseract OCR
        return Image.fromarray(processed)

    def _get_ocr_data(self, new_image):
        """Run OCR on the bounding box"""
        custom_config = self.get_custom_config()
        return pytesseract.image_to_data(
            new_image, lang='eng', config=custom_config, output_type=pytesseract.Output.DICT
        )

    def _increase_offset(self, size_offset, add_offset):
        """Increase offsets to expand bounding box."""
        size_offset += add_offset
        add_offset += 25
        return size_offset, add_offset

    def _retry_or_fail(self):
        """Counts attempts"""
        self.times_tried += 1
        if self.times_tried > 5:
            return False
        return True

    def _generate_search_terms(self, term):
        """Tries multiple versions of the search term"""
        terms = [term, term.replace(" ", "")]
        words = term.split()
        for i in range(len(words)):
            terms.append(" ".join(words[:i] + words[i + 1:]))
        return terms

    def _search_lines_for_term(self, data):
        """Loop through OCR lines, look for a match, and extract data."""
        num_words = len(data['text'])
        lines_seen = set()

        for i in range(num_words):
            line_num = data['line_num'][i]
            if line_num in lines_seen:
                continue
            lines_seen.add(line_num)

            line_text = self._assemble_line_text(data, line_num, num_words)
            for term_variant in self._generate_search_terms(self.SEARCH_TERM[self.utility_company]):
                if term_variant.lower() in line_text.lower():
                    result = self.extract_data_from_line(line_text)
                    if result:
                        return result
        return None

    def _assemble_line_text(self, data, line_num, num_words):
        """Combine all words from a line into a single string."""
        line_words = [
            data['text'][j].strip()
            for j in range(num_words)
            if data['line_num'][j] == line_num and data['text'][j].strip() != ''
        ]
        return " ".join(line_words).strip()

    """
    Overrides
    """
    def extract_data_from_line(self, line_text):
        """Subclasses implement this to parse the line text and return only the desired data."""
        raise NotImplementedError("Subclasses must override extract_data_from_line()")

    def get_custom_config(self):
        """Return a config that gradually relaxes OCR strictness based on attempts."""
        base_whitelist = '0123456789$.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
        if self.times_tried == 1:
            whitelist = base_whitelist
            psm = 6
        elif self.times_tried == 2:
            whitelist = base_whitelist + "-,"
            psm = 6
        elif self.times_tried == 3:
            whitelist = base_whitelist + "-, "
            psm = 6
        else:
            whitelist = ''
            psm = 11

        return (
            f'--oem 3 --psm {psm} '
            f'-c preserve_interword_spaces=1 '
            f'-c tessedit_char_whitelist={whitelist} '
            f'-c tessedit_do_invert=0 '
            f'-c textord_heavy_nr=0 '
            f'-c textord_min_linesize=1 '
            f'-c textord_max_noise_size=5 '
            f'-c textord_noise_rejwords=0 '
            f'-c textord_noise_rejrows=0'
        )
