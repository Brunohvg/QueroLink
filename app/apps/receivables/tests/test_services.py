from datetime import timedelta
from unittest.mock import Mock, patch

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import Boleto
from app.apps.receivables.providers import (
    ProviderDefinitiveError,
    ProviderInconclusiveError,
    ProviderResult,
    ProviderStatus,
)
from app.apps.receivables.services import BoletoServiceError, create_boleto
from app.apps.receivables.services import IdempotencyConflictError
from app.apps.sellers.models import Seller


class CreateBoletoTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Provider Tenant')
        self.user = User.objects.create_user(
            username='provider-manager', tenant=self.tenant, role=User.Role.MANAGER,
        )
        seller_user = User.objects.create_user(
            username='provider-seller', tenant=self.tenant, role=User.Role.SELLER,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=seller_user,
            name='Seller',
            phone='11999999999',
        )

    def boleto_data(self):
        return {
            'payer_name': 'Maria da Silva',
            'payer_document': '52998224725',
            'payer_document_type': Boleto.DocumentType.CPF,
            'payer_email': 'maria@example.com',
            'payer_phone': '11999999999',
            'payer_zip_code': '01310100',
            'payer_street': 'Avenida Paulista',
            'payer_number': '1000',
            'payer_complement': '',
            'payer_neighborhood': 'Bela Vista',
            'payer_city': 'Sao Paulo',
            'payer_state': 'SP',
            'amount_cents': 15000,
            'due_date': timezone.localdate() + timedelta(days=10),
            'instructions': 'Pagamento ate o vencimento.',
        }

    def provider(self, result=None, side_effect=None):
        provider = Mock()
        provider.create.side_effect = side_effect
        provider.create.return_value = result or ProviderResult(
            provider='PAGARME',
            order_id='or_123',
            charge_id='ch_123',
            status=ProviderStatus.PENDING,
        )
        return provider

    @patch('app.apps.receivables.services.get_provider')
    def test_successful_creation_calls_provider_outside_atomic(self, get_provider):
        provider = self.provider()
        atomic_states = []

        def create(*args):
            atomic_states.append(connection.in_atomic_block)
            return provider.create.return_value

        provider.create.side_effect = create
        get_provider.return_value = provider

        boleto = create_boleto(
            self.tenant, self.seller, self.user, self.boleto_data(), 'stable-key'
        )

        self.assertEqual(atomic_states, [False])
        self.assertEqual(boleto.status, Boleto.Status.PENDENTE)
        self.assertEqual(boleto.provider_order_id, 'or_123')
        self.assertEqual(boleto.provider_charge_id, 'ch_123')

    @patch('app.apps.receivables.services.get_provider')
    def test_retry_with_same_key_does_not_emit_again(self, get_provider):
        provider = self.provider()
        get_provider.return_value = provider

        first = create_boleto(
            self.tenant, self.seller, self.user, self.boleto_data(), 'same-key'
        )
        second = create_boleto(
            self.tenant, self.seller, self.user, self.boleto_data(), 'same-key'
        )

        self.assertEqual(first.pk, second.pk)
        provider.create.assert_called_once()

    @patch('app.apps.receivables.services.get_provider')
    def test_two_tenants_can_use_same_key(self, get_provider):
        get_provider.return_value = self.provider()
        first = create_boleto(
            self.tenant, self.seller, self.user, self.boleto_data(), 'shared-key'
        )
        other_tenant = Tenant.objects.create(company_name='Other Provider Tenant')
        other_user = User.objects.create_user(
            username='other-provider-manager',
            tenant=other_tenant,
            role=User.Role.MANAGER,
        )
        other_seller_user = User.objects.create_user(
            username='other-provider-seller',
            tenant=other_tenant,
            role=User.Role.SELLER,
        )
        other_seller = Seller.objects.create(
            tenant=other_tenant,
            user=other_seller_user,
            name='Other Seller',
            phone='21999999999',
        )

        second = create_boleto(
            other_tenant,
            other_seller,
            other_user,
            self.boleto_data(),
            'shared-key',
        )

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(get_provider.return_value.create.call_count, 2)

    @patch('app.apps.receivables.services.get_provider')
    def test_timeout_keeps_reconcilable_record_and_retry_does_not_emit(self, get_provider):
        provider = self.provider(side_effect=ProviderInconclusiveError('timeout'))
        get_provider.return_value = provider

        with self.assertRaises(BoletoServiceError) as error:
            create_boleto(
                self.tenant, self.seller, self.user, self.boleto_data(), 'timeout-key'
            )

        boleto = error.exception.boleto
        self.assertEqual(boleto.status, Boleto.Status.CRIANDO)
        self.assertEqual(boleto.operation_error_code, 'provider_inconclusive')
        retry = create_boleto(
            self.tenant, self.seller, self.user, self.boleto_data(), 'timeout-key'
        )
        self.assertEqual(retry.pk, boleto.pk)
        provider.create.assert_called_once()

    @patch('app.apps.receivables.services.get_provider')
    def test_definitive_error_marks_record_failed(self, get_provider):
        get_provider.return_value = self.provider(
            side_effect=ProviderDefinitiveError('rejected')
        )

        with self.assertRaises(BoletoServiceError) as error:
            create_boleto(
                self.tenant, self.seller, self.user, self.boleto_data(), 'failed-key'
            )

        self.assertEqual(error.exception.boleto.status, Boleto.Status.FALHOU)
        self.assertEqual(
            error.exception.boleto.operation_error_code, 'provider_definitive'
        )

    @patch('app.apps.receivables.services.get_provider')
    def test_result_without_ids_marks_record_failed(self, get_provider):
        get_provider.return_value = self.provider(result=ProviderResult(
            provider='PAGARME',
            order_id='',
            charge_id='',
            status=ProviderStatus.UNKNOWN,
        ))

        with self.assertRaises(BoletoServiceError) as error:
            create_boleto(
                self.tenant, self.seller, self.user, self.boleto_data(), 'missing-ids'
            )

        self.assertEqual(error.exception.boleto.status, Boleto.Status.FALHOU)

    @patch('app.apps.receivables.services.get_provider')
    def test_barcode_digitable_line_and_url_persisted(self, get_provider):
        provider = self.provider()
        provider.create.return_value = ProviderResult(
            provider='PAGARME',
            order_id='or_barcode',
            charge_id='ch_barcode',
            status=ProviderStatus.PENDING,
            barcode='12345678901234567890123456789012345678901234',
            digitable_line='12345.67890 12345.678901 1 12345678901234',
            url='https://pagarme.me/boleto/test',
        )
        get_provider.return_value = provider
        boleto = create_boleto(
            self.tenant, self.seller, self.user,
            self.boleto_data(), 'barcode-key',
        )
        self.assertEqual(
            boleto.provider_barcode,
            '12345678901234567890123456789012345678901234',
        )
        self.assertEqual(
            boleto.provider_url, 'https://pagarme.me/boleto/test',
        )
        self.assertEqual(
            boleto.provider_digitable_line,
            '12345.67890 12345.678901 1 12345678901234',
        )

    @patch('app.apps.receivables.services.get_provider')
    def test_same_key_different_payload_raises_error(self, get_provider):
        provider = self.provider()
        get_provider.return_value = provider
        create_boleto(
            self.tenant, self.seller, self.user,
            self.boleto_data(), 'conflict-key',
        )
        diff_data = dict(self.boleto_data())
        diff_data['amount_cents'] = 99999
        with self.assertRaises(IdempotencyConflictError):
            create_boleto(
                self.tenant, self.seller, self.user,
                diff_data, 'conflict-key',
            )
        self.assertEqual(provider.create.call_count, 1)

    @patch('app.apps.receivables.services.get_provider')
    def test_same_key_different_seller_raises_without_provider_call(self, get_provider):
        provider = self.provider()
        get_provider.return_value = provider
        create_boleto(
            self.tenant, self.seller, self.user,
            self.boleto_data(), 'seller-conflict-key',
        )
        other_user = User.objects.create_user(
            username='provider-seller-two', tenant=self.tenant,
            role=User.Role.SELLER,
        )
        other_seller = Seller.objects.create(
            tenant=self.tenant, user=other_user, name='Seller Two',
            phone='11988888888',
        )

        with self.assertRaises(IdempotencyConflictError):
            create_boleto(
                self.tenant, other_seller, self.user,
                self.boleto_data(), 'seller-conflict-key',
            )

        self.assertEqual(provider.create.call_count, 1)
