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


class CommissionPeriodCustomDateFlowTest(TestCase):
    TEST_ONLY_MANAGER_PASSWORD = 'test-only-password-8371'

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Bibelo Datas', cnpj='44444444444444',
        )
        self.manager = User.objects.create_user(
            username='gestor_datas', password=self.TEST_ONLY_MANAGER_PASSWORD,
            role=User.Role.MANAGER, tenant=self.tenant,
        )

    def _auth_manager(self):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': self.manager.username,
            'password': self.TEST_ONLY_MANAGER_PASSWORD,
        }, format='json')
        self.assertIn('access', resp.data)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}')
        return client

    def test_manager_creates_custom_date_period_without_calendar_overwrite(self):
        client = self._auth_manager()

        response = client.post(reverse('api-commission-period-list'), {
            'label': '21/06 a 20/07',
            'start_date': '2026-06-21',
            'end_date': '2026-07-20',
            'expected_working_days': 22,
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        period = CommissionPeriod.objects.get(tenant=self.tenant)
        self.assertEqual(period.label, '21/06 a 20/07')
        self.assertEqual(period.start_date, date(2026, 6, 21))
        self.assertEqual(period.end_date, date(2026, 7, 20))
        self.assertEqual(period.month, 7)
        self.assertEqual(period.year, 2026)
        self.assertNotEqual(period.start_date, date(2026, 7, 1))

    def test_manager_create_validation_returns_friendly_errors(self):
        client = self._auth_manager()

        missing = client.post(reverse('api-commission-period-list'), {
            'label': 'Sem datas',
        }, format='json')
        self.assertEqual(missing.status_code, 400)
        self.assertIn('start_date', missing.data)
        self.assertIn('end_date', missing.data)

        inverted = client.post(reverse('api-commission-period-list'), {
            'label': 'Invertida',
            'start_date': '2026-07-20',
            'end_date': '2026-06-21',
        }, format='json')
        self.assertEqual(inverted.status_code, 400)
        self.assertIn('Data final deve ser maior', str(inverted.data))

        long_range = client.post(reverse('api-commission-period-list'), {
            'label': 'Longa',
            'start_date': '2026-01-01',
            'end_date': '2026-03-05',
        }, format='json')
        self.assertEqual(long_range.status_code, 201, long_range.data)
        self.assertEqual(long_range.data['start_date'], '2026-01-01')
        self.assertEqual(long_range.data['end_date'], '2026-03-05')

    def test_manager_can_create_two_free_periods_same_month_without_overlap(self):
        client = self._auth_manager()

        first = client.post(reverse('api-commission-period-list'), {
            'label': 'Julho 1a quinzena',
            'start_date': '2026-07-01',
            'end_date': '2026-07-15',
            'month': 7,
            'year': 2026,
            'expected_working_days': 11,
        }, format='json')
        self.assertEqual(first.status_code, 201, first.data)

        second = client.post(reverse('api-commission-period-list'), {
            'label': 'Julho 2a quinzena',
            'start_date': '2026-07-16',
            'end_date': '2026-07-31',
            'month': 7,
            'year': 2026,
            'expected_working_days': 12,
        }, format='json')
        self.assertEqual(second.status_code, 201, second.data)

        periods = CommissionPeriod.objects.filter(
            tenant=self.tenant, month=7, year=2026,
        ).exclude(status=CommissionPeriod.Status.CANCELADA)
        self.assertEqual(periods.count(), 2)

    def test_manager_create_overlap_returns_friendly_error(self):
        CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=6,
            year=2026,
            start_date=date(2026, 5, 21),
            end_date=date(2026, 6, 20),
        )
        client = self._auth_manager()

        response = client.post(reverse('api-commission-period-list'), {
            'label': 'Sobreposta',
            'start_date': '2026-06-15',
            'end_date': '2026-07-14',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('sobrepoe', str(response.data))

    def test_manager_can_recreate_period_after_cancellation(self):
        cancelled = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=8,
            year=2026,
            label='Agosto/2026',
            start_date=date(2026, 7, 21),
            end_date=date(2026, 8, 20),
            status=CommissionPeriod.Status.CANCELADA,
        )
        client = self._auth_manager()

        response = client.post(reverse('api-commission-period-list'), {
            'label': 'Agosto/2026',
            'start_date': '2026-07-21',
            'end_date': '2026-08-20',
            'expected_working_days': 22,
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        novo = CommissionPeriod.objects.filter(
            tenant=self.tenant, month=8, year=2026,
        ).exclude(status=CommissionPeriod.Status.CANCELADA).get()
        self.assertNotEqual(novo.uuid, cancelled.uuid)
        self.assertEqual(novo.status, CommissionPeriod.Status.ABERTA)
        self.assertEqual(novo.start_date, date(2026, 7, 21))
        self.assertEqual(novo.end_date, date(2026, 8, 20))

    def test_manager_edits_label_and_safe_range_with_date_fields(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=7,
            year=2026,
            label='Original',
            start_date=date(2026, 6, 21),
            end_date=date(2026, 7, 20),
        )
        client = self._auth_manager()

        response = client.patch(reverse('api-commission-period-detail', args=[period.uuid]), {
            'label': 'Ajustada',
            'start_date': '2026-06-22',
            'end_date': '2026-07-21',
            'month': 7,
            'year': 2026,
        }, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        period.refresh_from_db()
        self.assertEqual(period.label, 'Ajustada')
        self.assertEqual(period.start_date, date(2026, 6, 22))
        self.assertEqual(period.end_date, date(2026, 7, 21))

    def test_period_screen_uses_date_inputs_and_shows_label_range_helpers(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse('dashboard:gestor_fechamento'))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('x-model="createLabel"', html)
        self.assertIn('type="date" x-model="createStartDate"', html)
        self.assertIn('type="date" x-model="createEndDate"', html)
        self.assertIn('periodLabel(p)', html)
        self.assertIn('periodRange(p)', html)
