import io
from datetime import date, datetime

from django.test import TestCase

from app.apps.accounts.models import Tenant, User
from app.apps.sales.services_matrix import (
    detect_import_format,
    generate_template_xlsx,
    parse_matrix_xlsx,
)
from app.apps.sellers.models import Seller


class SalesMatrixTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Matrix Tenant')
        seller_user = User.objects.create_user(
            username='matrix-seller',
            tenant=self.tenant,
            role=User.Role.SELLER,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=seller_user,
            name='Matrix Seller',
            phone='11999999999',
        )

    def test_generated_bytes_are_detected_and_parsed_as_matrix(self):
        output = generate_template_xlsx(
            self.tenant, date(2026, 7, 20), date(2026, 7, 20),
        )
        content = output.getvalue()

        self.assertEqual(detect_import_format(content, 'modelo.xlsx'), 'matrix')
        self.assertEqual(parse_matrix_xlsx(content, self.tenant), [])

    def test_numeric_excel_date_is_normalized_to_date(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'IMPORTACAO'
        sheet.append(['DATA', self.seller.name])
        sheet.append([46223, 100.50])
        output = io.BytesIO()
        workbook.save(output)

        rows = parse_matrix_xlsx(output.getvalue(), self.tenant)

        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0]['date'], date)
        self.assertNotIsInstance(rows[0]['date'], datetime)
        self.assertEqual(rows[0]['amount_cents'], 10050)
