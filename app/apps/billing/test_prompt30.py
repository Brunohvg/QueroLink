import io
import os
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone

from app.apps.accounts.forms import TenantRegistrationForm
from app.apps.accounts.models import Tenant, User, tenant_operational
from app.apps.api.serializers import SaleCreateSerializer
from app.apps.billing.models import Subscription
from app.apps.sellers.models import Seller
from app.apps.webhooks.models import WebhookEvent
from app.services.gateway.mercadopago import MercadoPagoError


class Prompt30Factories:
    def tenant(self, slug, plan=Tenant.Plan.STARTER, billing_email='billing@example.com'):
        return Tenant.objects.create(
            company_name=slug,
            slug=slug,
            plan=plan,
            is_active=True,
            billing_email=billing_email,
        )

    def user(self, tenant, username, role=User.Role.MANAGER):
        return User.objects.create_user(
            username=username,
            password='test12345',
            role=role,
            tenant=tenant,
        )

    def seller(self, tenant, username, phone, active=True):
        user = self.user(tenant, username, role=User.Role.SELLER)
        return Seller.objects.create(
            tenant=tenant,
            user=user,
            name=username,
            phone=phone,
            is_active=active,
            commission_rate=tenant.default_commission_rate,
        )


class TestPrompt30OfferedPlans(TestCase, Prompt30Factories):
    def test_signup_offers_business_but_not_enterprise(self):
        choices = [code for code, _label in TenantRegistrationForm.base_fields['plan'].choices]
        self.assertEqual(choices, ['STARTER', 'PRO', 'BUSINESS'])
        self.assertNotIn('ENTERPRISE', choices)

    def test_plan_list_returns_offered_plans_only(self):
        tenant = self.tenant('plans')
        self.user(tenant, 'plans-manager')
        self.client.login(username='plans-manager', password='test12345')
        response = self.client.get('/api/billing/plans/')
        self.assertEqual(response.status_code, 200)
        ids = [item['id'] for item in response.json()]
        self.assertEqual(ids, ['STARTER', 'PRO', 'BUSINESS'])

    def test_upgrade_rejects_enterprise_but_legacy_tenant_remains_readable(self):
        tenant = self.tenant('legacy', plan=Tenant.Plan.ENTERPRISE)
        self.user(tenant, 'legacy-manager')
        self.client.login(username='legacy-manager', password='test12345')
        response = self.client.post(
            '/api/billing/subscription/upgrade/',
            {'plan': 'ENTERPRISE', 'billing_cycle': 'MONTHLY'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Tenant.objects.get(pk=tenant.pk).plan, Tenant.Plan.ENTERPRISE)


class TestPrompt30SellerLimits(TestCase, Prompt30Factories):
    @override_settings(PLAN_SELLER_LIMITS={'STARTER': 5, 'PRO': 15, 'BUSINESS': 50})
    def test_unit_creation_enforces_plan_limits_and_ignores_other_tenants(self):
        cases = [('STARTER', 4, 201), ('STARTER', 5, 400), ('PRO', 14, 201), ('PRO', 15, 400), ('BUSINESS', 49, 201), ('BUSINESS', 50, 400)]
        for plan, existing, expected in cases:
            with self.subTest(plan=plan, existing=existing):
                tenant = self.tenant(f'{plan.lower()}-{existing}', plan=plan)
                self.user(tenant, f'{plan}-{existing}-manager')
                other = self.tenant(f'{plan.lower()}-{existing}-other', plan=plan)
                self.seller(other, f'{plan}-{existing}-other-seller', '11988880000')
                for i in range(existing):
                    self.seller(tenant, f'{plan}-{existing}-seller-{i}', f'1199999{i:04d}')
                self.client.login(username=f'{plan}-{existing}-manager', password='test12345')
                response = self.client.post(
                    '/api/sellers/',
                    {'name': 'Novo', 'phone': '11977770000'},
                    content_type='application/json',
                )
                self.assertEqual(response.status_code, expected)
                self.client.logout()

    @override_settings(PLAN_SELLER_LIMITS={'STARTER': 5, 'PRO': 15, 'BUSINESS': 50})
    @patch('app.apps.notifications.tasks.notify_seller_credentials')
    def test_import_above_capacity_creates_nothing_and_sends_no_whatsapp(self, notify):
        tenant = self.tenant('import-limit', plan=Tenant.Plan.STARTER)
        self.user(tenant, 'import-manager')
        for i in range(5):
            self.seller(tenant, f'import-seller-{i}', f'1199000{i:04d}')
        self.client.login(username='import-manager', password='test12345')
        upload = SimpleUploadedFile(
            'sellers.csv',
            b'nome,telefone\nNovo,11977770000\n',
            content_type='text/csv',
        )
        before_users = User.objects.count()
        before_sellers = Seller.objects.count()
        response = self.client.post('/api/sellers/import_sellers/', {'file': upload})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.count(), before_users)
        self.assertEqual(Seller.objects.count(), before_sellers)
        notify.assert_not_called()

    @override_settings(PLAN_SELLER_LIMITS={'STARTER': 5})
    @patch('app.apps.notifications.tasks.notify_seller_credentials')
    def test_import_response_never_contains_password(self, notify):
        tenant = self.tenant('import-ok', plan=Tenant.Plan.STARTER)
        self.user(tenant, 'import-ok-manager')
        self.client.login(username='import-ok-manager', password='test12345')
        upload = SimpleUploadedFile(
            'sellers.csv',
            b'nome,telefone\nNovo,11977770000\n',
            content_type='text/csv',
        )
        response = self.client.post('/api/sellers/import_sellers/', {'file': upload})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['criados'], 1)
        self.assertIn('username', body['created'][0])
        self.assertIn('name', body['created'][0])
        self.assertIn('uuid', body['created'][0])
        self.assertNotIn('password', body['created'][0])
        self.assertNotIn('password', response.content.decode().lower())

    @override_settings(PLAN_SELLER_LIMITS={'STARTER': 5})
    @patch('app.apps.notifications.tasks.notify_seller_credentials')
    def test_seller_create_retries_username_after_integrity_error(self, notify):
        tenant = self.tenant('seller-username-race', plan=Tenant.Plan.STARTER)
        self.user(tenant, 'seller-username-manager')
        self.client.login(username='seller-username-manager', password='test12345')
        original_create_user = User.objects.create_user
        attempts = []

        def create_user_with_one_collision(*args, **kwargs):
            attempts.append(kwargs.get('username'))
            if len(attempts) == 1:
                raise IntegrityError('duplicate username')
            return original_create_user(*args, **kwargs)

        with patch(
            'app.apps.api.serializers.User.objects.create_user',
            side_effect=create_user_with_one_collision,
        ):
            response = self.client.post(
                '/api/sellers/',
                {'name': 'Ana Silva', 'phone': '11977770000'},
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(attempts, ['ana-silva', 'ana-silva-2'])
        self.assertTrue(User.objects.filter(username='ana-silva-2').exists())
        notify.assert_called_once()


class TestPrompt30BillingSafety(TestCase, Prompt30Factories):
    def setUp(self):
        self.tenant_obj = self.tenant('billing-safe')
        self.user(self.tenant_obj, 'billing-manager')
        self.client.login(username='billing-manager', password='test12345')

    @override_settings(PLAN_PRICES={'PRO': 29700}, MP_ACCESS_TOKEN='test-token')
    @patch('app.apps.billing.views.MercadoPagoGateway')
    def test_upgrade_create_failure_does_not_cancel_old_subscription(self, gateway_cls):
        gateway = gateway_cls.return_value
        gateway.create_preapproval.side_effect = MercadoPagoError('temporary', retryable=True)
        Subscription.objects.create(
            tenant=self.tenant_obj,
            plan='STARTER',
            status=Subscription.Status.ACTIVE,
            gateway_subscription_id='old-sub',
        )
        response = self.client.post(
            '/api/billing/subscription/upgrade/',
            {'plan': 'PRO', 'billing_cycle': 'MONTHLY'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 502)
        gateway.cancel_preapproval.assert_not_called()
        sub = Subscription.objects.get(tenant=self.tenant_obj)
        self.assertEqual(sub.gateway_subscription_id, 'old-sub')
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

    @override_settings(PLAN_PRICES={'PRO': 29700}, MP_ACCESS_TOKEN='test-token')
    @patch('app.apps.billing.views.MercadoPagoGateway')
    def test_upgrade_pending_keeps_active_tenant_operational_and_defers_old_cancel(self, gateway_cls):
        gateway = gateway_cls.return_value
        gateway.create_preapproval.return_value = {'id': 'new-sub', 'init_point': 'https://mp'}
        gateway.cancel_preapproval.side_effect = MercadoPagoError('timeout', retryable=True)
        Subscription.objects.create(
            tenant=self.tenant_obj,
            plan='STARTER',
            status=Subscription.Status.ACTIVE,
            gateway_subscription_id='old-sub',
        )
        response = self.client.post(
            '/api/billing/subscription/upgrade/',
            {'plan': 'PRO', 'billing_cycle': 'MONTHLY'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        sub = Subscription.objects.get(tenant=self.tenant_obj)
        self.assertEqual(sub.gateway_subscription_id, 'new-sub')
        self.assertEqual(sub.pending_cancel_gateway_subscription_id, 'old-sub')
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertTrue(tenant_operational(self.tenant_obj))
        self.assertTrue(response.json()['cleanup_pending'])
        gateway.cancel_preapproval.assert_not_called()

    @override_settings(PLAN_PRICES={'PRO': 29700}, MP_ACCESS_TOKEN='test-token')
    @patch('app.apps.billing.views.MercadoPagoGateway')
    def test_abandoned_upgrade_does_not_remove_active_access(self, gateway_cls):
        gateway = gateway_cls.return_value
        gateway.create_preapproval.return_value = {'id': 'new-sub', 'init_point': 'https://mp'}
        Subscription.objects.create(
            tenant=self.tenant_obj,
            plan='STARTER',
            status=Subscription.Status.ACTIVE,
            gateway_subscription_id='old-sub',
        )
        response = self.client.post(
            '/api/billing/subscription/upgrade/',
            {'plan': 'PRO', 'billing_cycle': 'MONTHLY'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        sub = Subscription.objects.get(tenant=self.tenant_obj)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertEqual(sub.pending_cancel_gateway_subscription_id, 'old-sub')
        self.assertTrue(tenant_operational(self.tenant_obj))
        gateway.cancel_preapproval.assert_not_called()

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_confirmed_new_subscription_cancels_old_subscription(self, gateway_cls):
        gateway = gateway_cls.return_value
        gateway.get_preapproval.return_value = {
            'external_reference': str(self.tenant_obj.uuid),
            'status': 'authorized',
        }
        sub = Subscription.objects.create(
            tenant=self.tenant_obj,
            plan='PRO',
            status=Subscription.Status.ACTIVE,
            gateway_subscription_id='new-sub',
            pending_cancel_gateway_subscription_id='old-sub',
        )
        event = WebhookEvent.objects.create(
            gateway='mercadopago',
            payload={
                'type': 'subscription_preapproval',
                'data': {'id': 'new-sub'},
            },
        )

        from app.apps.webhooks.tasks import process_billing_webhook
        with self.captureOnCommitCallbacks(execute=True):
            process_billing_webhook(event.id)

        sub.refresh_from_db()
        self.tenant_obj.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertIsNone(sub.pending_cancel_gateway_subscription_id)
        self.assertEqual(self.tenant_obj.plan, Tenant.Plan.PRO)
        self.assertTrue(event.processed)
        gateway.cancel_preapproval.assert_called_once_with('old-sub')

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_stale_old_subscription_cancel_webhook_does_not_cancel_current_subscription(self, gateway_cls):
        gateway = gateway_cls.return_value
        gateway.get_preapproval.return_value = {
            'external_reference': str(self.tenant_obj.uuid),
            'status': 'cancelled',
        }
        sub = Subscription.objects.create(
            tenant=self.tenant_obj,
            plan='PRO',
            status=Subscription.Status.ACTIVE,
            gateway_subscription_id='new-sub',
        )
        event = WebhookEvent.objects.create(
            gateway='mercadopago',
            payload={
                'type': 'subscription_preapproval',
                'data': {'id': 'old-sub'},
            },
        )

        from app.apps.webhooks.tasks import process_billing_webhook
        process_billing_webhook(event.id)

        sub.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertTrue(event.processed)
        self.assertEqual(event.skip_reason, 'stale preapproval')

    @patch('app.apps.billing.views.MercadoPagoGateway')
    def test_cancel_remote_error_does_not_change_local_status(self, gateway_cls):
        gateway_cls.return_value.cancel_preapproval.side_effect = MercadoPagoError('mp down', retryable=True)
        sub = Subscription.objects.create(
            tenant=self.tenant_obj,
            plan='PRO',
            status=Subscription.Status.ACTIVE,
            gateway_subscription_id='sub-active',
        )
        response = self.client.post('/api/billing/subscription/cancel/')
        self.assertEqual(response.status_code, 502)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertEqual(sub.gateway_subscription_id, 'sub-active')


class TestPrompt30BillingWebhookRetry(TestCase, Prompt30Factories):
    def setUp(self):
        self.tenant_obj = self.tenant('retry')
        self.sub = Subscription.objects.create(
            tenant=self.tenant_obj,
            plan='PRO',
            status=Subscription.Status.PENDING,
            gateway_subscription_id='sub-retry',
        )

    def _event(self, payload):
        return WebhookEvent.objects.create(gateway='mercadopago', payload=payload)

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_payment_retryable_errors_remain_unprocessed(self, gateway_cls):
        from app.apps.webhooks.tasks import process_billing_webhook
        for status_code in (429, 500, None):
            with self.subTest(status_code=status_code):
                gateway_cls.return_value.get_payment.side_effect = MercadoPagoError(
                    'retryable', operation='get_payment', status_code=status_code, retryable=True,
                )
                event = self._event({'type': 'payment', 'data': {'id': f'pay-{status_code}'}})
                with self.assertRaises(MercadoPagoError):
                    process_billing_webhook(event.id)
                event.refresh_from_db()
                self.assertFalse(event.processed)

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_payment_404_is_processed_with_safe_skip_reason(self, gateway_cls):
        from app.apps.webhooks.tasks import process_billing_webhook
        gateway_cls.return_value.get_payment.side_effect = MercadoPagoError(
            'not found', operation='get_payment', status_code=404, retryable=False,
        )
        event = self._event({'type': 'payment', 'data': {'id': 'missing'}})
        process_billing_webhook(event.id)
        event.refresh_from_db()
        self.assertTrue(event.processed)
        self.assertIn('get_payment', event.skip_reason)

    @patch('app.services.gateway.mercadopago.MercadoPagoGateway')
    def test_preapproval_retryable_error_remains_unprocessed(self, gateway_cls):
        from app.apps.webhooks.tasks import process_billing_webhook
        gateway_cls.return_value.get_preapproval.side_effect = MercadoPagoError(
            'retryable', operation='get_preapproval', status_code=500, retryable=True,
        )
        event = self._event({'type': 'subscription_preapproval', 'data': {'id': 'sub-retry'}})
        with self.assertRaises(MercadoPagoError):
            process_billing_webhook(event.id)
        event.refresh_from_db()
        self.assertFalse(event.processed)

    @override_settings(MP_WEBHOOK_SECRET='')
    def test_webhook_without_secret_fails_closed(self):
        response = self.client.post(
            '/api/webhooks/billing/',
            data='{"type":"payment","data":{"id":"123"}}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 503)
        self.assertFalse(WebhookEvent.objects.filter(gateway='mercadopago').exists())


class TestPrompt30BootstrapSuperuser(TestCase):
    def test_bootstrap_creates_superuser_without_printing_password(self):
        out = io.StringIO()
        with patch.dict(os.environ, {
            'DJANGO_SUPERUSER_EMAIL': 'root@example.com',
            'DJANGO_SUPERUSER_PASSWORD': 'StrongPassword123!',
        }, clear=False):
            call_command('bootstrap_superuser', stdout=out)
        self.assertTrue(User.objects.filter(username='root@example.com', is_superuser=True).exists())
        self.assertNotIn('StrongPassword123!', out.getvalue())

    def test_bootstrap_is_idempotent_and_does_not_change_password(self):
        User.objects.create_superuser('root@example.com', 'root@example.com', 'OriginalPassword123!')
        with patch.dict(os.environ, {
            'DJANGO_SUPERUSER_EMAIL': 'root@example.com',
            'DJANGO_SUPERUSER_PASSWORD': 'DifferentPassword123!',
        }, clear=False):
            call_command('bootstrap_superuser', stdout=io.StringIO())
        user = User.objects.get(username='root@example.com')
        self.assertTrue(user.check_password('OriginalPassword123!'))

    def test_bootstrap_requires_env_and_minimum_length(self):
        with patch.dict(os.environ, {'DJANGO_SUPERUSER_EMAIL': '', 'DJANGO_SUPERUSER_PASSWORD': ''}, clear=False):
            with self.assertRaises(Exception):
                call_command('bootstrap_superuser', stdout=io.StringIO(), stderr=io.StringIO())
        with patch.dict(os.environ, {'DJANGO_SUPERUSER_EMAIL': 'root@example.com', 'DJANGO_SUPERUSER_PASSWORD': 'short'}, clear=False):
            with self.assertRaises(Exception):
                call_command('bootstrap_superuser', stdout=io.StringIO(), stderr=io.StringIO())


class TestPrompt30CrossTenant(TestCase, Prompt30Factories):
    def test_admin_cannot_create_sale_for_other_tenant_seller(self):
        tenant_a = self.tenant('tenant-a')
        tenant_b = self.tenant('tenant-b')
        admin = self.user(tenant_a, 'admin-a', role=User.Role.ADMIN)
        seller_b = self.seller(tenant_b, 'seller-b', '11966660000')
        request = type('Request', (), {'user': admin})()
        serializer = SaleCreateSerializer(
            data={'seller': str(seller_b.pk), 'origin': 'MANUAL', 'amount': 1000, 'sale_date': timezone.localdate()},
            context={'request': request},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('seller', serializer.errors)

    @override_settings(PLAN_SELLER_LIMITS={'STARTER': 5})
    def test_downgrade_counts_only_request_user_tenant(self):
        tenant_a = self.tenant('tenant-upgrade-a', plan=Tenant.Plan.PRO)
        tenant_b = self.tenant('tenant-upgrade-b', plan=Tenant.Plan.PRO)
        self.user(tenant_a, 'upgrade-a')
        for i in range(5):
            self.seller(tenant_b, f'other-{i}', f'1195000{i:04d}')
        self.client.login(username='upgrade-a', password='test12345')
        with patch('app.apps.billing.views.MercadoPagoGateway') as gateway_cls:
            gateway_cls.return_value.create_preapproval.return_value = {'id': 'new-sub', 'init_point': 'https://mp'}
            response = self.client.post(
                '/api/billing/subscription/upgrade/',
                {'plan': 'STARTER', 'billing_cycle': 'MONTHLY'},
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
