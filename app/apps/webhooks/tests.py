import json
from datetime import datetime, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.orders.models import Order, PaymentLink
from app.apps.payments.models import Payment
from app.apps.receivables.models import Boleto
from app.apps.sales.models import Sale
from app.apps.webhooks.models import WebhookEvent

MockNow = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.get_current_timezone())


@override_settings(WEBHOOK_AUTH_REQUIRED=False)
class ReceivablesRoutingTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Routing Tenant')
        self.manager = User.objects.create_user(
            username='routing-manager', tenant=self.tenant, role=User.Role.MANAGER
        )
        seller_user = User.objects.create_user(
            username='routing-seller', tenant=self.tenant, role=User.Role.SELLER
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
            idempotency_key='routing-key', provider_order_id='or_boleto',
            provider_charge_id='ch_boleto', status=Boleto.Status.PENDENTE,
        )
        self.url = f'/api/webhooks/pagarme/{self.tenant.slug}/'

    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    def test_boleto_event_is_persisted_and_enqueued(self, delay):
        payload = {
            'id': 'evt_boleto_paid',
            'type': 'charge.paid',
            'data': {
                'id': self.boleto.provider_charge_id,
                'status': 'paid',
                'amount': 10000,
                'metadata': {'boleto_uuid': str(self.boleto.uuid)},
            },
        }
        response = Client().post(
            self.url, data=json.dumps(payload), content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        event = WebhookEvent.objects.get(gateway_event_id='evt_boleto_paid')
        self.assertNotIn('customer', event.payload['data'])
        delay.assert_called_once_with(event.id)

    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    def test_foreign_boleto_method_without_local_correlation_stays_foreign(self, delay):
        response = Client().post(
            self.url,
            data=json.dumps({
                'id': 'evt_foreign_boleto',
                'type': 'charge.paid',
                'data': {
                    'id': 'ch_foreign',
                    'payment_method': 'boleto',
                    'status': 'paid',
                },
            }),
            content_type='application/json',
        )

        self.assertEqual(response.json()['status'], 'ignored_foreign')
        self.assertFalse(WebhookEvent.objects.filter(
            gateway_event_id='evt_foreign_boleto'
        ).exists())
        delay.assert_not_called()

    @patch('app.apps.webhooks.tasks.process_receivable_webhook.delay')
    def test_router_dispatches_correlated_boleto(self, delay):
        event = WebhookEvent.objects.create(
            gateway='pagarme', tenant=self.tenant,
            payload={
                'type': 'charge.paid',
                'data': {'id': 'ch_boleto', 'status': 'paid'},
            },
        )
        from app.apps.webhooks.tasks import process_pagarme_webhook

        process_pagarme_webhook(event.id)
        delay.assert_called_once_with(event.id)


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

        self.assertEqual(Sale.objects.filter(order=self.order).count(), 0)

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


class TestWebhookIdempotency(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Idempotency Test',
            slug='idempotency-test',
            is_active=True,
        )
        self.seller_user = User.objects.create_user(
            username='idempotency_seller',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Idempotency Seller',
            is_active=True,
        )
        self.order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Cliente Idempotency',
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
            gateway_url='https://pagar.me/link/idempotency',
            gateway_link_id='pl_idempotency',
        )
        self.url = f'/api/webhooks/pagarme/{self.tenant.slug}/'

    def _post(self, payload):
        return Client().post(
            self.url,
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _post_billing(self, payload):
        import hashlib
        import hmac

        data_id = payload.get('data', {}).get('id', '')
        request_id = f'req-{payload.get("id", "event")}'
        ts = '1704067200'
        manifest = f'id:{data_id};request-id:{request_id};ts:{ts};'
        v1 = hmac.new(
            b'test_secret_key',
            manifest.encode(),
            hashlib.sha256,
        ).hexdigest()
        return Client().post(
            f'/api/webhooks/billing/?data.id={data_id}&type=payment',
            data=json.dumps(payload).encode(),
            content_type='application/json',
            HTTP_X_SIGNATURE=f'ts={ts},v1={v1}',
            HTTP_X_REQUEST_ID=request_id,
        )

    @override_settings(WEBHOOK_AUTH_REQUIRED=False)
    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    def test_duplicate_event_keeps_original_payload(self, mock_process):
        first_payload = {
            'id': 'evt_duplicate',
            'type': 'order.paid',
            'data': {'id': 'or_1', 'code': str(self.order.uuid)},
        }
        second_payload = {
            'id': 'evt_duplicate',
            'type': 'order.paid',
            'data': {'id': 'or_2', 'code': str(self.order.uuid)},
        }

        first = self._post(first_payload)
        second = self._post(second_payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['status'], 'received')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['status'], 'received')
        self.assertEqual(
            WebhookEvent.objects.filter(
                gateway='pagarme',
                gateway_event_id='evt_duplicate',
            ).count(),
            1,
        )
        event = WebhookEvent.objects.get(
            gateway='pagarme',
            gateway_event_id='evt_duplicate',
        )
        self.assertEqual(event.payload['data']['id'], 'or_1')
        self.assertEqual(mock_process.call_count, 2)

    @override_settings(WEBHOOK_AUTH_REQUIRED=False, MP_WEBHOOK_SECRET='test_secret_key')
    @patch('app.apps.webhooks.tasks.process_billing_webhook.delay')
    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    def test_same_event_id_can_exist_in_different_gateways(self, mock_pagarme, mock_billing):
        pagarme_response = self._post({
            'id': 'evt_same',
            'type': 'order.paid',
            'data': {'id': 'or_same', 'code': str(self.order.uuid)},
        })
        billing_response = self._post_billing({
            'id': 'evt_same',
            'type': 'payment',
            'data': {'id': 'evt_same'},
        })

        self.assertEqual(pagarme_response.status_code, 200)
        self.assertEqual(billing_response.status_code, 200)
        self.assertEqual(
            WebhookEvent.objects.filter(gateway_event_id='evt_same').count(),
            2,
        )
        self.assertTrue(WebhookEvent.objects.filter(
            gateway='pagarme',
            gateway_event_id='evt_same',
        ).exists())
        self.assertTrue(WebhookEvent.objects.filter(
            gateway='mercadopago',
            gateway_event_id='evt_same',
        ).exists())
        self.assertEqual(mock_pagarme.call_count, 1)
        self.assertEqual(mock_billing.call_count, 1)

    @override_settings(MP_WEBHOOK_SECRET='test_secret_key')
    @patch('app.apps.webhooks.tasks.process_billing_webhook.delay')
    def test_mercadopago_duplicate_event_dedupes_and_keeps_original_payload(self, mock_process):
        first_payload = {'id': 'evt_mp_duplicate', 'type': 'payment', 'data': {'id': 'pay_1'}}
        second_payload = {'id': 'evt_mp_duplicate', 'type': 'payment', 'data': {'id': 'pay_2'}}

        first = self._post_billing(first_payload)
        second = self._post_billing(second_payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['status'], 'received')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['status'], 'duplicate')
        self.assertEqual(
            WebhookEvent.objects.filter(
                gateway='mercadopago',
                gateway_event_id='evt_mp_duplicate',
            ).count(),
            1,
        )
        event = WebhookEvent.objects.get(
            gateway='mercadopago',
            gateway_event_id='evt_mp_duplicate',
        )
        self.assertEqual(event.payload['data']['id'], 'pay_1')
        self.assertEqual(mock_process.call_count, 1)

    @override_settings(WEBHOOK_AUTH_REQUIRED=False)
    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    def test_concurrent_duplicate_requests_create_one_event(self, mock_process):
        payload = {
            'id': 'evt_concurrent',
            'type': 'order.paid',
            'data': {'id': 'or_1', 'code': str(self.order.uuid)},
        }

        def post_and_close(_i):
            try:
                return self._post(payload)
            finally:
                if connection.vendor != 'sqlite':
                    connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(post_and_close, range(2)))

        self.assertEqual(sorted(response.status_code for response in responses), [200, 200])
        self.assertEqual(
            WebhookEvent.objects.filter(
                gateway='pagarme',
                gateway_event_id='evt_concurrent',
            ).count(),
            1,
        )
        self.assertEqual(mock_process.call_count, 2)

    @override_settings(WEBHOOK_AUTH_REQUIRED=False)
    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    def test_event_without_external_id_is_accepted_without_integrity_error(self, mock_process):
        payload = {'type': 'connection.test', 'data': {'id': 'internal-only'}}

        first = self._post(payload)
        second = self._post(payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            WebhookEvent.objects.filter(
                tenant=self.tenant,
                gateway='pagarme',
                gateway_event_id__isnull=True,
            ).count(),
            2,
        )
        self.assertEqual(mock_process.call_count, 2)

    @override_settings(WEBHOOK_AUTH_REQUIRED=False)
    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    def test_foreign_charge_paid_is_ignored_without_persisting(self, mock_process):
        payload = {
            'id': 'evt_foreign_charge',
            'type': 'charge.paid',
            'data': {
                'id': 'ch_foreign',
                'code': 'external-code',
                'order': {'id': 'or_foreign', 'code': 'external-order'},
            },
        }

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ignored_foreign')
        self.assertFalse(WebhookEvent.objects.filter(
            gateway='pagarme',
            gateway_event_id='evt_foreign_charge',
        ).exists())
        mock_process.assert_not_called()

    @override_settings(WEBHOOK_AUTH_REQUIRED=False)
    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    def test_foreign_payment_link_expired_is_ignored_without_persisting(self, mock_process):
        payload = {
            'id': 'evt_foreign_expired',
            'type': 'payment-link.expired',
            'data': {'id': 'pl_foreign'},
        }

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ignored_foreign')
        self.assertFalse(WebhookEvent.objects.filter(
            gateway='pagarme',
            gateway_event_id='evt_foreign_expired',
        ).exists())
        mock_process.assert_not_called()

    @override_settings(WEBHOOK_AUTH_REQUIRED=False)
    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    def test_known_payment_link_event_is_accepted(self, mock_process):
        payload = {
            'id': 'evt_known_expired',
            'type': 'payment-link.expired',
            'data': {'id': self.payment_link.gateway_link_id},
        }

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'received')
        self.assertTrue(WebhookEvent.objects.filter(
            gateway='pagarme',
            gateway_event_id='evt_known_expired',
        ).exists())
        mock_process.assert_called_once()


class TestReconcilePendingOrders(BaseWebhookTest):
    def setUp(self):
        super().setUp()
        Tenant.objects.filter(pk=self.tenant.pk).update(
            pagarme_api_key='test-api-key',
        )
        self.tenant.refresh_from_db()
        self.old_order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Old Order',
            total_amount=20000,
            status=Order.Status.PENDING,
        )
        Order.objects.filter(pk=self.old_order.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=12),
        )
        self.old_order.refresh_from_db()
        Payment.objects.create(
            order=self.old_order,
            gateway_name='pagarme',
            status=Payment.Status.PENDING,
        )
        PaymentLink.objects.create(
            order=self.old_order,
            gateway_url='https://pagar.me/link/old',
            gateway_link_id='pl_old123',
        )

    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    @patch('app.services.gateway.pagar_me.PagarMeGateway.find_order_by_code')
    def test_order_paid_creates_webhook_event(self, mock_find, mock_process):
        mock_find.return_value = {
            'id': 'or_recon123',
            'code': str(self.old_order.uuid),
            'status': 'paid',
            'charges': [{
                'id': 'ch_recon1',
                'payment_method': 'credit_card',
                'paid_at': '2026-07-03T12:00:00Z',
                'last_transaction': {
                    'installments': 1,
                    'card': {'brand': 'visa', 'last_four_digits': '4242'},
                },
            }],
        }

        from app.apps.webhooks.tasks import reconcile_pending_orders
        with patch('app.apps.notifications.tasks.create_and_send_notification', return_value=None):
            reconcile_pending_orders()

        event = WebhookEvent.objects.filter(
            gateway_event_id=f'reconcile_{self.old_order.uuid}',
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.payload['type'], 'order.paid')

    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    @patch('app.services.gateway.pagar_me.PagarMeGateway.find_order_by_code')
    def test_reconcile_remote_pending_does_nothing(self, mock_find, mock_process):
        mock_find.return_value = {
            'id': 'or_pending',
            'code': str(self.old_order.uuid),
            'status': 'pending',
        }

        from app.apps.webhooks.tasks import reconcile_pending_orders
        reconcile_pending_orders()

        self.old_order.refresh_from_db()
        self.assertEqual(self.old_order.status, Order.Status.PENDING)
        mock_process.assert_not_called()

    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    @patch('app.services.gateway.pagar_me.PagarMeGateway.find_order_by_code')
    def test_tenant_without_api_key_skipped(self, mock_find, mock_process):
        self.tenant.pagarme_api_key = None
        self.tenant.save()

        from app.apps.webhooks.tasks import reconcile_pending_orders
        reconcile_pending_orders()

        mock_find.assert_not_called()

    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    @patch('app.services.gateway.pagar_me.PagarMeGateway.find_order_by_code')
    def test_idempotency_does_not_duplicate(self, mock_find, mock_process):
        mock_find.return_value = {
            'id': 'or_recon123',
            'code': str(self.old_order.uuid),
            'status': 'paid',
            'charges': [{
                'id': 'ch_recon_idempotent',
                'payment_method': 'credit_card',
                'paid_at': '2026-07-03T12:00:00Z',
                'last_transaction': {'installments': 1},
            }],
        }

        from app.apps.webhooks.tasks import reconcile_pending_orders
        with patch('app.apps.notifications.tasks.create_and_send_notification', return_value=None):
            reconcile_pending_orders()
            reconcile_pending_orders()

        sale_count = Sale.objects.filter(order=self.old_order).count()
        self.assertEqual(sale_count, 0)
        self.assertEqual(
            WebhookEvent.objects.filter(
                gateway_event_id=f'reconcile_{self.old_order.uuid}',
            ).count(),
            1,
        )


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


class TestReconcileDedup(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Dedup Test', slug='dedup-test',
            is_active=True, pagarme_api_key='test-api-key',
        )
        self.seller_user = User.objects.create_user(
            username='dedup_seller', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Dedup Seller',
            is_active=True,
        )
        self.order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Dedup Customer',
            total_amount=15000,
            status=Order.Status.PENDING,
        )
        Order.objects.filter(pk=self.order.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=12),
        )
        self.order.refresh_from_db()
        self.payment = Payment.objects.create(
            order=self.order,
            gateway_name='pagarme',
            status=Payment.Status.PENDING,
        )
        PaymentLink.objects.create(
            order=self.order,
            gateway_url='https://pagar.me/link/dedup',
            gateway_link_id='pl_dedup123',
        )

    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    @patch('app.services.gateway.pagar_me.PagarMeGateway.find_order_by_code')
    def test_second_reconcile_does_not_create_duplicate_event(self, mock_find, mock_process):
        mock_find.return_value = {
            'id': 'or_dedup',
            'code': str(self.order.uuid),
            'status': 'paid',
        }

        from app.apps.webhooks.tasks import reconcile_pending_orders
        with patch('app.apps.notifications.tasks.create_and_send_notification', return_value=None):
            reconcile_pending_orders()

        first_count = WebhookEvent.objects.filter(
            gateway_event_id=f'reconcile_{self.order.uuid}',
        ).count()
        self.assertEqual(first_count, 1)

        # Reset order to pending so the order still qualifies for reconciliation
        self.order.status = Order.Status.PENDING
        self.order.save(update_fields=['status'])

        with patch('app.apps.notifications.tasks.create_and_send_notification', return_value=None):
            reconcile_pending_orders()

        second_count = WebhookEvent.objects.filter(
            gateway_event_id=f'reconcile_{self.order.uuid}',
        ).count()
        self.assertEqual(second_count, 1)

    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    @patch('app.services.gateway.pagar_me.PagarMeGateway.find_order_by_code')
    def test_exception_in_one_order_does_not_abort_batch(self, mock_find, mock_process):
        order2 = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Order 2',
            total_amount=20000,
            status=Order.Status.PENDING,
        )
        Order.objects.filter(pk=order2.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=12),
        )
        order2.refresh_from_db()
        Payment.objects.create(
            order=order2,
            gateway_name='pagarme',
            status=Payment.Status.PENDING,
        )
        PaymentLink.objects.create(
            order=order2,
            gateway_url='https://pagar.me/link/dedup2',
            gateway_link_id='pl_dedup456',
        )

        call_count = 0

        def side_effect(order_code):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Simulated failure in first order")
            return {
                'id': 'or_dedup2',
                'code': str(order2.uuid),
                'status': 'paid',
            }

        mock_find.side_effect = side_effect

        from app.apps.webhooks.tasks import reconcile_pending_orders
        with patch('app.apps.notifications.tasks.create_and_send_notification', return_value=None):
            reconcile_pending_orders()

        # The second order should have been processed
        event = WebhookEvent.objects.filter(
            gateway_event_id=f'reconcile_{order2.uuid}',
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.payload['type'], 'order.paid')


class TestPagarmeReliability(BaseWebhookTest):
    def test_charge_paid_accepts_real_payload_code_and_metadata_link_id(self):
        payload = {
            'type': 'charge.paid',
            'data': {
                'id': 'ch_real_shape',
                'code': str(self.order.uuid),
                'payment_method': 'credit_card',
                'paid_at': '2026-07-14T17:20:16Z',
                'last_transaction': {'installments': 3},
                'metadata': {'payment_link_id': 'pl_test123'},
            },
        }
        event = self._create_event(payload)

        self._run_task(event)

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.payment.installments, 3)
        self.assertEqual(self.order.status, Order.Status.COMPLETED)
        self.assertEqual(self.order.status_display_pt, 'Pago')
        self.assertEqual(self.payment.status_display_pt, 'Pago')

    def test_charge_paid_notification_failure_does_not_rollback_financial_state(self):
        payload = {
            'type': 'charge.paid',
            'data': {
                'id': 'ch_no_rollback',
                'payment_method': 'credit_card',
                'paid_at': '2026-07-03T12:00:00Z',
                'last_transaction': {'installments': 2},
                'order': {
                    'id': 'or_no_rollback',
                    'code': str(self.order.uuid),
                    'payment_link': {'id': 'pl_test123'},
                },
            },
        }
        event = self._create_event(payload)

        from app.apps.webhooks.tasks import process_pagarme_webhook
        with patch(
            'app.apps.notifications.tasks.create_and_send_notification',
            side_effect=RuntimeError('WHATSAPP_FAILURE'),
        ):
            process_pagarme_webhook(event.id)

        event.refresh_from_db()
        self.payment.refresh_from_db()
        self.order.refresh_from_db()

        self.assertTrue(event.processed)
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.order.status, Order.Status.COMPLETED)
        self.assertEqual(Sale.objects.filter(order=self.order).count(), 0)

    def test_payment_failed_reason_visible_and_sent_to_notification(self):
        payload = {
            'type': 'charge.payment_failed',
            'data': {
                'id': 'ch_failed_reason',
                'payment_method': 'credit_card',
                'last_transaction': {
                    'installments': 2,
                    'refusal_reason': 'Cartao recusado',
                },
                'order': {
                    'payment_link': {'id': 'pl_test123'},
                },
            },
        }
        event = self._create_event(payload)

        with patch('app.apps.notifications.tasks.create_and_send_notification') as mock_notify:
            from app.apps.webhooks.tasks import process_pagarme_webhook
            with self.captureOnCommitCallbacks(execute=True):
                process_pagarme_webhook(event.id)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.payment.refusal_reason, 'Cartao recusado')
        self.assertEqual(self.payment.status_display_pt, 'Recusado')
        context = mock_notify.call_args.kwargs['context']
        self.assertEqual(context['motivo'], 'Cartao recusado')

    def test_charge_paid_and_order_paid_do_not_create_sale(self):
        charge_payload = {
            'type': 'charge.paid',
            'data': {
                'id': 'ch_same_order',
                'payment_method': 'credit_card',
                'paid_at': '2026-07-03T12:00:00Z',
                'last_transaction': {'installments': 3},
                'order': {
                    'id': 'or_same_order',
                    'code': str(self.order.uuid),
                    'payment_link': {'id': 'pl_test123'},
                },
            },
        }
        order_payload = {
            'type': 'order.paid',
            'data': {
                'id': 'or_same_order',
                'code': str(self.order.uuid),
                'charges': [{
                    'id': 'ch_same_order',
                    'payment_method': 'credit_card',
                    'paid_at': '2026-07-03T12:00:00Z',
                    'last_transaction': {'installments': 3},
                }],
            },
        }

        self._run_task(self._create_event(charge_payload))
        self._run_task(self._create_event(order_payload))

        self.assertEqual(Sale.objects.filter(order=self.order).count(), 0)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.order.status, Order.Status.COMPLETED)

    def test_processing_failure_persists_failed_status_after_rollback(self):
        payload = {
            'type': 'charge.paid',
            'data': {
                'id': 'ch_failure_status',
                'payment_method': 'credit_card',
                'order': {
                    'id': 'or_failure_status',
                    'code': str(self.order.uuid),
                    'payment_link': {'id': 'pl_test123'},
                },
            },
        }
        event = self._create_event(payload)

        from app.apps.webhooks.services import process_paid_pagarme_event
        with patch(
            'app.apps.webhooks.services.populate_payment_from_webhook',
            side_effect=RuntimeError('SANITIZED_PROCESSING_FAILURE'),
        ):
            with self.assertRaises(RuntimeError):
                process_paid_pagarme_event(event.id)

        event.refresh_from_db()
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertFalse(event.processed)
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertIn('SANITIZED_PROCESSING_FAILURE', event.processing_error)
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_reprocess_command_dry_run_does_not_change_status(self):
        from io import StringIO
        from django.core.management import call_command

        payload = {
            'type': 'charge.paid',
            'data': {
                'id': 'ch_dry_run',
                'payment_method': 'credit_card',
                'order': {
                    'id': 'or_dry_run',
                    'code': str(self.order.uuid),
                    'payment_link': {'id': 'pl_test123'},
                },
            },
        }
        event = self._create_event(payload)

        out = StringIO()
        call_command(
            'reprocess_pagarme_webhook',
            '--event-id', str(event.id),
            '--dry-run',
            stdout=out,
        )

        event.refresh_from_db()
        self.payment.refresh_from_db()
        self.order.refresh_from_db()

        self.assertFalse(event.processed)
        self.assertEqual(event.status, WebhookEvent.Status.RECEIVED)
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertIn('dry_run', out.getvalue())

    def test_reprocess_command_uses_idempotent_service(self):
        from io import StringIO
        from django.core.management import call_command

        payload = {
            'type': 'charge.paid',
            'data': {
                'id': 'ch_reprocess',
                'payment_method': 'credit_card',
                'order': {
                    'id': 'or_reprocess',
                    'code': str(self.order.uuid),
                    'payment_link': {'id': 'pl_test123'},
                },
            },
        }
        event = self._create_event(payload)

        out = StringIO()
        with patch('app.apps.notifications.tasks.create_and_send_notification', return_value=None):
            call_command(
                'reprocess_pagarme_webhook',
                '--event-id', str(event.id),
                '--operator', 'test',
                stdout=out,
            )
            call_command(
                'reprocess_pagarme_webhook',
                '--event-id', str(event.id),
                '--operator', 'test',
                stdout=out,
            )

        event.refresh_from_db()
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertTrue(event.processed)
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.order.status, Order.Status.COMPLETED)
        self.assertEqual(Sale.objects.filter(order=self.order).count(), 0)


class TestPagarmeManualVerification(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Manual Verify',
            slug='manual-verify',
            is_active=True,
            pagarme_api_key='test-api-key',
        )
        self.manager = User.objects.create_user(
            username='manager_verify', password='test123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='seller_verify', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Seller Verify',
            phone='55999999999',
            is_active=True,
        )
        self.order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Cliente Verify',
            total_amount=12345,
            status=Order.Status.PENDING,
        )
        self.payment = Payment.objects.create(
            order=self.order,
            gateway_name='pagarme',
            status=Payment.Status.PENDING,
            installments=4,
        )
        PaymentLink.objects.create(
            order=self.order,
            gateway_url='https://pagar.me/link/verify',
            gateway_link_id='pl_verify',
        )

    @patch('app.apps.notifications.tasks.create_and_send_notification', return_value=None)
    @patch('app.services.gateway.pagar_me.PagarMeGateway.find_order_by_code')
    def test_manual_verification_only_marks_paid_when_remote_paid(self, mock_find, _mock_notify):
        self.client.force_login(self.manager)
        mock_find.return_value = {
            'id': 'or_verify',
            'code': str(self.order.uuid),
            'status': 'paid',
            'charges': [{
                'id': 'ch_verify',
                'payment_method': 'credit_card',
                'paid_at': '2026-07-03T12:00:00Z',
                'last_transaction': {'installments': 4},
            }],
        }

        response = self.client.post(
            f'/dashboard/gestor/links/{self.order.uuid}/verificar-pagamento/',
        )

        self.assertEqual(response.status_code, 302)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.order.status, Order.Status.COMPLETED)
        self.assertEqual(Sale.objects.filter(order=self.order).count(), 0)

    @patch('app.services.gateway.pagar_me.PagarMeGateway.find_order_by_code')
    def test_manual_verification_does_not_mark_paid_when_remote_pending(self, mock_find):
        self.client.force_login(self.manager)
        mock_find.return_value = {
            'id': 'or_verify_pending',
            'code': str(self.order.uuid),
            'status': 'pending',
        }

        response = self.client.post(
            f'/dashboard/gestor/links/{self.order.uuid}/verificar-pagamento/',
        )

        self.assertEqual(response.status_code, 302)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(Sale.objects.filter(order=self.order).count(), 0)

    def test_manager_webhook_status_hides_technical_details(self):
        self.client.force_login(self.manager)
        response = self.client.get('/api/manager/webhook-status/')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn(data['status'], ['connected', 'requires_attention'])
        self.assertNotIn('webhook_url', data)
        self.assertNotIn('last_event', data)
        self.assertNotIn('error', data)


class TestPagarmeRefundRequests(TestCase):
    def test_partial_cancel_charge_uses_cancel_endpoint_with_amount(self):
        from app.services.gateway.pagar_me import PagarMeGateway

        gateway = PagarMeGateway(api_key='test-api-key')
        with patch('app.services.gateway.pagar_me.requests.request') as mock_request:
            mock_response = mock_request.return_value
            mock_response.status_code = 200
            mock_response.json.return_value = {'id': 'ch_refund'}

            gateway.partial_cancel_charge('ch_refund', 56160)

        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], 'POST')
        self.assertEqual(
            args[1],
            'https://api.pagar.me/core/v5/charges/ch_refund/cancel',
        )
        self.assertEqual(kwargs['json'], {'amount': 56160})

    def test_full_cancel_charge_uses_cancel_endpoint_without_amount(self):
        from app.services.gateway.pagar_me import PagarMeGateway

        gateway = PagarMeGateway(api_key='test-api-key')
        with patch('app.services.gateway.pagar_me.requests.request') as mock_request:
            mock_response = mock_request.return_value
            mock_response.status_code = 200
            mock_response.json.return_value = {'id': 'ch_refund'}

            gateway.cancel_charge('ch_refund')

        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], 'POST')
        self.assertEqual(
            args[1],
            'https://api.pagar.me/core/v5/charges/ch_refund/cancel',
        )
        self.assertNotIn('json', kwargs)


class TestGestorLinkRefundView(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Refund View',
            slug='refund-view',
            is_active=True,
            pagarme_api_key='test-api-key',
        )
        self.manager = User.objects.create_user(
            username='manager_refund',
            password='test123',
            role=User.Role.MANAGER,
            tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='seller_refund',
            password='test123',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Seller Refund',
            is_active=True,
        )
        self.order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Cliente Refund',
            total_amount=56160,
            status=Order.Status.COMPLETED,
        )
        self.payment = Payment.objects.create(
            order=self.order,
            gateway_name='pagarme',
            status=Payment.Status.PAID,
            gateway_transaction_id='ch_refund_view',
        )
        self.url = f'/dashboard/gestor/links/{self.order.uuid}/estornar/'

    @patch('app.services.gateway.pagar_me.PagarMeGateway.partial_cancel_charge')
    def test_partial_refund_accepts_numeric_string_amount(self, mock_partial):
        self.client.force_login(self.manager)

        response = self.client.post(
            self.url,
            data=json.dumps({'amount': '56160'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        mock_partial.assert_called_once_with('ch_refund_view', 56160)

    @patch('app.services.gateway.pagar_me.PagarMeGateway.partial_cancel_charge')
    def test_partial_refund_rejects_amount_greater_than_payment(self, mock_partial):
        self.client.force_login(self.manager)

        response = self.client.post(
            self.url,
            data=json.dumps({'amount': 56161}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('maior que o pagamento', response.json()['error'])
        mock_partial.assert_not_called()
