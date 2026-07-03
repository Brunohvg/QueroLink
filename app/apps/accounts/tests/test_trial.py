from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from app.apps.accounts.models import Tenant, User, tenant_operational
from app.apps.sellers.models import Seller


class BaseTrialTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Test Trial',
            slug='test-trial',
            is_active=True,
            trial_ends_at=timezone.now() - timedelta(days=1),
        )
        self.user = User.objects.create_user(
            username='manager', password='test123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )


class TestTenantOperational(BaseTrialTest):
    def test_active_not_expired(self):
        self.tenant.trial_ends_at = timezone.now() + timedelta(days=30)
        self.tenant.is_active = True
        self.assertTrue(tenant_operational(self.tenant))

    def test_suspended(self):
        self.tenant.is_active = False
        self.assertFalse(tenant_operational(self.tenant))

    def test_trial_expired(self):
        self.tenant.trial_ends_at = timezone.now() - timedelta(days=1)
        self.tenant.is_active = True
        self.assertFalse(tenant_operational(self.tenant))


class TestTrialEnforcementMiddleware(BaseTrialTest):
    def test_trial_expired_blocks_html(self):
        self.client.login(username='manager', password='test123')
        resp = self.client.get('/dashboard/gestor/fechamento/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('plano-expirado', resp.url)

    def test_trial_expired_blocks_api(self):
        self.client.login(username='manager', password='test123')
        resp = self.client.get('/api/manager/periodos/')
        self.assertEqual(resp.status_code, 402)
        self.assertIn('Assinatura expirada', resp.json().get('detail', ''))

    def test_suspended_blocks_html(self):
        self.tenant.is_active = False
        self.tenant.save(update_fields=['is_active'])
        self.client.login(username='manager', password='test123')
        resp = self.client.get('/dashboard/gestor/fechamento/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('plano-expirado', resp.url)

    def test_exempt_path_allows_access(self):
        self.client.login(username='manager', password='test123')
        resp = self.client.get('/dashboard/plano-expirado/')
        self.assertEqual(resp.status_code, 200)

    def test_webhook_is_not_blocked(self):
        self.client.login(username='manager', password='test123')
        resp = self.client.get('/api/webhooks/pagarme/test-trial/')
        self.assertNotEqual(resp.status_code, 402)

    def test_superuser_not_blocked(self):
        superuser = User.objects.create_superuser(
            username='admin', password='test123',
        )
        self.client.login(username='admin', password='test123')
        resp = self.client.get('/dashboard/gestor/fechamento/')
        self.assertNotEqual(resp.status_code, 302)

    def test_public_link_page_404_when_trial_expired(self):
        resp = self.client.get(f'/loja/{self.tenant.slug}/')
        self.assertEqual(resp.status_code, 404)

    def test_public_link_page_200_when_active(self):
        self.tenant.trial_ends_at = timezone.now() + timedelta(days=30)
        self.tenant.save(update_fields=['trial_ends_at'])
        Seller.objects.create(
            tenant=self.tenant,
            user=User.objects.create_user(
                username='seller_trial', password='test123',
                role=User.Role.SELLER, tenant=self.tenant,
            ),
            name='Seller Trial',
            is_active=True,
        )
        resp = self.client.get(f'/loja/{self.tenant.slug}/')
        self.assertEqual(resp.status_code, 200)
