from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import Boleto
from app.apps.sellers.models import Seller


class BoletoModelTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Receivables Tenant')
        self.user = User.objects.create_user(
            username='receivables-manager',
            tenant=self.tenant,
            role=User.Role.MANAGER,
        )
        seller_user = User.objects.create_user(
            username='receivables-seller',
            tenant=self.tenant,
            role=User.Role.SELLER,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=seller_user,
            name='Seller',
            phone='11999999999',
        )

    def make_boleto(self, **overrides):
        data = {
            'tenant': self.tenant,
            'seller': self.seller,
            'created_by': self.user,
            'payer_name': 'Maria da Silva',
            'payer_document': '529.982.247-25',
            'payer_document_type': Boleto.DocumentType.CPF,
            'payer_email': 'maria@example.com',
            'payer_phone': '(11) 99999-9999',
            'payer_zip_code': '01310-100',
            'payer_street': 'Avenida Paulista',
            'payer_number': '1000',
            'payer_complement': 'Sala 1',
            'payer_neighborhood': 'Bela Vista',
            'payer_city': 'Sao Paulo',
            'payer_state': 'sp',
            'amount_cents': 15000,
            'due_date': timezone.localdate() + timedelta(days=10),
            'idempotency_key': 'test-key',
        }
        data.update(overrides)
        return Boleto(**data)

    def test_valid_boleto_normalizes_fields(self):
        boleto = self.make_boleto()

        boleto.full_clean()

        self.assertEqual(boleto.payer_document, '52998224725')
        self.assertEqual(boleto.payer_phone, '11999999999')
        self.assertEqual(boleto.payer_zip_code, '01310100')
        self.assertEqual(boleto.payer_state, 'SP')

    def test_valid_cnpj(self):
        boleto = self.make_boleto(
            payer_document='11.222.333/0001-81',
            payer_document_type=Boleto.DocumentType.CNPJ,
        )

        boleto.full_clean()

        self.assertEqual(boleto.payer_document, '11222333000181')

    def test_invalid_cpf_and_cnpj(self):
        for document_type, document in (
            (Boleto.DocumentType.CPF, '111.111.111-11'),
            (Boleto.DocumentType.CNPJ, '11.111.111/1111-11'),
        ):
            with self.subTest(document_type=document_type):
                boleto = self.make_boleto(
                    payer_document=document,
                    payer_document_type=document_type,
                )
                with self.assertRaises(ValidationError) as error:
                    boleto.full_clean()
                self.assertIn('payer_document', error.exception.message_dict)

    def test_amount_must_be_positive(self):
        boleto = self.make_boleto(amount_cents=0)

        with self.assertRaises(ValidationError) as error:
            boleto.full_clean()

        self.assertIn('amount_cents', error.exception.message_dict)

    def test_due_date_must_be_between_tomorrow_and_180_days(self):
        for due_date in (
            timezone.localdate(),
            timezone.localdate() + timedelta(days=181),
        ):
            with self.subTest(due_date=due_date):
                boleto = self.make_boleto(due_date=due_date)
                with self.assertRaises(ValidationError) as error:
                    boleto.full_clean()
                self.assertIn('due_date', error.exception.message_dict)

    def test_phone_must_have_10_or_11_digits(self):
        boleto = self.make_boleto(payer_phone='119999999')

        with self.assertRaises(ValidationError) as error:
            boleto.full_clean()

        self.assertIn('payer_phone', error.exception.message_dict)

    def test_zip_code_must_have_eight_digits(self):
        boleto = self.make_boleto(payer_zip_code='01310')

        with self.assertRaises(ValidationError) as error:
            boleto.full_clean()

        self.assertIn('payer_zip_code', error.exception.message_dict)

    def test_seller_and_creator_must_belong_to_tenant(self):
        other_tenant = Tenant.objects.create(company_name='Other Tenant')
        other_user = User.objects.create_user(
            username='other-manager',
            tenant=other_tenant,
            role=User.Role.MANAGER,
        )
        other_seller_user = User.objects.create_user(
            username='other-seller',
            tenant=other_tenant,
            role=User.Role.SELLER,
        )
        other_seller = Seller.objects.create(
            tenant=other_tenant,
            user=other_seller_user,
            name='Other Seller',
            phone='21999999999',
        )

        boleto = self.make_boleto(seller=other_seller, created_by=other_user)
        with self.assertRaises(ValidationError) as error:
            boleto.full_clean()

        self.assertIn('seller', error.exception.message_dict)
        self.assertIn('created_by', error.exception.message_dict)

    def test_valid_and_invalid_status_transitions(self):
        boleto = self.make_boleto()

        boleto.transition_to(Boleto.Status.PENDENTE)
        boleto.transition_to(Boleto.Status.PAGO)
        boleto.transition_to(Boleto.Status.ESTORNADO)
        self.assertEqual(boleto.status, Boleto.Status.ESTORNADO)

        with self.assertRaises(ValidationError):
            boleto.transition_to(Boleto.Status.PENDENTE)

    def test_paid_cannot_regress_to_pending(self):
        boleto = self.make_boleto(status=Boleto.Status.PAGO)

        with self.assertRaises(ValidationError):
            boleto.transition_to(Boleto.Status.PENDENTE)

    def test_idempotency_key_is_unique_inside_tenant(self):
        first = self.make_boleto()
        first.full_clean()
        first.save()
        duplicate = self.make_boleto()
        duplicate.full_clean(exclude={'idempotency_key'})

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                duplicate.save()

    def test_two_tenants_can_use_same_idempotency_key(self):
        first = self.make_boleto()
        first.full_clean()
        first.save()

        other_tenant = Tenant.objects.create(company_name='Second Tenant')
        other_user = User.objects.create_user(
            username='second-manager',
            tenant=other_tenant,
            role=User.Role.MANAGER,
        )
        other_seller_user = User.objects.create_user(
            username='second-seller',
            tenant=other_tenant,
            role=User.Role.SELLER,
        )
        other_seller = Seller.objects.create(
            tenant=other_tenant,
            user=other_seller_user,
            name='Second Seller',
            phone='31999999999',
        )
        second = self.make_boleto(
            tenant=other_tenant,
            seller=other_seller,
            created_by=other_user,
        )

        second.full_clean()
        second.save()

        self.assertNotEqual(first.tenant_id, second.tenant_id)

    def test_payer_personal_data_is_encrypted_at_rest(self):
        boleto = self.make_boleto()
        boleto.full_clean()
        boleto.save()

        with self.connection.cursor() as cursor:
            cursor.execute(
                'SELECT payer_name, payer_document FROM receivables_boleto WHERE uuid = %s',
                [boleto.uuid],
            )
            stored_name, stored_document = cursor.fetchone()

        self.assertNotEqual(stored_name, 'Maria da Silva')
        self.assertNotEqual(stored_document, '52998224725')

    @property
    def connection(self):
        from django.db import connection

        return connection

    def test_operation_error_message_is_sanitized(self):
        boleto = self.make_boleto()

        boleto.set_operation_error('REMOTE_ERROR', 'gateway\nfailed\x00now')

        self.assertEqual(boleto.operation_error_code, 'REMOTE_ERROR')
        self.assertEqual(boleto.operation_error_message, 'gateway failed now')
