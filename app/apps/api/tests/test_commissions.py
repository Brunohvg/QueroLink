from django.test import TestCase
from django.urls import reverse
from django.core.cache import cache
from rest_framework.test import APIClient
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.sales.models import Sale


class CommissionPeriodStatusTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(company_name="Bibelo", cnpj="11111111111111")
        self.manager = User.objects.create_user(
            username="gestor", password="gestor123",
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.financeiro = User.objects.create_user(
            username="financeiro", password="fin123",
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username="vendedor", password="senha123",
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name="Vendedor Teste", phone="111",
            user=self.seller_user,
        )

        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=0.05,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date='2026-06-10', created_by=self.seller_user,
        )

    def _auth(self, user, password):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': user.username, 'password': password,
        }, format='json')
        self.assertIn('access', resp.data, f"Login failed for {user.username}")
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}')
        return client

    def _auth_manager(self):
        return self._auth(self.manager, 'gestor123')

    def _auth_financeiro(self):
        return self._auth(self.financeiro, 'fin123')

    def _url(self, action):
        url_action = action.replace('_', '-')
        return reverse(f'api-commission-period-{url_action}', args=[self.period.uuid])

    def test_close_aberta_works(self):
        self.assertEqual(self.period.status, CommissionPeriod.Status.ABERTA)
        client = self._auth_manager()
        response = client.post(self._url('close'))
        self.assertEqual(response.status_code, 200)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, CommissionPeriod.Status.EM_CONFERENCIA)
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.total_sold_amount, 10000)

    def test_cannot_close_twice(self):
        self.period.status = CommissionPeriod.Status.EM_CONFERENCIA
        self.period.save()
        client = self._auth_manager()
        response = client.post(self._url('close'))
        self.assertEqual(response.status_code, 400)

    def test_cannot_approve_before_send(self):
        client = self._auth_financeiro()
        response = client.post(self._url('approve'))
        self.assertEqual(response.status_code, 400)

    def test_full_flow_close_send_approve_mark_paid(self):
        client_mgr = self._auth_manager()
        client_fin = self._auth_financeiro()

        for action, expected_status in [
            ('close', CommissionPeriod.Status.EM_CONFERENCIA),
            ('send', CommissionPeriod.Status.ENVIADA_FINANCEIRO),
        ]:
            resp = client_mgr.post(self._url(action))
            self.assertEqual(resp.status_code, 200, f"Action {action} failed")
            self.period.refresh_from_db()
            self.assertEqual(self.period.status, expected_status)

        for action, expected_status in [
            ('approve', CommissionPeriod.Status.APROVADA),
            ('mark_paid', CommissionPeriod.Status.PAGA),
        ]:
            resp = client_fin.post(self._url(action))
            self.assertEqual(resp.status_code, 200, f"Action {action} failed")
            self.period.refresh_from_db()
            self.assertEqual(self.period.status, expected_status)

    def test_reject_sends_back_to_conferencia(self):
        self.period.status = CommissionPeriod.Status.ENVIADA_FINANCEIRO
        self.period.save()
        client = self._auth_financeiro()
        resp = client.post(self._url('reject'))
        self.assertEqual(resp.status_code, 200)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, CommissionPeriod.Status.EM_CONFERENCIA)
        self.assertIsNone(self.period.sent_to_financial_at)

    def test_manager_cannot_approve(self):
        self.period.status = CommissionPeriod.Status.ENVIADA_FINANCEIRO
        self.period.save()
        client = self._auth_manager()
        resp = client.post(self._url('approve'))
        self.assertEqual(resp.status_code, 403)

    def test_cannot_mark_paid_before_approved(self):
        client = self._auth_financeiro()
        resp = client.post(self._url('mark_paid'))
        self.assertEqual(resp.status_code, 400)
