from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.core.cache import cache
from rest_framework.test import APIClient

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.commissions.models import (
    CommissionPeriod,
    SellerCommission,
)
from app.apps.audit.models import AuditLog


class AccountingSendTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Empresa AS',
            slug='empresa-as',
            default_commission_rate=Decimal('0.05'),
        )
        self.tenant2 = Tenant.objects.create(
            company_name='Outra AS',
            slug='outra-as',
            default_commission_rate=Decimal('0.05'),
        )

        self.manager = User.objects.create_user(
            username='manager_as', password='pass123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.admin = User.objects.create_user(
            username='admin_as', password='pass123',
            role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='seller_as', password='pass123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller_profile = Seller.objects.create(
            tenant=self.tenant, name='Vendedor AS',
            phone='11999999999', user=self.seller_user,
        )
        self.manager2 = User.objects.create_user(
            username='manager2_as', password='pass123',
            role=User.Role.MANAGER, tenant=self.tenant2,
        )

        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=6, year=2026,
            start_date='2026-06-01',
            end_date='2026-06-30',
            status=CommissionPeriod.Status.FECHADA,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period,
            seller=self.seller_profile,
            commission_rate=Decimal('0.05'),
            status=SellerCommission.Status.FECHADA,
            total_sold_amount=50000,
            commission_amount=2500,
        )

    def _auth(self, user):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': user.username, 'password': 'pass123',
        }, format='json')
        self.assertIn('access', resp.data)
        client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}'
        )
        return client

    def _send(self, client, period_uuid, force_resend=False):
        url = reverse('api-commission-period-send-accounting', args=[period_uuid])
        body = {}
        if force_resend:
            body['force_resend'] = True
        return client.post(url, body, format='json')

    def test_send_with_closed_period_suceeds(self):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()

        client = self._auth(self.manager)
        resp = self._send(client, self.period.uuid)
        self.assertIn(resp.status_code, (200, 202))

    def test_without_accountant_email_returns_400(self):
        client = self._auth(self.manager)
        resp = self._send(client, self.period.uuid)
        self.assertEqual(resp.status_code, 400)

    def test_open_seller_blocks_send(self):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()
        self.sc.status = SellerCommission.Status.ABERTA
        self.sc.save()

        client = self._auth(self.manager)
        resp = self._send(client, self.period.uuid)
        self.assertEqual(resp.status_code, 409)

    def test_reopened_seller_blocks_send(self):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()
        self.sc.status = SellerCommission.Status.REABERTA
        self.sc.save()

        client = self._auth(self.manager)
        resp = self._send(client, self.period.uuid)
        self.assertEqual(resp.status_code, 409)

    def test_paid_and_closed_allows_send(self):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()
        self.sc.status = SellerCommission.Status.PAGA
        self.sc.save()

        client = self._auth(self.manager)
        resp = self._send(client, self.period.uuid)
        self.assertIn(resp.status_code, (200, 202))

    def test_cancelled_period_returns_409(self):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()
        self.period.status = CommissionPeriod.Status.CANCELADA
        self.period.save()

        client = self._auth(self.manager)
        resp = self._send(client, self.period.uuid)
        self.assertEqual(resp.status_code, 409)

    def test_other_tenant_cannot_send(self):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()

        client = self._auth(self.manager2)
        resp = self._send(client, self.period.uuid)
        self.assertIn(resp.status_code, (403, 404))

    def test_seller_receives_403(self):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()

        client = self._auth(self.seller_user)
        resp = self._send(client, self.period.uuid)
        self.assertEqual(resp.status_code, 403)

    @patch('app.apps.notifications.tasks.send_accounting_package_email.delay')
    def test_send_registers_audit_log(self, mock_delay):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()

        client = self._auth(self.manager)
        resp = self._send(client, self.period.uuid)
        self.assertIn(resp.status_code, (200, 202))

        logs = AuditLog.objects.filter(
            action='commission_period.send_accounting',
            tenant=self.tenant,
        )
        self.assertEqual(logs.count(), 1)

    @patch('app.apps.notifications.tasks.send_accounting_package_email.delay')
    def test_send_dispatches_task(self, mock_delay):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()

        client = self._auth(self.admin)
        resp = self._send(client, self.period.uuid)
        self.assertIn(resp.status_code, (200, 202))
        mock_delay.assert_called_once_with(
            str(self.tenant.uuid),
            self.period.month,
            self.period.year,
            requested_by_user_id=str(self.admin.pk),
        )

    def test_already_sent_without_force_returns_409(self):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()
        self.period.sent_to_accounting_at = '2026-06-15T10:00:00Z'
        self.period.save()

        client = self._auth(self.manager)
        resp = self._send(client, self.period.uuid)
        self.assertEqual(resp.status_code, 409)

    @patch('app.apps.notifications.tasks.send_accounting_package_email.delay')
    def test_force_resend_allows(self, mock_delay):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()
        self.period.sent_to_accounting_at = '2026-06-15T10:00:00Z'
        self.period.save()

        client = self._auth(self.manager)
        resp = self._send(client, self.period.uuid, force_resend=True)
        self.assertIn(resp.status_code, (200, 202))
        mock_delay.assert_called_once()

    def test_period_with_no_sellers_returns_400(self):
        self.tenant.accountant_email = 'contador@teste.com'
        self.tenant.save()
        self.sc.delete()

        client = self._auth(self.manager)
        resp = self._send(client, self.period.uuid)
        self.assertEqual(resp.status_code, 400)
