from django.test import TestCase

from app.apps.accounts.utils import generate_temp_password


class GenerateTempPasswordTest(TestCase):
    def test_generates_readable_passwords_with_required_classes(self):
        forbidden = set('0Oo1lI')
        letters = set('ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz')
        digits = set('23456789')
        symbols = set('@#%')

        for _ in range(200):
            password = generate_temp_password()

            self.assertEqual(len(password), 10)
            self.assertFalse(forbidden.intersection(password))
            self.assertTrue(any(c in letters for c in password))
            self.assertTrue(any(c in digits for c in password))
            self.assertTrue(any(c in symbols for c in password))
