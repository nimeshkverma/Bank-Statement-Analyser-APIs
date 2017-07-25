import os
import subprocess
import uuid
import requests
import json
import datetime
import csv
from copy import deepcopy
from cStringIO import StringIO

from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.converter import TextConverter
from pdfminer.layout import LAParams
from pdfminer.pdfpage import PDFPage

from tabula import read_pdf
from django.conf import settings

import re
from ICICI_bank_statements_a_service import ICICIBankStatementsA
from ICICI_bank_statements_b_service import ICICIBankStatementsB
from HDFC_bank_statements_service import HDFCBankStatements
from AXIS_bank_statements_a_service import AXISBankStatementsA
from AXIS_bank_statements_b_service import AXISBankStatementsB
from SBI_bank_statements_service import SBIBankStatements

from database_service import Database
from email_service import send_mail


class BankStatementsRawData(object):
    """Class to obtain the Raw data from the Bank Statements"""

    def __init__(self, pdf_path, password=''):
        self.pdf_path = pdf_path
        self.password = password
        self.tabula_params = {
            'pages': 'all',
            'guess': True,
            'pandas_options': {
                'error_bad_lines': False
            },
            'password': self.password,
            'output_format': 'json',
        }
        self.pdf_json = self.__get_pdf_json()
        self.raw_table_data = self.__get_raw_table_data()
        self.pdf_text = self.__get_pdf_text()

    def __get_tabula_params(self, password_on=False):
        if password_on:
            return self.tabula_params
        else:
            tabula_params = deepcopy(self.tabula_params)
            tabula_params['password'] = self.password
            return tabula_params

    def __get_pdf_json(self):
        try:
            return read_pdf(self.pdf_path, **self.__get_tabula_params(True))
        except Exception as e:
            return read_pdf(self.pdf_path, **self.__get_tabula_params(False))

    def __get_decrypted_pdf_path(self):
        if '.pdf' in self.pdf_path:
            path_list = self.pdf_path.split('.pdf')
            return path_list[0] + '_decrypted.pdf'
        elif '.pdf' in self.pdf_path:
            path_list = self.pdf_path.split('.PDF')
            return path_list[0] + '_decrypted.pdf'
        else:
            self.pdf_path + +'_decrypted.pdf'

    def __get_pdf_text(self):
        pdf_text = ''
        pdf_path_decrypt = self.__get_decrypted_pdf_path()
        file_clean_command = 'rm {pdf_path_decrypt}'.format(
            pdf_path_decrypt=pdf_path_decrypt)
        try:
            decrypt_command = 'qpdf --password={password} --decrypt {pdf_path} {pdf_path_decrypt}'.format(
                password=self.password, pdf_path=self.pdf_path, pdf_path_decrypt=pdf_path_decrypt)
            subprocess.call(decrypt_command, shell=True)
        except Exception as e:
            decrypt_command = 'qpdf --password={password} --decrypt {pdf_path} {pdf_path_decrypt}'.format(
                password='', pdf_path=self.pdf_path, pdf_path_decrypt=pdf_path_decrypt)
            subprocess.call(decrypt_command, shell=True)
        pdf_text = self.__pdf_to_text(pdf_path_decrypt)
        subprocess.call(file_clean_command, shell=True)
        return pdf_text

    def __get_raw_table_data(self):
        raw_table_data = {}
        rows_data_list = []
        for data_dict in self.pdf_json:
            for rows_data in data_dict.get('data', []):
                row_data_list = []
                for row_data in rows_data:
                    if row_data.get('text'):
                        row_data_list.append(row_data['text'])
                if row_data_list:
                    rows_data_list.append(row_data_list)
        if rows_data_list:
            raw_table_data = {
                'headers': rows_data_list[0],
                'body': rows_data_list[1:]
            }
        return raw_table_data

    def __pdf_to_text(self, pdf_path_decrypt):
        pagenums = set()
        output = StringIO()
        manager = PDFResourceManager()
        converter = TextConverter(manager, output, laparams=LAParams())
        interpreter = PDFPageInterpreter(manager, converter)

        infile = file(pdf_path_decrypt, 'rb')
        for page in PDFPage.get_pages(infile, pagenums):
            interpreter.process_page(page)
        infile.close()
        converter.close()
        text = output.getvalue()
        output.close
        return text


class BankStatements(object):
    """Class for determinsation of Specific Bank from the Bank Statements"""

    def __init__(self, pdf_path, password=''):
        self.pdf_path = pdf_path
        self.password = password
        self.bank_statememt_raw_data = BankStatementsRawData(
            self.pdf_path, self.password)
        self.raw_table_data = self.bank_statememt_raw_data.raw_table_data
        self.pdf_text = self.bank_statememt_raw_data.pdf_text
        self.bank_dict = {
            'icici_a': {
                'unique_header': 'Transaction Remarks',
                'class': ICICIBankStatementsA,
            },
            'icici_b': {
                'unique_header': 'ACCOUNT TYPE',
                'class': ICICIBankStatementsB,
            },
            'hdfc': {
                'unique_header': 'Narration',
                'class': HDFCBankStatements,
            },
            'axis_a': {
                'unique_header': 'Particulars',
                'class': AXISBankStatementsA,
            },
            'axis_b': {
                'unique_header': 'Bank Account',
                'class': AXISBankStatementsB,
            },
            'sbi': {
                'unique_header': 'Description',
                'class': SBIBankStatements,
            }
        }
        self.banks = ['icici_a', 'hdfc', 'axis_a', 'axis_b', 'sbi', 'icici_b']
        self.bank_name = None
        self.specific_bank = self.__get_specific_bank()

    def __term_in_header(self, term):
        for header in self.raw_table_data['headers']:
            if re.search(term, header, re.IGNORECASE):
                return True
        return False

    def __get_specific_bank(self):
        for bank in self.banks:
            if self.__term_in_header(self.bank_dict[bank]['unique_header']):
                self.bank_name = bank
                return self.bank_dict[bank]['class'](self.raw_table_data, self.pdf_text)
        return None


class BankStatementsAnalyser(object):

    def __init__(self, customer_id, document_type_id):
        self.customer_id = customer_id
        self.document_type_id = document_type_id
        self.db = Database('backend_db')
        self.bank_pdf_s3_url = None
        self.bank_pdf_password = None
        self.loan_details = {}
        self.template = 'statements/v1/bank_statement_analysis_email.html'
        self.pwd = self.__pwd()
        self.__set_bank_pdf_details()
        self.__set_loan_details()
        self.bank_pdf_path = self.__get_bank_pdf_path()
        self.bank_statements = self.__get_bank_statements()
        self.bank_data = self.__get_bank_data()
        self.__clean_up()

    def __clean_up(self):
        self.db.close_connection()
        subprocess.call('rm {bank_pdf_path}'.format(
            bank_pdf_path=self.bank_pdf_path), shell=True)

    def __get_bank_statements(self):
        bank_statements = None
        try:
            bank_statements = BankStatements(
                self.bank_pdf_path, self.bank_pdf_password)
        except Exception as e:
            pass
        return bank_statements

    def __pwd(self):
        pwd = ''
        try:
            subprocess_pwd = subprocess.check_output('pwd')
            pwd = subprocess_pwd.split('\n')[0] + '/statements/v1/services'
        except Exception as e:
            pass
        return pwd

    def __get_bank_details_sql_query(self):
        return """
                     SELECT  document_1, document_1_password
                     FROM customer_documents
                     WHERE customer_id={customer_id} and document_type_id={document_type_id};
               """.format(customer_id=self.customer_id, document_type_id=self.document_type_id)

    def __set_bank_pdf_details(self):
        query = self.__get_bank_details_sql_query()
        rows = self.db.execute_query(query)
        for row in rows:
            self.bank_pdf_s3_url = settings.S3_URL + str(row['document_1'])
            self.bank_pdf_password = row['document_1_password'] if row[
                'document_1_password'] else ''
            break

    def __get_loan_details_sql_query(self):
        return """
                select loan_emi, loan_amount, monthly_income, existing_emi, loan_tenure
                from loan_product where customer_id={customer_id};
               """.format(customer_id=self.customer_id)

    def __set_loan_details(self):
        query = self.__get_loan_details_sql_query()
        rows = self.db.execute_query(query)
        for row in rows:
            self.loan_details = {
                'loan_emi': row['loan_emi'],
                'loan_amount': row['loan_amount'],
                'existing_emi': row['existing_emi'],
                'loan_tenure': row['loan_tenure'],
            }
            break

    def __get_bank_pdf_path(self):
        pdf_path = None
        if self.bank_pdf_s3_url:
            r = requests.get(self.bank_pdf_s3_url, timeout=20)
            if r.status_code == 200:
                file_name = '{pwd}/{uuid}.pdf'.format(
                    pwd=self.pwd, uuid=uuid.uuid4().hex)
                with open(file_name, 'wb') as f:
                    f.write(r.content)
                pdf_path = file_name
        return pdf_path

    def __get_bank_data(self):
        data = {
            'loan_details': self.loan_details,
        }
        if self.bank_statements and self.bank_statements.specific_bank:
            data['bank_name'] = self.bank_statements.bank_name
            data.update(self.bank_statements.specific_bank.data_json(
                self.loan_details.get('loan_emi', 0)))
        return data

    def __create_bank_statement_csv(self):
        csv_name = "{pwd}/Customer{customer_id}_Bank_{bank}.csv".format(
            pwd=self.pwd, customer_id=self.customer_id, bank=self.bank_data['bank_name'])
        with open(csv_name, 'w') as csvfile:
            writer = csv.writer(csvfile, delimiter=',')
            writer.writerow(['Attribute Name', 'Value'])
            for loan_detail_key, loan_detail_value in self.bank_data['loan_details'].iteritems():
                writer.writerow([loan_detail_key, loan_detail_value])
            for stats_key, stats_value in self.bank_data['stats'].iteritems():
                writer.writerow([stats_key, stats_value])

            writer.writerow(['', ''])
            writer.writerow(
                ['Month-Year', 'No of day Balance is above EMI', 'No of days Analysed'])
            for monthly_stats_key, monthly_stats_value in self.bank_data['monthly_stats'].iteritems():
                writer.writerow([monthly_stats_key, monthly_stats_value[
                                'balance_above_day_count'], monthly_stats_value['all_day_count']])

            for i in xrange(0, 25 - len(self.bank_data['monthly_stats'])):
                writer.writerow([''])

            writer.writerow(['', ''])
            writer.writerow(['Date', 'Balance'])
            for transaction_key, transaction_value in self.bank_data['all_transactions'].iteritems():
                writer.writerow([transaction_key, transaction_value])
        return csv_name

    def __remove_file(self, file_path):
        if os.path.isfile(file_path):
            os.remove(file_path)

    def send_bank_analysis_email(self):
        csvfile = self.__create_bank_statement_csv()
        email_data = {
            'data': {
                'customer_id': self.customer_id,
                'bank': self.bank_data['bank_name'],
            }
        }
        email_details = {
            'data': email_data,
            'subject': "Customer:{customer_id} Bank:{bank} Statements Analysis".format(customer_id=self.customer_id, bank=self.bank_data['bank_name']),
            'body': "Empty",
            'sender_email_id': settings.SERVER_EMAIL,
            'reciever_email_ids': settings.RECIEVER_EMAILS,
        }
        send_mail(email_details, self.template, [csvfile])
        self.__remove_file(csvfile)
