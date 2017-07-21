import re
import datetime
from copy import deepcopy

MIN_COLUMNS = 5
MAX_COLUMNS = 6


HEADER = set(['Date', 'Narration', 'Chq./Ref.No.', 'Value Dt',
              'Withdrawal Amt.', 'Deposit Amt.', 'Closing Balance'])

MAX_START_DAY_OF_MONTH = 5
MIN_END_DAY_OF_MONTH = 25


class HDFCBankStatements(object):
    """Class to analyse the data obtained from HDFC Bank"""

    def __init__(self, raw_table_data, pdf_text):
        self.raw_table_data = raw_table_data
        self.pdf_text = pdf_text
        self.statements = []
        self.transactions = {}
        self.__set_statements_and_transaction()
        self.stats = {}
        self.__set_pdf_text_stats()
        self.all_day_transactions = self.__get_all_day_transactions()
        self.__set_stats()

    def __get_date(self, date_input):
        all_date_list = []
        all_string_date_list = re.findall(r'(\d{2}/\d{2}/\d{2,4})', date_input)
        for string_date in all_string_date_list:
            try:
                all_date_list.append(
                    datetime.datetime.strptime(string_date, '%d/%m/%y'))
            except Exception as e:
                try:
                    all_date_list.append(
                        datetime.datetime.strptime(string_date, '%d/%m/%Y'))
                except Exception as e:
                    pass
        return all_date_list[0]

    def __get_amount(self, input_string):
        comma_remove_input_string = input_string.replace(',', '')
        try:
            return float(comma_remove_input_string)
        except Exception as e:
            return 0.0

    def __deconcatinate_numbers(self, input_string):
        number_list = input_string.split(' ')
        if len(number_list) == 2:
            for index in xrange(0, len(number_list)):
                number_list[index] = self.__get_amount(number_list[index])
            return number_list
        return [0.0, 0.0]

    def __get_statement_set_transaction(self, data_list):
        statement_dict = {}
        try:
            if len(data_list) in [MIN_COLUMNS, MAX_COLUMNS]:
                statement_dict = {
                    'value_date': self.__get_date(data_list[0]),
                    'narration': str(data_list[1]),
                    'reference_no': str(data_list[2]),
                    'transaction_date': self.__get_date(data_list[3]),
                }
                if len(data_list) == MIN_COLUMNS:
                    statement_dict.update({
                        'withdraw_deposit': self.__deconcatinate_numbers(data_list[4])[0],
                        'closing_balance': self.__deconcatinate_numbers(data_list[4])[1],
                    })
                elif len(data_list) == MAX_COLUMNS:
                    statement_dict.update({
                        'withdraw_deposit': self.__get_amount(data_list[4]),
                        'closing_balance': self.__get_amount(data_list[5]),
                    })
                else:
                    statement_dict = {}
                if statement_dict:
                    self.transactions[statement_dict[
                        'transaction_date']] = statement_dict['closing_balance']
        except Exception as e:
            print "Following error occured while processing {data_list} : {error}".format(data_list=str(data_list), error=str(e))
        return statement_dict

    def __set_statements_and_transaction(self):
        for data_list in self.raw_table_data.get('body', []):
            if MIN_COLUMNS <= len(data_list) <= MAX_COLUMNS and not HEADER.intersection(set(data_list)):
                statement_dict = self.__get_statement_set_transaction(
                    data_list)
                self.statements.append(
                    statement_dict) if statement_dict else None

    def __get_pdf_dates(self):
        pdf_dates = []
        from_to_string_date_list = re.findall(r'Statement From : \d{2}/\d{2}/\d{2}', self.pdf_text) + re.findall(
            r'From : \d{2}/\d{2}/\d{4}', self.pdf_text) + re.findall(r'TO : \d{2}/\d{2}/\d{2}', self.pdf_text) + re.findall(r'To : \d{2}/\d{2}/\d{4}', self.pdf_text)
        for from_to_string_date in from_to_string_date_list:
            pdf_dates.append(from_to_string_date.split(' : ')[-1])
        return pdf_dates

    def __set_pdf_text_stats(self):
        self.stats['start_date'] = min(self.transactions.keys())
        self.stats['end_date'] = max(self.transactions.keys())
        all_string_date_list = self.__get_pdf_dates()
        all_date_list = []
        for string_date in all_string_date_list:
            try:
                all_date_list.append(
                    datetime.datetime.strptime(string_date, '%d/%m/%Y'))
            except Exception as e:
                try:
                    all_date_list.append(
                        datetime.datetime.strptime(string_date, '%d/%m/%y'))
                except Exception as e:
                    pass
        self.stats['pdf_text_start_date'] = min(
            all_date_list) if all_date_list else self.stats['start_date']
        self.stats['pdf_text_end_date'] = max(
            all_date_list) if all_date_list else self.stats['end_date']
        self.stats['days'] = (self.stats['pdf_text_end_date'] -
                              self.stats['pdf_text_start_date'] + datetime.timedelta(1)).days

    def __get_first_day_balance(self):
        opening_balance = None
        for index in xrange(0, len(self.raw_table_data.get('body', []))):
            if len(self.raw_table_data['body'][index]) == 5 and self.raw_table_data['body'][index][0] == 'Opening Balance':
                if index + 1 < len(self.raw_table_data['body']) and len(self.raw_table_data['body'][index + 1]):
                    opening_balance = self.raw_table_data['body'][index + 1][0]
                    try:
                        opening_balance = float(
                            opening_balance.replace(',', ''))
                    except Exception as e:
                        opening_balance = None
        return opening_balance if opening_balance else self.transactions[self.stats['start_date']]

    def __get_all_day_transactions(self):
        all_day_transactions = {}
        all_day_transactions[self.stats[
            'pdf_text_start_date']] = self.__get_first_day_balance()
        for day_no in xrange(1, self.stats['days']):
            day_date = self.stats['pdf_text_start_date'] + \
                datetime.timedelta(days=day_no)
            all_day_transactions[day_date] = self.transactions[day_date] if self.transactions.get(
                day_date) else all_day_transactions[day_date - datetime.timedelta(days=1)]
        return all_day_transactions

    def __min_date(self):
        if self.stats['pdf_text_start_date'].day <= MAX_START_DAY_OF_MONTH:
            return self.stats['pdf_text_start_date']
        day = 1
        month = self.stats['pdf_text_start_date'].month + \
            1 if self.stats['pdf_text_start_date'].month != 12 else 1
        year = self.stats['pdf_text_start_date'].year if self.stats[
            'pdf_text_start_date'].month != 12 else self.stats['pdf_text_start_date'].year + 1
        return datetime.datetime(year, month, day)

    def __max_date(self):
        if self.stats['pdf_text_end_date'].day >= MIN_END_DAY_OF_MONTH:
            return self.stats['pdf_text_end_date']
        return datetime.datetime(self.stats['pdf_text_end_date'].year, self.stats['pdf_text_end_date'].month, 1) - datetime.timedelta(days=1)

    def get_days_above_given_balance_unpartial_months(self, given_balance):
        min_date = self.__min_date()
        max_date = self.__max_date()
        above_given_balance_daywise = {}
        for day, balance in self.all_day_transactions.iteritems():
            if balance >= given_balance and min_date <= day <= max_date:
                above_given_balance_daywise[day] = balance
        return {
            'given_balance': given_balance,
            'no_of_days': len(above_given_balance_daywise),
            'above_balance_daywise': above_given_balance_daywise,
        }

    def get_days_above_given_balance(self, given_balance):
        above_given_balance_daywise = {}
        for day, balance in self.all_day_transactions.iteritems():
            if balance >= given_balance:
                above_given_balance_daywise[day] = balance
        return {
            'given_balance': given_balance,
            'no_of_days': len(above_given_balance_daywise),
            'above_balance_daywise': above_given_balance_daywise,
        }

    def __set_stats(self):
        self.stats['average_balance'] = round(sum(
            self.all_day_transactions.values()) /
            len(self.all_day_transactions.values()), 2)

    def __json_statements(self):
        statements = []
        for statement in self.statements:
            data = deepcopy(statement)
            for key in ['value_date', 'transaction_date']:
                data[key] = data[key].strftime("%d/%m/%y")
            for key in ['withdraw_deposit', 'closing_balance']:
                data[key] = str(data[key])
            statements.append(data)
        return statements

    def __json_transactions(self):
        transactions = {}
        for day, balance in self.all_day_transactions.iteritems():
            transactions[day.strftime("%d/%m/%y")] = str(balance)
        return transactions

    def __json_stats(self):
        stats = {}
        for key, value in self.stats.iteritems():
            if type(value) == datetime.datetime:
                stats[key] = value.strftime("%d/%m/%y")
            elif type(value) == float:
                stats[key] = str(value)
            else:
                stats[key] = value
        return stats

    def __json_days_above_given_balance(self, threshhold):
        days_above_given_balance = self.get_days_above_given_balance(
            threshhold)
        above_balance_daywise = days_above_given_balance.pop(
            'above_balance_daywise', {})
        days_above_given_balance['above_balance_daywise'] = {}
        for day, balance in above_balance_daywise.iteritems():
            days_above_given_balance['above_balance_daywise'][
                day.strftime("%d/%m/%y")] = str(balance)
        return days_above_given_balance

    def __json_monthly_stats(self, threshhold):
        monthly_stats = {}
        for day, balance in self.all_day_transactions.iteritems():
            if monthly_stats.get(day.month):
                monthly_stats[day.month]['all_day_count'] += 1
                monthly_stats[day.month]['balance_above_day_count'] = monthly_stats[day.month][
                    'balance_above_day_count'] + 1 if balance >= threshhold else monthly_stats[day.month]['balance_above_day_count']
            else:
                monthly_stats[day.month] = {
                    'all_day_count': 1,
                    'balance_above_day_count': 1 if balance >= threshhold else 0,
                }
        return monthly_stats

    def data_json(self, threshhold):
        data = {
            'raw_statements': self.__json_statements(),
            'all_transactions': self.__json_transactions(),
            'stats': self.__json_stats(),
            'above_emi_balance_data': self.__json_days_above_given_balance(threshhold),
            'monthly_stats': self.__json_monthly_stats(threshhold),
            'bank_name': 'HDFC',
        }
        return data
