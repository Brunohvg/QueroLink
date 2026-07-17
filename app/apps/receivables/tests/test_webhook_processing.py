from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import Boleto, IntegrationOutbox
from app.apps.sellers.models import Seller
from app.apps.webhooks.models import WebhookEvent
from app.apps.webhooks.services import process_receivable_pagarme_event


class ReceivableWebhookProcessingTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Webhook Boleto Tenant')
        self.manager = User.objects.create_user(
            username='webhook-boleto-manager', tenant=self.tenant,
            role=User.Role.MANAGER,
        )
        seller_user = User.objects.create_user(
            username='webhook-boleto-seller', tenant=self.tenant,
            role=User.Role.SELLER,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=seller_user, name='Seller', phone='11999999999'
        )
        self.boleto = Boleto.objects.create(
            tenant=self.tenant, seller=self.seller, created_by=self.manager,
            payer_name='Maria', payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF, payer_phone='11999999999',
            payer_zip_code='01310100', payer_street='Rua A', payer_number='1',
            payer_neighborhood='Centro', payer_city='Sao Paulo', payer_state='SP',
            amount_cents=10000, due_date=timezone.localdate() + timedelta(days=10),
            idempotency_key='webhook-key', provider_order_id='or_webhook',
            provider_charge_id='ch_webhook', status=Boleto.Status.PENDENTE,
        )

    def event(self, event_type, *, status='', amount=None):
        data = {
            'id': self.boleto.provider_charge_id,
            'status': status,
            'metadata': {'boleto_uuid': str(self.boleto.uuid)},
        }
        if amount is not None:
            data['amount'] = amount
        return WebhookEvent.objects.create(
            gateway='pagarme', tenant=self.tenant,
            gateway_event_id=f'evt-{event_type}-{WebhookEvent.objects.count()}',
            payload={'type': event_type, 'data': data},
        )

    def test_paid_event_is_idempotent(self):
        event = self.event('charge.paid', status='paid', amount=10000)

        self.assertEqual(process_receivable_pagarme_event(event.id), 'processed')
        self.assertEqual(process_receivable_pagarme_event(event.id), 'duplicate')

        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.PAGO)
        self.assertEqual(self.boleto.paid_amount_cents, 10000)
        self.assertEqual(IntegrationOutbox.objects.filter(
            event_type='boleto.paid'
        ).count(), 1)

    def test_refund_and_chargeback_create_the_correct_event(self):
        self.boleto.status = Boleto.Status.PAGO
        self.boleto.paid_at = timezone.now()
        self.boleto.paid_amount_cents = 10000
        self.boleto.save()
        refund = self.event('charge.refunded', status='refunded', amount=10000)
        process_receivable_pagarme_event(refund.id)

        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.ESTORNADO)
        self.assertTrue(IntegrationOutbox.objects.filter(
            event_type='boleto.refunded'
        ).exists())

        other = Boleto.objects.create(
            tenant=self.tenant, seller=self.seller, created_by=self.manager,
            payer_name='Joao', payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF, payer_phone='11999999999',
            payer_zip_code='01310100', payer_street='Rua B', payer_number='2',
            payer_neighborhood='Centro', payer_city='Sao Paulo', payer_state='SP',
            amount_cents=5000, due_date=timezone.localdate() + timedelta(days=10),
            idempotency_key='chargeback-key', provider_charge_id='ch_chargeback',
            status=Boleto.Status.PAGO, paid_at=timezone.now(), paid_amount_cents=5000,
        )
        event = WebhookEvent.objects.create(
            gateway='pagarme', tenant=self.tenant,
            payload={'type': 'charge.chargedback', 'data': {
                'id': 'ch_chargeback', 'status': 'refunded', 'amount': 5000,
                'metadata': {'boleto_uuid': str(other.uuid)},
            }},
        )
        process_receivable_pagarme_event(event.id)
        self.assertTrue(IntegrationOutbox.objects.filter(
            event_type='boleto.chargeback'
        ).exists())

    def test_payment_failed_marks_pending_boleto_failed(self):
        event = self.event('charge.payment_failed', status='failed')
        process_receivable_pagarme_event(event.id)

        self.boleto.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.FALHOU)
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)
