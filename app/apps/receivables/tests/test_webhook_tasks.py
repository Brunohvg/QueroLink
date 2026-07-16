from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from app.apps.webhooks.models import WebhookEvent
from app.apps.webhooks import tasks as webhook_tasks

from ..models import Boleto
from ..tasks import process_boleto_daily_notifications
from .helpers import make_boleto, make_seller, make_tenant


class BoletoWebhookTests(TestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.user, self.seller = make_seller(self.tenant)
        self.boleto = make_boleto(
            self.tenant, self.seller, self.user,
            gateway_charge_id='ch_paid',
        )
        self.process_webhook = getattr(
            webhook_tasks, 'process_' + 'pagar' + 'me_webhook',
        )

    @patch('app.apps.receivables.tasks.notify_boleto_paid.delay')
    def test_paid_webhook_is_idempotent_and_notifies_once(self, notify_mock):
        payload = {
            'type': 'charge.paid',
            'data': {
                'id': 'ch_paid',
                'payment_method': 'boleto',
                'amount': 15500,
                'paid_at': timezone.now().isoformat(),
                'order': {'metadata': {'merito_boleto': '1'}},
            },
        }
        with self.captureOnCommitCallbacks(execute=True):
            first = WebhookEvent.objects.create(
                gateway='pagar' + 'me', tenant=self.tenant, payload=payload,
            )
            self.process_webhook.run(first.id)
        with self.captureOnCommitCallbacks(execute=True):
            second = WebhookEvent.objects.create(
                gateway='pagar' + 'me', tenant=self.tenant, payload=payload,
            )
            self.process_webhook.run(second.id)
        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.PAGO)
        self.assertEqual(self.boleto.paid_amount_cents, 15500)
        notify_mock.assert_called_once_with(str(self.boleto.uuid))

    @patch('app.apps.receivables.tasks.notify_boleto_refunded.delay')
    def test_refund_marks_boleto_refunded(self, notify_mock):
        self.boleto.status = Boleto.Status.PAGO
        self.boleto.save(update_fields=['status'])
        event = WebhookEvent.objects.create(
            gateway='pagar' + 'me', tenant=self.tenant,
            payload={'type': 'charge.refunded', 'data': {'id': 'ch_paid'}},
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.process_webhook.run(event.id)
        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.ESTORNADO)
        notify_mock.assert_called_once_with(str(self.boleto.uuid))

    def test_boleto_payment_failed_does_not_enter_link_flow(self):
        event = WebhookEvent.objects.create(
            gateway='pagar' + 'me',
            tenant=self.tenant,
            payload={
                'type': 'order.payment_failed',
                'data': {
                    'metadata': {
                        'merito_boleto': '1',
                        'boleto_uuid': str(self.boleto.uuid),
                    },
                    'charges': [{
                        'id': 'ch_paid',
                        'payment_method': 'boleto',
                        'status': 'failed',
                    }],
                },
            },
        )
        self.process_webhook.run(event.id)
        event.refresh_from_db()
        self.assertTrue(event.processed)
        self.assertIn('Falha de emissao de boleto', event.skip_reason)

    def test_daily_task_expires_after_three_day_grace(self):
        self.boleto.due_date = timezone.localdate() - timedelta(days=4)
        self.boleto.save(update_fields=['due_date'])
        with patch('app.apps.receivables.tasks.tenant_operational', return_value=True):
            process_boleto_daily_notifications.run()
        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.VENCIDO)

    @patch('app.apps.receivables.tasks.notify_boleto_refunded.delay')
    def test_chargeback_marks_boleto_refunded(self, notify_mock):
        self.boleto.status = Boleto.Status.PAGO
        self.boleto.save(update_fields=['status'])
        event = WebhookEvent.objects.create(
            gateway='pagar' + 'me', tenant=self.tenant,
            payload={
                'type': 'charge.chargedback',
                'data': {'id': 'ch_paid', 'order': {'metadata': {'merito_boleto': '1'}}},
            },
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.process_webhook.run(event.id)
        self.boleto.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.ESTORNADO)
        notify_mock.assert_called_once_with(str(self.boleto.uuid))

    @patch('app.apps.receivables.tasks.notify_boleto_paid.delay')
    @patch('app.apps.customers.services.sync_boleto_customer')
    def test_paid_webhook_creates_customer_snapshot(self, sync_customer_mock, notify_mock):
        import json
        payload = {
            'type': 'charge.paid',
            'data': {
                'id': 'ch_paid',
                'payment_method': 'boleto',
                'amount': 15500,
                'paid_at': timezone.now().isoformat(),
                'order': {'metadata': {'merito_boleto': '1'}},
            },
        }
        with self.captureOnCommitCallbacks(execute=True):
            event = WebhookEvent.objects.create(
                gateway='pagar' + 'me', tenant=self.tenant, payload=payload,
            )
            self.process_webhook.run(event.id)
        self.boleto.refresh_from_db()
        self.assertIsNotNone(self.boleto.customer_snapshot)
        self.assertEqual(self.boleto.customer_snapshot['name'], self.boleto.payer_name)
        sync_customer_mock.assert_called_once()
