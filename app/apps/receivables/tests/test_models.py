from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from app.apps.sellers.validators import validate_cnpj

from ..models import Boleto
from .helpers import boleto_data, make_seller, make_tenant


class BoletoModelTests(TestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.user, self.seller = make_seller(self.tenant)

    def test_valid_cnpj(self):
        self.assertEqual(validate_cnpj('11.222.333/0001-81'), '11222333000181')

    def test_invalid_cnpj(self):
        with self.assertRaisesRegex(ValueError, 'CNPJ invalido'):
            validate_cnpj('11.111.111/1111-11')

    def test_clean_normalizes_document_and_address(self):
        boleto = Boleto(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.user,
            **boleto_data(
                payer_document='529.982.247-25',
                payer_phone='(31) 98888-7777',
                payer_zip_code='30110-000',
                payer_state='mg',
            ),
        )
        boleto.full_clean()
        self.assertEqual(boleto.payer_document, '52998224725')
        self.assertEqual(boleto.payer_phone, '31988887777')
        self.assertEqual(boleto.payer_zip_code, '30110000')
        self.assertEqual(boleto.payer_state, 'MG')

    def test_due_date_must_be_tomorrow_to_180_days(self):
        boleto = Boleto(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.user,
            **boleto_data(due_date=timezone.localdate()),
        )
        with self.assertRaises(ValidationError):
            boleto.full_clean()
        boleto.due_date = timezone.localdate() + timedelta(days=181)
        with self.assertRaises(ValidationError):
            boleto.full_clean()
