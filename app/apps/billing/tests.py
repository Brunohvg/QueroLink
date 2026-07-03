from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from app.apps.accounts.models import Tenant, User, tenant_operational
from app.apps.billing.models import Subscription
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale
from app.apps.webhooks.models import WebhookEvent


class TestTenantOperational(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Billing Test', slug='billing-test',
            is_active=True, trial_ends_at=None,
        )
        self.sub = Subscription.objects.create(
            tenant=self.tenant,
            plan='ESSENCIAL',
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
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Billing WH', slug='billing-wh',
            is_active=True,
        )
        self.sub = Subscription.objects.create(
            tenant=self.tenant,
            plan='ESSENCIAL',
            gateway_subscription_id='sub_test123',
            status=Subscription.Status.TRIALING,
        )

    def test_charge_paid_makes_active(self):
        payload = {
            'type': 'subscription.charge_paid',
            'data': {'id': 'sub_test123', 'subscription_id': 'sub_test123'},
        }
        event = WebhookEvent.objects.create(
            gateway='pagarme_billing', payload=payload,
            gateway_event_id='evt_test1',
        )
        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)
        self.assertIsNotNone(self.sub.current_period_end)

    def test_charge_failed_makes_past_due(self):
        payload = {
            'type': 'subscription.charge_failed',
            'data': {'id': 'sub_test123', 'subscription_id': 'sub_test123'},
        }
        event = WebhookEvent.objects.create(
            gateway='pagarme_billing', payload=payload,
            gateway_event_id='evt_test2',
        )
        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.PAST_DUE)

    def test_canceled(self):
        payload = {
            'type': 'subscription.canceled',
            'data': {'id': 'sub_test123', 'subscription_id': 'sub_test123'},
        }
        event = WebhookEvent.objects.create(
            gateway='pagarme_billing', payload=payload,
            gateway_event_id='evt_test3',
        )
        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.CANCELED)
