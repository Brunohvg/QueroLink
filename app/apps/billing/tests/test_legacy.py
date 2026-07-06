from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from app.apps.accounts.models import Tenant, User, tenant_operational
from app.apps.billing.models import Subscription
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale
from app.apps.webhooks.models import WebhookEvent


class TestBillingWebhookSignature(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Sig Test', slug='sig-test',
            is_active=True,
        )

    @override_settings(MP_WEBHOOK_SECRET='test_secret_key')
    def test_valid_signature_passes(self):
        import hashlib
        import hmac
        import json

        data_id = '56789'
        request_id = 'req-abc-123'
        ts = '1704067200'
        payload = {'type': 'payment', 'data': {'id': data_id}}
        raw_body = json.dumps(payload).encode()

        manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
        v1 = hmac.new(
            b'test_secret_key', manifest.encode(), hashlib.sha256,
        ).hexdigest()
        x_sig = f"ts={ts},v1={v1}"

        resp = self.client.post(
            f'/api/webhooks/billing/?data.id={data_id}&type=payment',
            data=raw_body,
            content_type='application/json',
            HTTP_X_SIGNATURE=x_sig,
            HTTP_X_REQUEST_ID=request_id,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(WebhookEvent.objects.filter(gateway='mercadopago').exists())

    @override_settings(MP_WEBHOOK_SECRET='test_secret_key')
    def test_invalid_signature_rejected(self):
        resp = self.client.post(
            '/api/webhooks/billing/?data.id=123&type=payment',
            data='{"type":"payment","data":{"id":"123"}}',
            content_type='application/json',
            HTTP_X_SIGNATURE='ts=1,v1=invalid',
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(WebhookEvent.objects.filter(gateway='mercadopago').exists())

    @override_settings(MP_WEBHOOK_SECRET='test_secret_key')
    def test_missing_signature_rejected(self):
        resp = self.client.post(
            '/api/webhooks/billing/',
            data='{"type":"payment","data":{"id":"123"}}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)

    @override_settings(MP_WEBHOOK_SECRET='')
    def test_no_secret_configured_accepts(self):
        resp = self.client.post(
            '/api/webhooks/billing/',
            data='{"type":"payment","data":{"id":"123"}}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 503)

    @override_settings(MP_WEBHOOK_SECRET='test_secret_key')
    def test_uppercase_data_id_lowered_for_validation(self):
        import hashlib
        import hmac
        import json

        data_id = 'ABC123'
        request_id = 'req-def-456'
        ts = '1704067200'
        payload = {'type': 'payment', 'data': {'id': data_id}}
        raw_body = json.dumps(payload).encode()

        manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{ts};"
        v1 = hmac.new(
            b'test_secret_key', manifest.encode(), hashlib.sha256,
        ).hexdigest()
        x_sig = f"ts={ts},v1={v1}"

        resp = self.client.post(
            f'/api/webhooks/billing/?data.id={data_id}&type=payment',
            data=raw_body,
            content_type='application/json',
            HTTP_X_SIGNATURE=x_sig,
            HTTP_X_REQUEST_ID=request_id,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(WebhookEvent.objects.filter(gateway='mercadopago').exists())


class TestTenantOperational(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Billing Test', slug='billing-test',
            is_active=True, trial_ends_at=None,
        )
        self.sub = Subscription.objects.create(
            tenant=self.tenant,
            plan='PRO',
            status=Subscription.Status.ACTIVE,
            current_period_end=timezone.now() + timedelta(days=20),
            amount=4900,
        )

    def test_active_subscription_is_operational(self):
        self.assertTrue(tenant_operational(self.tenant))

    def test_trial_is_operational(self):
        self.tenant.trial_ends_at = timezone.now() + timedelta(days=5)
        self.tenant.save()
        self.sub.status = Subscription.Status.TRIALING
        self.sub.save()
        self.assertTrue(tenant_operational(self.tenant))

    def test_past_due_within_tolerance(self):
        self.sub.status = Subscription.Status.PAST_DUE
        self.sub.current_period_end = timezone.now() - timedelta(days=3)
        self.sub.save()
        self.assertTrue(tenant_operational(self.tenant))

    def test_past_due_outside_tolerance(self):
        self.sub.status = Subscription.Status.PAST_DUE
        self.sub.current_period_end = timezone.now() - timedelta(days=10)
        self.sub.save()
        self.assertFalse(tenant_operational(self.tenant))

    def test_canceled_not_operational(self):
        self.sub.status = Subscription.Status.CANCELED
        self.sub.save()
        self.assertFalse(tenant_operational(self.tenant))


class TestBillingWebhook(TestCase):
    MP_PAYMENT_APPROVED = {
        "id": 12345,
        "external_reference": None,
        "status": "approved",
    }
    MP_PAYMENT_REJECTED = {
        "id": 12346,
        "external_reference": None,
        "status": "rejected",
    }
    MP_PREAPPROVAL_AUTHORIZED = {
        "id": "sub_test123",
        "external_reference": None,
        "status": "authorized",
    }
    MP_PREAPPROVAL_CANCELLED = {
        "id": "sub_test123",
        "external_reference": None,
        "status": "cancelled",
    }

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Billing WH', slug='billing-wh',
            is_active=True,
        )
        self.sub = Subscription.objects.create(
            tenant=self.tenant,
            plan='PRO',
            gateway_subscription_id='sub_test123',
            status=Subscription.Status.TRIALING,
        )
        self.MP_PAYMENT_APPROVED['external_reference'] = str(self.tenant.uuid)
        self.MP_PAYMENT_REJECTED['external_reference'] = str(self.tenant.uuid)
        self.MP_PREAPPROVAL_AUTHORIZED['external_reference'] = str(self.tenant.uuid)
        self.MP_PREAPPROVAL_CANCELLED['external_reference'] = str(self.tenant.uuid)

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_payment_approved_makes_active(self, MockGateway):
        instance = MockGateway.return_value
        instance.get_payment.return_value = self.MP_PAYMENT_APPROVED

        payload = {
            'type': 'payment',
            'action': 'payment.created',
            'data': {'id': '12345'},
        }
        event = WebhookEvent.objects.create(
            gateway='mercadopago', payload=payload,
            gateway_event_id='evt_test1',
        )
        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)
        self.assertIsNotNone(self.sub.current_period_end)

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_payment_rejected_makes_past_due(self, MockGateway):
        instance = MockGateway.return_value
        instance.get_payment.return_value = self.MP_PAYMENT_REJECTED

        payload = {
            'type': 'payment',
            'action': 'payment.updated',
            'data': {'id': '12346'},
        }
        event = WebhookEvent.objects.create(
            gateway='mercadopago', payload=payload,
            gateway_event_id='evt_test2',
        )
        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.PAST_DUE)

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_subscription_cancelled(self, MockGateway):
        instance = MockGateway.return_value
        instance.get_preapproval.return_value = self.MP_PREAPPROVAL_CANCELLED

        payload = {
            'type': 'subscription_preapproval',
            'action': 'subscription_preapproval.updated',
            'data': {'id': 'sub_test123'},
        }
        event = WebhookEvent.objects.create(
            gateway='mercadopago', payload=payload,
            gateway_event_id='evt_test3',
        )
        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.CANCELED)

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_subscription_authorized(self, MockGateway):
        instance = MockGateway.return_value
        instance.get_preapproval.return_value = self.MP_PREAPPROVAL_AUTHORIZED

        payload = {
            'type': 'subscription_preapproval',
            'action': 'subscription_preapproval.updated',
            'data': {'id': 'sub_test123'},
        }
        event = WebhookEvent.objects.create(
            gateway='mercadopago', payload=payload,
            gateway_event_id='evt_test4',
        )
        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)


class TestAnnualBillingCycle(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Annual Test', slug='annual-test',
            is_active=True, billing_email='test@test.com',
        )
        self.user = User.objects.create_user(
            username='annual_mgr', password='test123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )

    @override_settings(PLAN_PRICES={'STARTER': 9700, 'PRO': 24700, 'ENTERPRISE': 49700}, MP_ACCESS_TOKEN='test_token')
    @patch('app.apps.billing.views.MercadoPagoGateway')
    def test_upgrade_yearly_sets_frequency_12(self, MockGateway):
        instance = MockGateway.return_value
        instance.create_preapproval.return_value = {'id': 'sub_yearly', 'init_point': 'https://mp.com'}

        self.client.login(username='annual_mgr', password='test123')
        resp = self.client.post(
            '/api/billing/subscription/upgrade/',
            data={'plan': 'PRO', 'billing_cycle': 'YEARLY', 'payment_method': 'credit_card'},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

        call_kwargs = instance.create_preapproval.call_args.kwargs
        self.assertEqual(call_kwargs['frequency'], 12)
        self.assertEqual(call_kwargs['frequency_type'], 'months')
        expected_yearly_amount = 24700 * 10 / 100
        self.assertAlmostEqual(call_kwargs['amount'], expected_yearly_amount)

    @override_settings(PLAN_PRICES={'STARTER': 9700, 'PRO': 24700, 'ENTERPRISE': 49700}, MP_ACCESS_TOKEN='test_token')
    @patch('app.apps.billing.views.MercadoPagoGateway')
    def test_upgrade_monthly_sets_frequency_1(self, MockGateway):
        instance = MockGateway.return_value
        instance.create_preapproval.return_value = {'id': 'sub_monthly', 'init_point': 'https://mp.com'}

        self.client.login(username='annual_mgr', password='test123')
        resp = self.client.post(
            '/api/billing/subscription/upgrade/',
            data={'plan': 'PRO', 'billing_cycle': 'MONTHLY', 'payment_method': 'credit_card'},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

        call_kwargs = instance.create_preapproval.call_args.kwargs
        self.assertEqual(call_kwargs['frequency'], 1)
        expected_monthly_amount = 24700 / 100
        self.assertEqual(call_kwargs['amount'], expected_monthly_amount)

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_payment_approved_yearly_sets_365_days(self, MockGateway):
        from datetime import timedelta
        from django.utils import timezone

        instance = MockGateway.return_value
        instance.get_payment.return_value = {
            'id': 12345,
            'external_reference': str(self.tenant.uuid),
            'status': 'approved',
        }

        sub = Subscription.objects.create(
            tenant=self.tenant,
            plan='PRO',
            gateway_subscription_id='sub_yearly_test',
            status=Subscription.Status.TRIALING,
            billing_cycle='YEARLY',
        )
        event = WebhookEvent.objects.create(
            gateway='mercadopago',
            payload={'type': 'payment', 'data': {'id': '12345'}},
            gateway_event_id='evt_yearly',
        )
        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertIsNotNone(sub.current_period_end)
        diff_days = (sub.current_period_end - timezone.now()).days
        self.assertGreaterEqual(diff_days, 364)
        self.assertLessEqual(diff_days, 365)

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_payment_approved_monthly_sets_30_days(self, MockGateway):
        from datetime import timedelta
        from django.utils import timezone

        instance = MockGateway.return_value
        instance.get_payment.return_value = {
            'id': 12346,
            'external_reference': str(self.tenant.uuid),
            'status': 'approved',
        }

        sub = Subscription.objects.create(
            tenant=self.tenant,
            plan='PRO',
            gateway_subscription_id='sub_monthly_test',
            status=Subscription.Status.TRIALING,
            billing_cycle='MONTHLY',
        )
        event = WebhookEvent.objects.create(
            gateway='mercadopago',
            payload={'type': 'payment', 'data': {'id': '12346'}},
            gateway_event_id='evt_monthly',
        )
        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertIsNotNone(sub.current_period_end)
        diff_days = (sub.current_period_end - timezone.now()).days
        self.assertGreaterEqual(diff_days, 29)
        self.assertLessEqual(diff_days, 30)


class TestMercadoPagoGuardRails(TestCase):
    def test_frequency_guard_rail(self):
        from app.services.gateway.mercadopago import MercadoPagoGateway, MercadoPagoError
        with override_settings(MP_ACCESS_TOKEN='test_token'):
            import mercadopago
            with patch.object(mercadopago.SDK, 'preapproval') as mock_preapproval:
                mock_preapproval.return_value.create.return_value = {
                    'status': 201, 'response': {'id': 'test'},
                }
                gateway = MercadoPagoGateway()
                with self.assertRaises(MercadoPagoError) as ctx:
                    gateway.create_preapproval(
                        reason='Test', external_reference='ref',
                        payer_email='test@test.com', amount=10,
                        frequency=6,
                    )
                self.assertIn('Frequencia invalida', str(ctx.exception))


class TestPaymentBypassPrevention(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Bypass Test', slug='bypass-test',
            is_active=True, plan=Tenant.Plan.STARTER,
            billing_email='bypass@test.com',
            trial_ends_at=timezone.now() + timedelta(days=30),
        )
        self.user = User.objects.create_user(
            username='bypass_mgr', password='test123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )

    @override_settings(PLAN_PRICES={'PRO': 24700}, MP_ACCESS_TOKEN='test_token')
    @patch('app.apps.billing.views.MercadoPagoGateway')
    def test_upgrade_init_does_not_change_tenant_plan(self, MockGateway):
        instance = MockGateway.return_value
        instance.create_preapproval.return_value = {'id': 'sub_pending', 'init_point': 'https://mp.com'}

        self.assertEqual(self.tenant.plan, Tenant.Plan.STARTER)

        self.client.login(username='bypass_mgr', password='test123')
        resp = self.client.post(
            '/api/billing/subscription/upgrade/',
            data={'plan': 'PRO', 'billing_cycle': 'MONTHLY', 'payment_method': 'credit_card'},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, Tenant.Plan.STARTER)

        sub = Subscription.objects.get(tenant=self.tenant)
        self.assertEqual(sub.status, Subscription.Status.PENDING)
        self.assertEqual(sub.plan, 'PRO')

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_preapproval_authorized_updates_tenant_plan(self, MockGateway):
        instance = MockGateway.return_value
        instance.get_preapproval.return_value = {
            'id': 'sub_auth_test',
            'external_reference': str(self.tenant.uuid),
            'status': 'authorized',
        }

        sub = Subscription.objects.create(
            tenant=self.tenant,
            plan='PRO',
            gateway_subscription_id='sub_auth_test',
            status=Subscription.Status.PENDING,
        )

        event = WebhookEvent.objects.create(
            gateway='mercadopago',
            payload={
                'type': 'subscription_preapproval',
                'data': {'id': 'sub_auth_test'},
            },
            gateway_event_id='evt_auth',
        )

        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, 'PRO')

    def test_pending_sub_not_operational(self):
        self.tenant.trial_ends_at = timezone.now() - timedelta(days=1)
        self.tenant.save(update_fields=['trial_ends_at'])
        Subscription.objects.create(
            tenant=self.tenant,
            plan='PRO',
            gateway_subscription_id='sub_pending_test',
            status=Subscription.Status.PENDING,
        )
        self.assertFalse(tenant_operational(self.tenant))

    def test_active_sub_is_operational_when_trial_expired(self):
        self.tenant.trial_ends_at = timezone.now() - timedelta(days=1)
        self.tenant.save(update_fields=['trial_ends_at'])
        Subscription.objects.create(
            tenant=self.tenant,
            plan='PRO',
            gateway_subscription_id='sub_active_test',
            status=Subscription.Status.ACTIVE,
        )
        self.assertTrue(tenant_operational(self.tenant))


class TestConcurrentBillingWebhook(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Concurrent Test', slug='concurrent-test',
            is_active=True,
        )
        self.user = User.objects.create_user(
            username='concurrent_mgr', password='test123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.sub = Subscription.objects.create(
            tenant=self.tenant,
            plan='PRO',
            gateway_subscription_id='sub_concurrent',
            status=Subscription.Status.TRIALING,
        )

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_concurrent_execution_idempotent(self, MockGateway):
        instance = MockGateway.return_value
        instance.get_payment.return_value = {
            'id': 12345,
            'external_reference': str(self.tenant.uuid),
            'status': 'approved',
        }

        event = WebhookEvent.objects.create(
            gateway='mercadopago',
            payload={'type': 'payment', 'data': {'id': '12345'}},
            gateway_event_id='evt_concurrent',
        )

        from app.apps.webhooks.tasks import process_billing_webhook

        process_billing_webhook(event.id)
        process_billing_webhook(event.id)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)


class TestPlanAmountHelper(TestCase):
    @override_settings(PLAN_PRICES={'STARTER': 9700, 'PRO': 24700, 'ENTERPRISE': 49700})
    def test_plan_amount_yearly_equals_monthly_times_10(self):
        from app.apps.billing.models import plan_amount
        self.assertEqual(plan_amount('PRO', 'YEARLY'), 247000)
        self.assertEqual(plan_amount('PRO', 'MONTHLY'), 24700)
        self.assertEqual(plan_amount('STARTER', 'YEARLY'), 97000)
        self.assertEqual(plan_amount('STARTER', 'MONTHLY'), 9700)


class TestPlanListView(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Plan List Test', slug='plan-list-test',
            is_active=True,
        )
        self.user = User.objects.create_user(
            username='plan_mgr', password='test123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )

    @override_settings(PLAN_PRICES={'STARTER': 9700, 'PRO': 24700, 'ENTERPRISE': 49700})
    @override_settings(PLAN_SELLER_LIMITS={'STARTER': 5, 'PRO': 15, 'ENTERPRISE': 40})
    def test_plan_list_yearly_equals_monthly_times_10(self):
        self.client.login(username='plan_mgr', password='test123')
        resp = self.client.get('/api/billing/plans/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        pro_plan = next(p for p in data if p['id'] == 'PRO')
        self.assertEqual(pro_plan['price_monthly'], 24700)
        self.assertEqual(pro_plan['price_yearly'], 247000)
        self.assertEqual(pro_plan['seller_limit'], 15)
