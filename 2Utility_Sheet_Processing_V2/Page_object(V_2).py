class Page:
    def __init__(self, service_address, account_num, service_dates, bill_date, due_date, amount_due, image):
        self.service_address = service_address
        self.account_num = account_num
        self.service_dates = service_dates
        self.bill_date = bill_date
        self.due_date = due_date
        self.amount_due = amount_due
        self.image = image
    def __str__(self):
        return [self.service_address, self.account_num, self.bill_date, self.service_dates, self.due_date, self.amount_due, self.image]