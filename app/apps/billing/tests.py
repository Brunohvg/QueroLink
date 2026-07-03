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
    @override_settings(MP_WEBHOOK_SECRET='test_secret_key')
    def test_valid_signature_passes(self):
        import hashlib
        import hmac
        import json

        data_id = '56789'
        ts = '1704067200'
        payload = {'type': 'payment', 'data': {'id': data_id}}
        raw_body = json.dumps(payload).encode()

        template = f"id:{data_id};ts:{ts};{raw_body.decode('utf-8')}"
        v1 = hmac.new(
            b'test_secret_key', template.encode(), hashlib.sha256,
        ).hexdigest()
        x_sig = f"ts={ts},v1={v1}"

        resp = self.client.post(
            '/api/webhooks/billing/',
            data=raw_body,
            content_type='application/json',
            HTTP_X_SIGNATURE=x_sig,
        )
        self.assertEqual(resp.status_code, 200)

    @override_settings(MP_WEBHOOK_SECRET='test_secret_key')
    def test_invalid_signature_rejected(self):
        resp = self.client.post(
            '/api/webhooks/billing/',
            data='{"type":"payment","data":{"id":"123"}}',
            content_type='application/json',
            HTTP_X_SIGNATURE='ts=1,v1=invalid',
        )
        self.assertEqual(resp.status_code, 403)

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
        self.assertEqual(resp.status_code, 200)


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
