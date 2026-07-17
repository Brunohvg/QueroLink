from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import Boleto, IntegrationOutbox
from app.apps.receivables.providers import ProviderResult, ProviderStatus
from app.apps.receivables.tasks import reconcile_pending_boletos
from app.apps.sellers.models import Seller


@override_settings(
    RECEIVABLES_RECONCILE_BATCH_SIZE=100,
    RECEIVABLES_RECONCILE_CREATING_MIN_AGE_MINUTES=10,
    RECEIVABLES_RECONCILE_STALE_MINUTES=60,
)
class ReconcilePendingBoletosTests(TestCase):
    def setUp(self):
        self.tenant = self.make_tenant('reconcile', enabled=True, credential='key')
        self.boleto = self.make_boleto(self.tenant, Boleto.Status.PENDENTE, 'one')

    def make_tenant(self, slug, *, enabled, credential=''):
        tenant = Tenant.objects.create(
            company_name=slug, slug=slug, receivables_enabled=enabled,
            pagarme_api_key=credential,
        )
        manager = User.objects.create_user(
            username=f'{slug}-manager', tenant=tenant, role=User.Role.MANAGER,
        )
        seller_user = User.objects.create_user(
            username=f'{slug}-seller', tenant=tenant, role=User.Role.SELLER,
        )
        tenant.test_manager = manager
        tenant.test_seller = Seller.objects.create(
            tenant=tenant, user=seller_user, name='Seller', phone='11999999999',
        )
        return tenant

    def make_boleto(self, tenant, status, key):
        boleto = Boleto.objects.create(
            tenant=tenant, seller=tenant.test_seller, created_by=tenant.test_manager,
            payer_name='Maria', payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF, payer_phone='11999999999',
            payer_zip_code='01310100', payer_street='Rua A', payer_number='1',
            payer_neighborhood='Centro', payer_city='Sao Paulo', payer_state='SP',
            amount_cents=10000, due_date=timezone.localdate() + timedelta(days=10),
            idempotency_key=key, provider_order_id=f'or-{key}',
            provider_charge_id=f'ch-{key}', status=status,
        )
        Boleto.objects.filter(pk=boleto.pk).update(
            created_at=timezone.now() - timedelta(hours=2),
            last_synced_at=timezone.now() - timedelta(hours=2),
        )
        boleto.refresh_from_db()
        return boleto

    def provider(self, result=None, side_effect=None):
        provider = Mock()
        provider.retrieve_status.side_effect = side_effect
        provider.retrieve_status.return_value = result or ProviderResult(
            'PAGARME', 'or-one', 'ch-one', ProviderStatus.PENDING,
        )
        return provider

    @patch('app.apps.receivables.tasks.get_provider')
    def test_lost_webhook_payment_is_recovered_without_duplicate_outbox(self, get_provider):
        get_provider.return_value = self.provider(ProviderResult(
            'PAGARME', 'or-one', 'ch-one', ProviderStatus.PAID,
            paid_amount_cents=10000, paid_at=timezone.now(),
        ))

        reconcile_pending_boletos()
        Boleto.objects.filter(pk=self.boleto.pk).update(
            last_synced_at=timezone.now() - timedelta(hours=2)
        )
        reconcile_pending_boletos()

        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.PAGO)
        self.assertEqual(IntegrationOutbox.objects.filter(
            event_type='boleto.paid'
        ).count(), 1)

    @patch('app.apps.receivables.tasks.get_provider')
    def test_creating_timeout_is_found_by_local_code(self, get_provider):
        creating = self.make_boleto(self.tenant, Boleto.Status.CRIANDO, 'creating')
        creating.provider_order_id = ''
        creating.provider_charge_id = ''
        creating.save(update_fields=['provider_order_id', 'provider_charge_id'])
        provider = self.provider(ProviderResult(
            'PAGARME', 'or-found', 'ch-found', ProviderStatus.PENDING,
        ))
        get_provider.return_value = provider

        reconcile_pending_boletos()

        creating.refresh_from_db()
        self.assertEqual(creating.status, Boleto.Status.PENDENTE)
        self.assertEqual(creating.provider_charge_id, 'ch-found')
        self.assertIn(
            str(creating.uuid),
            [call.kwargs['local_code'] for call in provider.retrieve_status.call_args_list],
        )

    @patch('app.apps.receivables.tasks.get_provider')
    def test_creating_timeout_already_paid_is_recovered(self, get_provider):
        creating = self.make_boleto(self.tenant, Boleto.Status.CRIANDO, 'creating-paid')
        creating.provider_order_id = ''
        creating.provider_charge_id = ''
        creating.save(update_fields=['provider_order_id', 'provider_charge_id'])
        get_provider.return_value = self.provider(ProviderResult(
            'PAGARME', 'or-paid', 'ch-paid', ProviderStatus.PAID,
            paid_amount_cents=10000, paid_at=timezone.now(),
        ))

        reconcile_pending_boletos()

        creating.refresh_from_db()
        self.assertEqual(creating.status, Boleto.Status.PAGO)
        self.assertEqual(creating.provider_charge_id, 'ch-paid')

    @patch('app.apps.receivables.tasks.get_provider')
    def test_pending_cancel_is_confirmed(self, get_provider):
        self.boleto.status = Boleto.Status.CANCEL_PEND
        self.boleto.save(update_fields=['status'])
        get_provider.return_value = self.provider(ProviderResult(
            'PAGARME', 'or-one', 'ch-one', ProviderStatus.CANCELED,
        ))

        reconcile_pending_boletos()

        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.CANCELADO)
        self.assertEqual(IntegrationOutbox.objects.filter(
            event_type='boleto.canceled'
        ).count(), 1)

    @patch('app.apps.receivables.tasks.get_provider')
    def test_remote_pending_only_updates_sync(self, get_provider):
        get_provider.return_value = self.provider()
        reconcile_pending_boletos()
        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.PENDENTE)
        self.assertIsNotNone(self.boleto.last_synced_at)
        self.assertFalse(IntegrationOutbox.objects.exists())

    @patch('app.apps.receivables.tasks.get_provider')
    def test_disabled_or_uncredentialed_tenants_are_skipped(self, get_provider):
        disabled = self.make_tenant('disabled', enabled=False, credential='key')
        missing = self.make_tenant('missing', enabled=True, credential='')
        self.make_boleto(disabled, Boleto.Status.PENDENTE, 'disabled')
        self.make_boleto(missing, Boleto.Status.PENDENTE, 'missing')
        get_provider.return_value = self.provider()
        reconcile_pending_boletos()
        self.assertEqual(get_provider.call_count, 1)

    @patch('app.apps.receivables.tasks.get_provider')
    def test_remote_refund_is_applied_idempotently(self, get_provider):
        self.boleto.status = Boleto.Status.PAGO
        self.boleto.paid_at = timezone.now() - timedelta(days=1)
        self.boleto.paid_amount_cents = 10000
        self.boleto.save(update_fields=['status', 'paid_at', 'paid_amount_cents'])
        get_provider.return_value = self.provider(ProviderResult(
            'PAGARME', 'or-one', 'ch-one', ProviderStatus.REFUNDED,
            paid_amount_cents=10000, paid_at=self.boleto.paid_at,
        ))

        reconcile_pending_boletos()

        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.ESTORNADO)
        self.assertEqual(IntegrationOutbox.objects.filter(
            event_type='boleto.refunded'
        ).count(), 1)

    @patch('app.apps.receivables.tasks.get_provider')
    def test_failure_of_one_tenant_does_not_stop_next(self, get_provider):
        other = self.make_tenant('zz-other', enabled=True, credential='key')
        other_boleto = self.make_boleto(other, Boleto.Status.PENDENTE, 'other')
        paid = ProviderResult(
            'PAGARME', 'or-other', 'ch-other', ProviderStatus.PAID,
            paid_amount_cents=10000, paid_at=timezone.now(),
        )
        working_provider = self.provider(paid)

        def provider_for(tenant):
            if tenant.pk == self.tenant.pk:
                raise RuntimeError('tenant failure')
            return working_provider

        get_provider.side_effect = provider_for

        reconcile_pending_boletos()

        other_boleto.refresh_from_db()
        self.assertEqual(other_boleto.status, Boleto.Status.PAGO)

    @patch('app.apps.receivables.tasks.get_provider')
    def test_batch_limit_is_global(self, get_provider):
        self.make_boleto(self.tenant, Boleto.Status.PENDENTE, 'two')
        get_provider.return_value = self.provider()
        result = reconcile_pending_boletos(limit=1)
        self.assertEqual(result['processed'], 1)
        self.assertEqual(get_provider.return_value.retrieve_status.call_count, 1)
