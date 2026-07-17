from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock, patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import Boleto, IntegrationOutbox
from app.apps.receivables.providers import (
    ProviderDefinitiveError,
    ProviderInconclusiveError,
    ProviderResult,
    ProviderStatus,
)
from app.apps.receivables.services import (
    BoletoServiceError,
    cancel_boleto,
    mark_paid,
    mark_refunded,
)
from app.apps.receivables.tasks import OUTBOX_HANDLERS, process_outbox_event
from app.apps.sellers.models import Seller


class LifecycleTests(TransactionTestCase):
    def setUp(self):
        OUTBOX_HANDLERS.clear()
        self.tenant = Tenant.objects.create(company_name='Lifecycle Tenant')
        self.user = User.objects.create_user(
            username='lifecycle-manager', tenant=self.tenant, role=User.Role.MANAGER
        )
        seller_user = User.objects.create_user(
            username='lifecycle-seller', tenant=self.tenant, role=User.Role.SELLER
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=seller_user, name='Seller', phone='11999999999'
        )
        self.boleto = self.make_boleto()

    def tearDown(self):
        OUTBOX_HANDLERS.clear()

    def make_boleto(self, status=Boleto.Status.PENDENTE, key='lifecycle-key'):
        return Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.user,
            payer_name='Maria',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='',
            payer_phone='11999999999',
            payer_zip_code='01310100',
            payer_street='Avenida Paulista',
            payer_number='1000',
            payer_neighborhood='Bela Vista',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=15000,
            due_date=timezone.localdate() + timedelta(days=10),
            provider_order_id='or_123',
            provider_charge_id='ch_123',
            idempotency_key=key,
            status=status,
        )

    def test_duplicate_payment_creates_one_outbox_event(self):
        paid_at = timezone.now()
        first, created = mark_paid(self.boleto, 15000, paid_at)
        second, duplicate_created = mark_paid(self.boleto, 15000, paid_at)

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            IntegrationOutbox.objects.filter(event_type='boleto.paid').count(), 1
        )

    @patch('app.apps.receivables.services._outbox_event', side_effect=RuntimeError('down'))
    def test_financial_rollback_also_rolls_back_outbox(self, _outbox):
        with self.assertRaises(RuntimeError):
            mark_paid(self.boleto, 15000, timezone.now())

        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.PENDENTE)
        self.assertFalse(IntegrationOutbox.objects.exists())

    @patch('app.apps.receivables.services.get_provider')
    def test_cancel_timeout_remains_pending_for_reconciliation(self, get_provider):
        provider = Mock()
        provider.request_cancel.side_effect = ProviderInconclusiveError('timeout\ntoken=x')
        get_provider.return_value = provider

        with self.assertRaises(BoletoServiceError):
            cancel_boleto(self.boleto)

        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.CANCEL_PEND)
        self.assertNotIn('\n', self.boleto.operation_error_message)

    @patch('app.apps.receivables.services.get_provider')
    def test_definitive_cancel_error_returns_to_pending(self, get_provider):
        provider = Mock()
        provider.request_cancel.side_effect = ProviderDefinitiveError('rejected')
        get_provider.return_value = provider

        with self.assertRaises(BoletoServiceError):
            cancel_boleto(self.boleto)

        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.PENDENTE)
        self.assertEqual(self.boleto.operation_error_code, 'provider_definitive')

    @patch('app.apps.receivables.services.get_provider')
    def test_confirmed_cancel_creates_outbox_outside_provider_call(self, get_provider):
        provider = Mock()
        atomic_states = []

        def request_cancel(*args):
            atomic_states.append(connection.in_atomic_block)
            return ProviderResult('PAGARME', 'or_123', 'ch_123', ProviderStatus.CANCELED)

        provider.request_cancel.side_effect = request_cancel
        get_provider.return_value = provider
        canceled, created = cancel_boleto(self.boleto)

        self.assertTrue(created)
        self.assertEqual(canceled.status, Boleto.Status.CANCELADO)
        self.assertEqual(atomic_states, [False])
        self.assertEqual(
            IntegrationOutbox.objects.filter(event_type='boleto.canceled').count(), 1
        )

    def test_duplicate_refund_preserves_payment_and_creates_one_event(self):
        paid_at = timezone.now() - timedelta(days=1)
        self.boleto.status = Boleto.Status.PAGO
        self.boleto.paid_at = paid_at
        self.boleto.paid_amount_cents = 15000
        self.boleto.save()

        refunded, created = mark_refunded(self.boleto, reason='requested')
        duplicate, duplicate_created = mark_refunded(self.boleto, reason='requested')

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(refunded.paid_at, paid_at)
        self.assertEqual(duplicate.paid_amount_cents, 15000)
        self.assertEqual(
            IntegrationOutbox.objects.filter(event_type='boleto.refunded').count(), 1
        )

    def test_consumer_error_does_not_change_paid_boleto(self):
        paid_at = timezone.now()
        paid, _ = mark_paid(self.boleto, 15000, paid_at)
        event = IntegrationOutbox.objects.get(event_type='boleto.paid')
        event.status = IntegrationOutbox.Status.PROCESSING
        event.save(update_fields=['status'])
        OUTBOX_HANDLERS['boleto.paid'] = lambda outbox: (_ for _ in ()).throw(
            RuntimeError('consumer failed')
        )

        with self.assertRaises(RuntimeError):
            process_outbox_event(str(event.pk))

        paid.refresh_from_db()
        self.assertEqual(paid.status, Boleto.Status.PAGO)
        self.assertEqual(paid.paid_at, paid_at)
        self.assertEqual(paid.paid_amount_cents, 15000)

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_simultaneous_payments_have_one_effect(self):
        def pay():
            close_old_connections()
            try:
                return mark_paid(
                    Boleto.objects.get(pk=self.boleto.pk), 15000, timezone.now()
                )[1]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: pay(), range(2)))

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(IntegrationOutbox.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    @patch('app.apps.receivables.services.get_provider')
    def test_simultaneous_cancel_and_payment_never_overwrites_payment(self, get_provider):
        provider = Mock()
        provider.request_cancel.return_value = ProviderResult(
            'PAGARME', 'or_123', 'ch_123', ProviderStatus.CANCELED
        )
        get_provider.return_value = provider

        def run(operation):
            close_old_connections()
            try:
                current = Boleto.objects.get(pk=self.boleto.pk)
                if operation == 'pay':
                    try:
                        mark_paid(current, 15000, timezone.now())
                    except BoletoServiceError:
                        pass
                else:
                    try:
                        cancel_boleto(current)
                    except BoletoServiceError:
                        pass
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(run, ['cancel', 'pay']))

        self.boleto.refresh_from_db()
        self.assertIn(self.boleto.status, [Boleto.Status.PAGO, Boleto.Status.CANCELADO])
        if self.boleto.paid_at is not None:
            self.assertEqual(self.boleto.status, Boleto.Status.PAGO)
