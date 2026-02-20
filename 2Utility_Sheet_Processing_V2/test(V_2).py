"""
    # sets the original offset
    offset = 0
    # temperary - tracks how many times the code has run to be able to stop it if needed
    fail_safe = 0
    # creats a list of cords
    cords = LIST_OF_CORDS[index_for_cords]

    while True:
        # for items that require both x-axis cords to change for the search to work

        if should_offset_both:
            print(1)
            modified_cords = (cords[0] - offset, cords[1], cords[2] - offset, cords[3])
        else:
            modified_cords = (cords[0] - offset, cords[1], cords[2], cords[3])
            print(2)

        new_image = image.crop(modified_cords)
        new_image.show()
        print(3)

        custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.: '
        text_for_address = pytesseract.image_to_string(image, lang='eng', config=custom_config)
        test_text = pytesseract.image_to_string(new_image, lang='eng', config=custom_config)

        print(4)

        if index_for_cords == 0:
            string_created = check_data(text_for_address,index_for_cords,tab_names)
            print(5)

        else:
            string_created = check_data(test_text, index_for_cords, tab_names)
            print(6)
            print(string_created)

            if test_text != ":" and len(test_text) == 10:
                new_image.show()
                print(7)
                is_int = is_int_convertible(test_text)

                if is_int:
                    print(8)
                    return test_text

        if fail_safe > 15:
            break

        if string_created:
            return string_created
        else:
            print(9)
            offset += 20
            fail_safe += 1

    return test_text
    """