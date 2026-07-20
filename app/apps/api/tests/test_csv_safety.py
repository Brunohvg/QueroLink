from django.test import SimpleTestCase

from app.services.csv_safety import safe_csv_cell, safe_csv_row


class CsvSafetyTests(SimpleTestCase):
    def test_formula_prefixes_are_neutralized(self):
        for value in ('=1+1', '+cmd', '-10+20', '@SUM(A1:A2)', '  =1+1'):
            with self.subTest(value=value):
                self.assertTrue(safe_csv_cell(value).startswith("'"))

    def test_plain_text_and_numeric_values_are_preserved(self):
        self.assertEqual(safe_csv_cell('Cliente normal'), 'Cliente normal')
        self.assertEqual(safe_csv_cell(12345), 12345)
        self.assertEqual(
            safe_csv_row(['=formula', 'normal', 100]),
            ["'=formula", 'normal', 100],
        )
