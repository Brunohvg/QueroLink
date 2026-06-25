from django.test import TestCase
from django.urls import reverse
from django.core.cache import cache
from datetime import date
from rest_framework.test import APIClient
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.commissions.models import (
    CommissionPeriod,
    SellerCommission,
    CommissionAdjustment,
)
from app.apps.sales.models import Sale


class CommissionPeriodStatusTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Bibelo', cnpj='11111111111111',
        )
        self.manager = User.objects.create_user(
            username='gestor', password='gestor123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.financeiro = User.objects.create_user(
            username='financeiro', password='fin123',
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='vendedor', password='senha123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Vendedor Teste', phone='111',
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
        client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}',
        )
        return client

    def _auth_manager(self):
        return self._auth(self.manager, 'gestor123')

    def _auth_financeiro(self):
        return self._auth(self.financeiro, 'fin123')

    def _sc_ids(self):
        return [self.sc.id]

    def test_close_aberta_works(self):
        self.assertEqual(
            self.period.status, CommissionPeriod.Status.ABERTA,
        )
        client = self._auth_manager()
        response = client.post(
            reverse('api-commission-period-close-sellers', args=[self.period.uuid]),
            {'seller_commission_ids': self._sc_ids()}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.period.refresh_from_db()
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.status, SellerCommission.Status.FECHADA)
        self.assertIsNotNone(self.sc.closed_at)
        self.assertEqual(self.sc.closed_by, self.manager)
        self.assertEqual(self.sc.total_sold_amount, 10000)

    def test_cannot_close_twice(self):
        self.sc.freeze(self.manager, commit=True)
        client = self._auth_manager()
        response = client.post(
            reverse('api-commission-period-close-sellers', args=[self.period.uuid]),
            {'seller_commission_ids': self._sc_ids()}, format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_mark_paid_from_fechada_works(self):
        self.sc.freeze(self.manager, commit=True)
        client = self._auth_financeiro()
        resp = client.post(
            reverse('api-commission-period-pay-sellers', args=[self.period.uuid]),
            {
                'seller_commission_ids': self._sc_ids(),
                'payment_date': '2026-07-01',
                'payment_method': 'pix',
                'payment_notes': 'Pago conforme acordado',
            }, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.status, SellerCommission.Status.PAGA)
        self.assertEqual(self.sc.paid_by, self.financeiro)
        self.assertEqual(self.sc.paid_amount, self.sc.commission_amount)
        self.assertEqual(self.sc.payment_method, 'pix')
        self.assertEqual(self.sc.payment_date, date(2026, 7, 1))

    def test_cannot_mark_paid_before_closed(self):
        client = self._auth_financeiro()
        resp = client.post(
            reverse('api-commission-period-pay-sellers', args=[self.period.uuid]),
            {'seller_commission_ids': self._sc_ids()}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_mark_paid_when_already_paid(self):
        self.sc.freeze(self.manager, commit=True)
        self.sc.mark_paid(self.financeiro, {'payment_date': date(2026, 7, 1)}, commit=True)
        client = self._auth_financeiro()
        resp = client.post(
            reverse('api-commission-period-pay-sellers', args=[self.period.uuid]),
            {'seller_commission_ids': self._sc_ids()}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_financeiro_can_mark_paid(self):
        self.sc.freeze(self.manager, commit=True)
        client = self._auth_financeiro()
        resp = client.post(
            reverse('api-commission-period-pay-sellers', args=[self.period.uuid]),
            {'seller_commission_ids': self._sc_ids()}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.status, SellerCommission.Status.PAGA)

    def test_cannot_cancel_paid(self):
        self.sc.freeze(self.manager, commit=True)
        self.sc.mark_paid(self.manager, {'payment_date': date(2026, 7, 1)}, commit=True)
        client = self._auth_manager()
        resp = client.post(
            reverse('api-commission-period-cancel', args=[self.period.uuid]),
            {'reason': 'Tentativa invalida'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_full_flow_close_mark_paid(self):
        client_mgr = self._auth_manager()
        client_fin = self._auth_financeiro()

        resp = client_mgr.post(
            reverse('api-commission-period-close-sellers', args=[self.period.uuid]),
            {'seller_commission_ids': self._sc_ids()}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.status, SellerCommission.Status.FECHADA)

        resp = client_fin.post(
            reverse('api-commission-period-pay-sellers', args=[self.period.uuid]),
            {
                'seller_commission_ids': self._sc_ids(),
                'payment_date': '2026-07-05',
            }, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.status, SellerCommission.Status.PAGA)


class SaleManualEntryBusinessRulesTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Bibelo', cnpj='22222222222222',
        )
        self.seller_user = User.objects.create_user(
            username='vendedor', password='senha123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Vendedor Teste', phone='111',
            user=self.seller_user,
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
        )
        SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=0.05,
        )

    def _auth_seller(self):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': 'vendedor', 'password': 'senha123',
        }, format='json')
        client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}',
        )
        return client

    def _create_sale(self, client, amount=5000, sale_date='2026-06-15'):
        return client.post(reverse('api-sale-list'), {
            'seller': str(self.seller.uuid),
            'amount': amount,
            'sale_date': sale_date,
            'origin': 'MANUAL',
        }, format='json')

    def test_cannot_create_in_closed_period(self):
        sc = SellerCommission.objects.get(
            period=self.period, seller=self.seller,
        )
        sc.freeze(self.seller_user, commit=True)
        client = self._auth_seller()
        response = self._create_sale(client, sale_date='2026-06-20')
        self.assertEqual(response.status_code, 400)


class CommissionPeriodLockedTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Bibelo', cnpj='33333333333333',
        )
        self.mgr = User.objects.create_user(
            username='manager', password='mgr123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='vendedor_lock', password='senha123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Seller Lock', phone='222',
            user=self.seller_user,
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=0.05,
        )

    def test_is_locked_for_fechada(self):
        self.sc.freeze(self.mgr, commit=True)
        locked = CommissionPeriod.is_locked_for(
            self.tenant, date(2026, 6, 15),
        )
        self.assertIn(self.seller.pk, locked)

    def test_is_locked_for_paga(self):
        self.sc.freeze(self.mgr, commit=True)
        self.sc.mark_paid(self.mgr, {'payment_date': date(2026, 7, 1)}, commit=True)
        locked = CommissionPeriod.is_locked_for(
            self.tenant, date(2026, 6, 15),
        )
        self.assertIn(self.seller.pk, locked)
