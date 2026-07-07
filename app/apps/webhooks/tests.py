import json
from datetime import datetime
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.test import Client, TestCase, TransactionTestCase, override_settings
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


class TestWebhookIdempotency(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Idempotency Test',
            slug='idempotency-test',
            is_active=True,
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
        first_payload = {'id': 'evt_duplicate', 'type': 'order.paid', 'data': {'id': 'or_1'}}
        second_payload = {'id': 'evt_duplicate', 'type': 'order.paid', 'data': {'id': 'or_2'}}

        first = self._post(first_payload)
        second = self._post(second_payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['status'], 'received')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['status'], 'duplicate')
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
        self.assertEqual(mock_process.call_count, 1)

    @override_settings(WEBHOOK_AUTH_REQUIRED=False, MP_WEBHOOK_SECRET='test_secret_key')
    @patch('app.apps.webhooks.tasks.process_billing_webhook.delay')
    @patch('app.apps.webhooks.tasks.process_pagarme_webhook.delay')
    def test_same_event_id_can_exist_in_different_gateways(self, mock_pagarme, mock_billing):
        pagarme_response = self._post({
            'id': 'evt_same',
            'type': 'order.paid',
            'data': {'id': 'or_same'},
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
        payload = {'id': 'evt_concurrent', 'type': 'order.paid', 'data': {'id': 'or_1'}}

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _i: self._post(payload), range(2)))

        self.assertEqual(sorted(response.status_code for response in responses), [200, 200])
        self.assertEqual(
            WebhookEvent.objects.filter(
                gateway='pagarme',
                gateway_event_id='evt_concurrent',
            ).count(),
            1,
        )
        self.assertEqual(mock_process.call_count, 1)

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


class TestReconcilePendingOrders(BaseWebhookTest):
    def setUp(self):
        super().setUp()
        Tenant.objects.filter(pk=self.tenant.pk).update(
            pagarme_api_key='sk_test_fakekey123456',
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
        }
        self.payment.status = Payment.Status.PAID
        self.payment.save()

        from app.apps.webhooks.tasks import reconcile_pending_orders
        with patch('app.apps.notifications.tasks.create_and_send_notification', return_value=None):
            reconcile_pending_orders()

        sale_count = Sale.objects.filter(order=self.old_order).count()
        self.assertEqual(sale_count, 0)


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
            is_active=True, pagarme_api_key='sk_test_fakekey123456',
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
