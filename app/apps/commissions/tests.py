from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale
from app.apps.commissions.models import (
    CommissionPeriod,
    SellerCommission,
    CommissionAdjustment,
)
from app.apps.commissions.services import (
    get_manual_sales_total,
    get_commission_rate,
    calculate_estimated_commission,
    get_or_create_period,
    sync_period_seller_commissions,
    close_seller_commissions,
    reopen_seller_commissions,
    pay_seller_commissions,
    create_commission_adjustment,
    calculate_seller_working_days,
    calculate_period_summary,
    recalculate_period_status,
    validate_sale_can_be_changed,
    get_dashboard_data,
    get_links_data,
)

User = get_user_model()


class BaseTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Test Empresa',
            slug='test-empresa',
            default_commission_rate=Decimal('0.01'),
            is_active=True,
        )
        self.manager = User.objects.create_user(
            username='manager',
            password='test123',
            role=User.Role.MANAGER,
            tenant=self.tenant,
        )
        self.admin = User.objects.create_user(
            username='admin',
            password='test123',
            role=User.Role.ADMIN,
            tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='seller1',
            password='test123',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Bruno',
            phone='55999999999',
            commission_rate=Decimal('0.01'),
            is_active=True,
        )
        self.seller2 = Seller.objects.create(
            tenant=self.tenant,
            user=User.objects.create_user(
                username='seller2', password='test123',
                role=User.Role.SELLER, tenant=self.tenant,
            ),
            name='Celia',
            phone='55999999998',
            commission_rate=Decimal('0.01'),
            is_active=True,
        )

    def _create_manual_sale(self, seller, amount_cents, day, month=6, year=2026):
        return Sale.objects.create(
            tenant=self.tenant,
            seller=seller,
            origin=Sale.Origin.MANUAL,
            amount=amount_cents,
            sale_date=date(year, month, day),
            created_by=self.manager,
        )

    def _create_period(self, month=6, year=2026, expected_days=22):
        period, _ = get_or_create_period(
            self.tenant, month, year, expected_working_days=expected_days,
        )
        return period


class TestManualSalesTotal(BaseTest):
    def test_get_manual_sales_total(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        total = get_manual_sales_total(self.seller, 6, 2026)
        self.assertEqual(total, 8503050)

    def test_get_manual_sales_total_empty(self):
        total = get_manual_sales_total(self.seller, 6, 2026)
        self.assertEqual(total, 0)


class TestCommissionRate(BaseTest):
    def test_get_commission_rate_from_seller(self):
        rate = get_commission_rate(self.seller)
        self.assertEqual(rate, Decimal('0.01'))


class TestCalculateEstimatedCommission(BaseTest):
    def test_one_percent_8503050_gives_85031(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        commission, total = calculate_estimated_commission(self.seller, 6, 2026)
        self.assertEqual(total, 8503050)
        self.assertEqual(commission, 85031)


class TestGetOrCreatePeriod(BaseTest):
    def test_creates_period_with_expected_days(self):
        period, created = get_or_create_period(self.tenant, 6, 2026, expected_working_days=27)
        self.assertTrue(created)
        self.assertEqual(period.expected_working_days, 27)
        self.assertEqual(period.status, CommissionPeriod.Status.ABERTA)

    def test_does_not_create_duplicate(self):
        get_or_create_period(self.tenant, 6, 2026)
        period2, created = get_or_create_period(self.tenant, 6, 2026)
        self.assertFalse(created)
        self.assertIsNotNone(period2.uuid)


class TestSyncPeriodSellerCommissions(BaseTest):
    def test_sync_creates_commission_for_active_seller(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
            expected_working_days=22,
        )
        created = sync_period_seller_commissions(period)
        self.assertEqual(created, 2)

    def test_sync_recalculates_open_period(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        self.assertEqual(sc.total_sold_amount, 8503050)
        self.assertEqual(sc.commission_amount, 85031)
        self.assertEqual(sc.operational_status, SellerCommission.OperationalStatus.PENDENTE)


class TestWorkingDays(BaseTest):
    def test_calculate_working_days(self):
        self._create_manual_sale(self.seller, 100000, 15)
        period = self._create_period(expected_days=22)
        count, expected, missing = calculate_seller_working_days(period, self.seller)
        self.assertEqual(count, 1)
        self.assertEqual(expected, 22)
        self.assertEqual(missing, 21)

    def test_empty_seller_has_zero_days(self):
        period = self._create_period(expected_days=22)
        count, expected, missing = calculate_seller_working_days(period, self.seller)
        self.assertEqual(count, 0)
        self.assertEqual(missing, 22)

    def test_operational_status_pronto(self):
        for day in range(1, 23):
            self._create_manual_sale(self.seller, 1000, day, month=7, year=2026)
        period, _ = get_or_create_period(
            self.tenant, 7, 2026, expected_working_days=22,
        )
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        self.assertEqual(sc.operational_status, SellerCommission.OperationalStatus.PRONTO)

    def test_operational_status_sem_lancamento(self):
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        self.assertEqual(sc.operational_status, SellerCommission.OperationalStatus.SEM_LANCAMENTO)


class TestCloseSellerCommissions(BaseTest):
    def test_close_single_seller(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)

        result = close_seller_commissions(period, [sc.id], self.manager)
        self.assertEqual(len(result), 1)

        sc.refresh_from_db()
        self.assertEqual(sc.status, SellerCommission.Status.FECHADA)
        self.assertIsNotNone(sc.closed_at)
        self.assertEqual(sc.closed_by, self.manager)
        self.assertIsNotNone(sc.frozen_total_sold_amount)
        self.assertEqual(sc.frozen_total_sold_amount, 8503050)
        self.assertEqual(sc.frozen_commission_amount, 85031)

        period.refresh_from_db()
        self.assertEqual(period.status, CommissionPeriod.Status.PARCIALMENTE_FECHADA)

    def test_close_multiple_sellers(self):
        self._create_manual_sale(self.seller, 500000, 15)
        self._create_manual_sale(self.seller2, 300000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        ids = list(SellerCommission.objects.filter(period=period).values_list('id', flat=True))

        result = close_seller_commissions(period, ids, self.manager)
        self.assertEqual(len(result), 2)

        period.refresh_from_db()
        self.assertEqual(period.status, CommissionPeriod.Status.FECHADA)

    def test_other_seller_stays_open(self):
        self._create_manual_sale(self.seller, 500000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc1 = SellerCommission.objects.get(period=period, seller=self.seller)
        sc2 = SellerCommission.objects.get(period=period, seller=self.seller2)

        close_seller_commissions(period, [sc1.id], self.manager)
        sc2.refresh_from_db()
        self.assertEqual(sc2.status, SellerCommission.Status.ABERTA)

        period.refresh_from_db()
        self.assertEqual(period.status, CommissionPeriod.Status.PARCIALMENTE_FECHADA)


class TestReopenSellerCommissions(BaseTest):
    def test_reopen_before_payment(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        reopen_seller_commissions(period, [sc.id], self.manager, 'Teste reversao')
        sc.refresh_from_db()
        self.assertEqual(sc.status, SellerCommission.Status.REABERTA)
        self.assertIsNotNone(sc.reopened_at)
        self.assertEqual(sc.reopen_reason, 'Teste reversao')

    def test_reopen_requires_reason(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        with self.assertRaises(ValueError):
            reopen_seller_commissions(period, [sc.id], self.manager, '')

    def test_paid_cannot_be_reopened(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)
        pay_seller_commissions(period, [sc.id], self.manager, {
            'payment_method': 'pix',
        })

        with self.assertRaises(ValueError):
            reopen_seller_commissions(period, [sc.id], self.manager, 'Tentar')


class TestPaySellerCommissions(BaseTest):
    def test_pay_single_seller(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        pay_seller_commissions(period, [sc.id], self.manager, {
            'payment_method': 'pix',
            'payment_date': '2026-07-01',
            'payment_notes': 'Teste',
        })
        sc.refresh_from_db()
        self.assertEqual(sc.status, SellerCommission.Status.PAGA)
        self.assertIsNotNone(sc.paid_at)
        self.assertEqual(sc.paid_amount, 85031)
        self.assertEqual(sc.payment_method, 'pix')


class TestCommissionAdjustment(BaseTest):
    def test_create_adjustment(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        adjustment = create_commission_adjustment(sc, 100000, 'Ajuste teste', self.manager)
        self.assertEqual(adjustment.previous_amount, 85031)
        self.assertEqual(adjustment.new_amount, 100000)


class TestValidateSaleCanBeChanged(BaseTest):
    def test_seller_can_edit_when_open(self):
        can, msg = validate_sale_can_be_changed(self.seller, date(2026, 6, 15), self.seller_user)
        self.assertTrue(can)

    def test_seller_cannot_edit_when_closed(self):
        self._create_manual_sale(self.seller, 500000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        can, msg = validate_sale_can_be_changed(self.seller, date(2026, 6, 15), self.seller_user)
        self.assertFalse(can)
        self.assertIsNotNone(msg)

    def test_other_seller_can_edit_when_only_one_closed(self):
        self._create_manual_sale(self.seller, 500000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        can, msg = validate_sale_can_be_changed(self.seller2, date(2026, 6, 15), self.seller_user)
        self.assertTrue(can)


class TestPeriodSummary(BaseTest):
    def test_summary_with_mixed_statuses(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        self._create_manual_sale(self.seller2, 420000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)

        sc1 = SellerCommission.objects.get(period=period, seller=self.seller)
        sc2 = SellerCommission.objects.get(period=period, seller=self.seller2)
        close_seller_commissions(period, [sc1.id], self.manager)

        summary = calculate_period_summary(period)
        self.assertEqual(summary['vendedores_abertos'], 1)
        self.assertEqual(summary['vendedores_fechados'], 1)
        self.assertGreater(summary['total_vendido'], 0)


class TestRecalculatePeriodStatus(BaseTest):
    def test_all_open_is_aberta(self):
        period = self._create_period()
        sync_period_seller_commissions(period)
        status = recalculate_period_status(period)
        self.assertEqual(status, CommissionPeriod.Status.ABERTA)

    def test_all_closed_is_fechada(self):
        self._create_manual_sale(self.seller, 100000, 15)
        self._create_manual_sale(self.seller2, 100000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        ids = list(SellerCommission.objects.filter(period=period).values_list('id', flat=True))
        close_seller_commissions(period, ids, self.manager)
        period.refresh_from_db()
        self.assertEqual(period.status, CommissionPeriod.Status.FECHADA)

    def test_one_paid_is_parcialmente_paga(self):
        self._create_manual_sale(self.seller, 100000, 15)
        self._create_manual_sale(self.seller2, 100000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc1 = SellerCommission.objects.get(period=period, seller=self.seller)
        sc2 = SellerCommission.objects.get(period=period, seller=self.seller2)
        close_seller_commissions(period, [sc1.id, sc2.id], self.manager)
        pay_seller_commissions(period, [sc1.id], self.manager, {'payment_method': 'pix'})
        period.refresh_from_db()
        self.assertEqual(period.status, CommissionPeriod.Status.PARCIALMENTE_PAGA)

    def test_all_paid_is_paga(self):
        self._create_manual_sale(self.seller, 100000, 15)
        self._create_manual_sale(self.seller2, 100000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        ids = list(SellerCommission.objects.filter(period=period).values_list('id', flat=True))
        close_seller_commissions(period, ids, self.manager)
        pay_seller_commissions(period, ids, self.manager, {'payment_method': 'pix'})
        period.refresh_from_db()
        self.assertEqual(period.status, CommissionPeriod.Status.PAGA)


class TestDashboardData(BaseTest):
    def test_dashboard_shows_total_vendido(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        self._create_period()
        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertEqual(data['total_vendido'], 8503050)

    def test_dashboard_shows_commission_for_aberta(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        self._create_period()
        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertGreater(data['commission_aberta'], 0)

    def test_dashboard_shows_commission_for_paga(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)
        pay_seller_commissions(period, [sc.id], self.manager, {'payment_method': 'pix'})

        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertGreater(data['commission_paga'], 0)

    def test_dashboard_shows_vendor_counts(self):
        period = self._create_period()
        sync_period_seller_commissions(period)
        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertEqual(data['vendedores_ativos'], 2)
        self.assertEqual(data['vendedores_sem_lancamento_periodo'], 2)

    def test_dashboard_excludes_link_sales(self):
        self._create_manual_sale(self.seller, 50000, 15)
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.LINK, amount=99999999,
            sale_date=date(2026, 6, 15), created_by=self.manager,
        )
        self._create_period()
        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertEqual(data['total_vendido'], 50000)


class TestGetLinksData(BaseTest):
    def test_links_data_empty(self):
        data = get_links_data(self.tenant, 6, 2026)
        self.assertEqual(data['links_gerados'], 0)

    def test_links_data_with_order(self):
        from app.apps.orders.models import Order
        Order.objects.create(
            tenant=self.tenant, seller=self.seller,
            customer_name='Test', total_amount=10000, status='PENDING',
        )
        data = get_links_data(self.tenant, 6, 2026)
        self.assertEqual(data['links_gerados'], 1)
        self.assertEqual(data['links_pendentes'], 1)


class TestValidationAndBlocking(BaseTest):
    def test_seller_cannot_edit_when_own_commission_closed(self):
        self._create_manual_sale(self.seller, 500000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=100000,
            sale_date=date(2026, 6, 20), created_by=self.seller_user,
        )
        can, msg = validate_sale_can_be_changed(
            self.seller, date(2026, 6, 20), self.seller_user,
        )
        self.assertFalse(can)

    def test_other_seller_still_editable(self):
        self._create_manual_sale(self.seller, 500000, 15)
        self._create_manual_sale(self.seller2, 300000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        can, msg = validate_sale_can_be_changed(
            self.seller2, date(2026, 6, 20), self.manager,
        )
        self.assertTrue(can)


class TestPermissions(BaseTest):
    def setUp(self):
        super().setUp()
        self.financeiro = User.objects.create_user(
            username='financeiro', password='test123',
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )
        self._create_manual_sale(self.seller, 100000, 15)
        self.period = self._create_period()
        sync_period_seller_commissions(self.period)
        self.sc = SellerCommission.objects.get(period=self.period, seller=self.seller)
        self.sc_id = [self.sc.id]

    def test_manager_can_close_seller(self):
        result = close_seller_commissions(self.period, self.sc_id, self.manager)
        self.assertEqual(len(result), 1)

    def test_manager_can_reopen_before_payment(self):
        close_seller_commissions(self.period, self.sc_id, self.manager)
        result = reopen_seller_commissions(self.period, self.sc_id, self.manager, 'teste')
        self.assertEqual(len(result), 1)

    def test_manager_cannot_pay(self):
        from app.apps.api.permissions import IsFinancialOrAdmin
        class MockRequest:
            user = self.manager
            method = 'POST'
        perm = IsFinancialOrAdmin()
        self.assertFalse(perm.has_permission(MockRequest(), None))

    def test_financeiro_can_pay(self):
        close_seller_commissions(self.period, self.sc_id, self.manager)
        result = pay_seller_commissions(self.period, self.sc_id, self.financeiro, {
            'payment_method': 'pix',
        })
        self.assertEqual(len(result), 1)
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.status, SellerCommission.Status.PAGA)

    def test_financeiro_cannot_close(self):
        from app.apps.api.permissions import IsManagerOrAdmin
        class MockRequest:
            user = self.financeiro
            method = 'POST'
        perm = IsManagerOrAdmin()
        self.assertFalse(perm.has_permission(MockRequest(), None))

    def test_financeiro_cannot_reopen(self):
        from app.apps.api.permissions import IsManagerOrAdmin
        class MockRequest:
            user = self.financeiro
            method = 'POST'
        perm = IsManagerOrAdmin()
        self.assertFalse(perm.has_permission(MockRequest(), None))

    def test_admin_can_close_reopen_pay(self):
        close_seller_commissions(self.period, self.sc_id, self.admin)
        reopen_seller_commissions(self.period, self.sc_id, self.admin, 'teste')
        close_seller_commissions(self.period, self.sc_id, self.admin)
        pay_seller_commissions(self.period, self.sc_id, self.admin, {'payment_method': 'pix'})
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.status, SellerCommission.Status.PAGA)

    def test_paid_cannot_be_reopened(self):
        close_seller_commissions(self.period, self.sc_id, self.manager)
        pay_seller_commissions(self.period, self.sc_id, self.financeiro, {'payment_method': 'pix'})
        with self.assertRaises(ValueError):
            reopen_seller_commissions(self.period, self.sc_id, self.manager, 'teste')
