import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.orders.models import Order, PaymentLink
from app.apps.payments.models import Payment
from app.apps.sales.models import Sale
from app.apps.webhooks.models import WebhookEvent

MockNow = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.get_current_timezone())


class BaseWebhookTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Test Empresa',
            slug='test-empresa',
            is_active=True,
        )
        self.seller_user = User.objects.create_user(
            username='seller1', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Bruno',
            phone='55999999999',
            is_active=True,
        )
        self.order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Cliente Teste',
            total_amount=15000,
            status=Order.Status.PENDING,
        )
        self.payment = Payment.objects.create(
            order=self.order,
            gateway_name='pagarme',
            status=Payment.Status.PENDING,
        )
        self.payment_link = PaymentLink.objects.create(
            order=self.order,
            gateway_url='https://pagar.me/link/test',
            gateway_link_id='pl_test123',
        )

    def _create_event(self, payload):
        event = WebhookEvent.objects.create(
            gateway='pagarme',
            payload=payload,
            tenant=self.tenant,
        )
        return event

    def _run_task(self, event):
        from app.apps.webhooks.tasks import process_pagarme_webhook
        with patch('app.apps.notifications.tasks.create_and_send_notification', return_value=None):
            process_pagarme_webhook(event.id)


class TestOrderPaidCorrelation(BaseWebhookTest):
    def test_order_paid_correlates_by_code(self):
        payload = {
            'type': 'order.paid',
            'data': {
                'id': 'or_test123',
                'code': str(self.order.uuid),
                'charges': [{
                    'id': 'ch_test1',
                    'payment_method': 'credit_card',
                    'paid_at': '2026-07-03T12:00:00Z',
                    'last_transaction': {
                        'installments': 3,
                        'card': {
                            'brand': 'visa',
                            'last_four_digits': '4242',
                        },
                    },
                }],
            },
        }
        event = self._create_event(payload)
        self._run_task(event)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.payment.payment_method, 'credit_card')
        self.assertEqual(self.payment.installments, 3)
        self.assertEqual(self.payment.card_brand, 'visa')
        self.assertEqual(self.payment.card_last4, '4242')

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.COMPLETED)

        sale = Sale.objects.filter(order=self.order).first()
        self.assertIsNotNone(sale)
        self.assertEqual(sale.origin, Sale.Origin.LINK)
        self.assertEqual(sale.amount, 15000)

    def test_order_paid_unknown_code_skips(self):
        payload = {
            'type': 'order.paid',
            'data': {
                'id': 'or_unknown',
                'code': '00000000-0000-0000-0000-000000000000',
                'charges': [{'id': 'ch_unknown'}],
            },
        }
        event = self._create_event(payload)
        self._run_task(event)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PENDING)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

        event.refresh_from_db()
        self.assertTrue(event.processed)


class TestPaymentLinkFinished(BaseWebhookTest):
    def test_payment_link_finished_does_not_overwrite_charge_data(self):
        charge_payload = {
            'type': 'charge.paid',
            'data': {
                'id': 'ch_test1',
                'payment_method': 'credit_card',
                'paid_at': '2026-07-03T12:00:00Z',
                'last_transaction': {
                    'installments': 3,
                    'card': {
                        'brand': 'visa',
                        'last_four_digits': '4242',
                    },
                },
                'order': {
                    'id': 'or_test123',
                    'payment_link': {'id': 'pl_test123'},
                },
            },
        }
        event1 = self._create_event(charge_payload)
        self._run_task(event1)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.payment.payment_method, 'credit_card')
        self.assertEqual(self.payment.installments, 3)
        self.assertEqual(self.payment.card_brand, 'visa')

        link_payload = {
            'type': 'payment-link.finished',
            'data': {
                'id': 'pl_test123',
            },
        }
        event2 = self._create_event(link_payload)
        self._run_task(event2)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.payment.payment_method, 'credit_card')
        self.assertEqual(self.payment.installments, 3)
        self.assertEqual(self.payment.card_brand, 'visa')


class TestWebhookAuth(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Auth Test',
            slug='auth-test',
            is_active=True,
        )

    def _post(self, data=None):
        return self.client.post(
            f'/api/webhooks/pagarme/{self.tenant.slug}/',
            data=data or {'type': 'test', 'data': {}},
            content_type='application/json',
        )

    @override_settings(WEBHOOK_AUTH_REQUIRED=True)
    def test_no_credentials_with_auth_required_returns_401(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 401)

    @override_settings(WEBHOOK_AUTH_REQUIRED=False)
    def test_no_credentials_with_auth_disabled_returns_200(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 200)

    @override_settings(WEBHOOK_AUTH_REQUIRED=True)
    def test_wrong_credentials_returns_401(self):
        self.tenant.pagarme_webhook_username = 'user'
        self.tenant.pagarme_webhook_password = 'pass'
        self.tenant.save(update_fields=['pagarme_webhook_username', 'pagarme_webhook_password'])

        import base64
        creds = base64.b64encode(b'user:wrongpass').decode()
        resp = self.client.post(
            f'/api/webhooks/pagarme/{self.tenant.slug}/',
            data={'type': 'test', 'data': {}},
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Basic {creds}',
        )
        self.assertEqual(resp.status_code, 401)

    @override_settings(WEBHOOK_AUTH_REQUIRED=True)
    def test_correct_credentials_returns_200(self):
        self.tenant.pagarme_webhook_username = 'user'
        self.tenant.pagarme_webhook_password = 'pass'
        self.tenant.save(update_fields=['pagarme_webhook_username', 'pagarme_webhook_password'])

        import base64
        creds = base64.b64encode(b'user:pass').decode()
        resp = self.client.post(
            f'/api/webhooks/pagarme/{self.tenant.slug}/',
            data={'type': 'test', 'data': {}},
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Basic {creds}',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(WebhookEvent.objects.filter(tenant=self.tenant).exists())


class TestChargeRefunded(BaseWebhookTest):
    def test_charge_refunded_updates_status(self):
        self.payment.status = Payment.Status.PAID
        self.payment.payment_method = 'credit_card'
        self.payment.gateway_transaction_id = 'ch_refund1'
        self.payment.save()

        Sale.objects.create(
            order=self.order,
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.LINK,
            amount=15000,
            sale_date=timezone.localdate(),
        )

        payload = {
            'type': 'charge.refunded',
            'data': {
                'id': 'ch_refund1',
            },
        }
        event = self._create_event(payload)
        self._run_task(event)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.REFUNDED)
