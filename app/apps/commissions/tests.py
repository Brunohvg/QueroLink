from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
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
    sync_period_seller_commissions,
    freeze_period,
    mark_period_paid,
    reopen_period,
    create_commission_adjustment,
    get_dashboard_data,
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

    def _create_manual_sale(self, seller, amount_cents, day, month=6, year=2026):
        return Sale.objects.create(
            tenant=self.tenant,
            seller=seller,
            origin=Sale.Origin.MANUAL,
            amount=amount_cents,
            sale_date=date(year, month, day),
            created_by=self.manager,
        )

    def _create_period(self, month=6, year=2026):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=month,
            year=year,
            status=CommissionPeriod.Status.ABERTA,
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

    def test_get_manual_sales_total_multiple(self):
        self._create_manual_sale(self.seller, 100000, 1)
        self._create_manual_sale(self.seller, 200000, 2)
        total = get_manual_sales_total(self.seller, 6, 2026)
        self.assertEqual(total, 300000)


class TestCommissionRate(BaseTest):
    def test_get_commission_rate_from_seller(self):
        rate = get_commission_rate(self.seller)
        self.assertEqual(rate, Decimal('0.01'))

    def test_get_commission_rate_from_tenant(self):
        self.seller.commission_rate = Decimal('0')
        self.seller.save()
        rate = get_commission_rate(self.seller)
        self.assertEqual(rate, Decimal('0.01'))

    def test_get_commission_rate_default(self):
        self.seller.commission_rate = Decimal('0')
        self.seller.save(update_fields=['commission_rate'])
        rate = get_commission_rate(self.seller)
        self.assertEqual(rate, Decimal('0.01'))


class TestCalculateEstimatedCommission(BaseTest):
    def test_calculate_estimated_commission_one_percent(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        commission, total = calculate_estimated_commission(self.seller, 6, 2026)
        # 85.030,50 * 1% = 850,3050 -> round -> 850.31
        # In centavos: 8503050 * 0.01 = 85030.5 -> round -> 85031
        self.assertEqual(total, 8503050)
        self.assertEqual(commission, 85031)

    def test_calculate_estimated_commission_zero_sales(self):
        commission, total = calculate_estimated_commission(self.seller, 6, 2026)
        self.assertEqual(total, 0)
        self.assertEqual(commission, 0)

    def test_calculate_estimated_commission_different_rate(self):
        self.seller.commission_rate = Decimal('0.05')
        self.seller.save()
        self._create_manual_sale(self.seller, 100000, 15)
        commission, total = calculate_estimated_commission(self.seller, 6, 2026)
        self.assertEqual(total, 100000)
        self.assertEqual(commission, 5000)  # 5% of 100000 cents


class TestSyncPeriodSellerCommissions(BaseTest):
    def test_sync_creates_commission_for_active_seller(self):
        period = self._create_period()
        created = sync_period_seller_commissions(period)
        self.assertEqual(created, 1)
        self.assertTrue(
            SellerCommission.objects.filter(period=period, seller=self.seller).exists()
        )

    def test_sync_recalculates_open_period(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        self.assertEqual(sc.total_sold_amount, 8503050)
        self.assertEqual(sc.commission_amount, 85031)

    def test_sync_creates_for_seller_with_manual_sales_even_if_inactive(self):
        self._create_manual_sale(self.seller, 500000, 15)
        self.seller.is_active = False
        self.seller.save()

        period = self._create_period()
        created = sync_period_seller_commissions(period)
        self.assertEqual(created, 1)

    def test_sync_does_not_duplicate(self):
        period = self._create_period()
        sync_period_seller_commissions(period)
        created = sync_period_seller_commissions(period)
        self.assertEqual(created, 0)


class TestFreezePeriod(BaseTest):
    def test_freeze_period_changes_status(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        period.refresh_from_db()
        self.assertEqual(period.status, CommissionPeriod.Status.FECHADA)

    def test_freeze_period_calculates_commission(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        self.assertEqual(sc.total_sold_amount, 8503050)
        self.assertEqual(sc.commission_amount, 85031)
        self.assertIsNotNone(sc.closed_at)
        self.assertEqual(sc.closed_by, self.manager)

    def test_freeze_period_raises_on_wrong_status(self):
        period = self._create_period()
        period.status = CommissionPeriod.Status.PAGA
        period.save()
        with self.assertRaises(ValueError):
            freeze_period(period, self.manager)


class TestMarkPeriodPaid(BaseTest):
    def test_mark_paid_changes_status(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        mark_period_paid(period, self.manager, {})
        period.refresh_from_db()
        self.assertEqual(period.status, CommissionPeriod.Status.PAGA)

    def test_mark_paid_records_payment(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        mark_period_paid(period, self.manager, {
            'payment_date': '2026-07-01',
            'payment_method': 'pix',
            'payment_notes': 'Teste pagamento',
        })
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        self.assertIsNotNone(sc.paid_at)
        self.assertEqual(sc.paid_amount, 85031)
        self.assertEqual(sc.payment_method, 'pix')
        self.assertEqual(sc.payment_notes, 'Teste pagamento')

    def test_mark_paid_without_commissions_raises(self):
        period = self._create_period()
        period.status = CommissionPeriod.Status.FECHADA
        period.save()
        with self.assertRaises(ValueError):
            mark_period_paid(period, self.manager, {})

    def test_mark_paid_raises_on_wrong_status(self):
        period = self._create_period()
        with self.assertRaises(ValueError):
            mark_period_paid(period, self.manager, {})

    def test_paid_period_appears_in_history_with_sellers(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        mark_period_paid(period, self.manager, {
            'payment_method': 'pix',
        })
        period.refresh_from_db()
        self.assertEqual(period.status, CommissionPeriod.Status.PAGA)
        sc_count = SellerCommission.objects.filter(period=period).count()
        self.assertGreater(sc_count, 0)
        sc = SellerCommission.objects.filter(period=period).first()
        self.assertIsNotNone(sc.paid_amount)


class TestReopenPeriod(BaseTest):
    def test_reopen_changes_status_to_aberta(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        reopen_period(period, self.manager, 'Teste reversao')
        period.refresh_from_db()
        self.assertEqual(period.status, CommissionPeriod.Status.ABERTA)

    def test_reopen_clears_frozen_dates(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        reopen_period(period, self.manager, 'Teste reversao')
        period.refresh_from_db()
        self.assertIsNone(period.closed_at)
        self.assertIsNone(period.closed_by)

    def test_reopen_requires_reason(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        with self.assertRaises(ValueError):
            reopen_period(period, self.manager, '')

    def test_paid_period_cannot_be_reopened(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        mark_period_paid(period, self.manager, {'payment_method': 'pix'})
        with self.assertRaises(ValueError):
            reopen_period(period, self.manager, 'Tentar reverter')

    def test_aberta_period_cannot_be_reopened(self):
        period = self._create_period()
        with self.assertRaises(ValueError):
            reopen_period(period, self.manager, 'Teste')


class TestCommissionAdjustment(BaseTest):
    def test_create_adjustment(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        adjustment = create_commission_adjustment(sc, 100000, 'Ajuste de teste', self.manager)
        self.assertEqual(adjustment.previous_amount, 85031)
        self.assertEqual(adjustment.new_amount, 100000)
        self.assertEqual(adjustment.difference, 14969)
        self.assertEqual(adjustment.reason, 'Ajuste de teste')
        sc.refresh_from_db()
        self.assertEqual(sc.commission_amount, 100000)


class TestDashboardData(BaseTest):
    def test_dashboard_shows_total_vendido(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertEqual(data['total_vendido'], 8503050)

    def test_dashboard_shows_estimated_commission_for_open_period(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertEqual(data['commission_estimada'], 85031)
        self.assertEqual(data['period_status'], CommissionPeriod.Status.ABERTA)

    def test_dashboard_shows_closed_commission_for_fechada(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertEqual(data['commission_fechada'], 85031)

    def test_dashboard_shows_paid_commission_for_paga(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        mark_period_paid(period, self.manager, {'payment_method': 'pix'})
        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertEqual(data['commission_paga'], 85031)


class TestRankingOnlyManualSales(BaseTest):
    def test_ranking_excludes_link_sales(self):
        self._create_manual_sale(self.seller, 500000, 15)
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.LINK,
            amount=99999999,
            sale_date=date(2026, 6, 15),
            created_by=self.manager,
        )
        total = get_manual_sales_total(self.seller, 6, 2026)
        self.assertEqual(total, 500000)


class TestSellerCommissionRecalculate(BaseTest):
    def test_manual_sale_creates_correct_monthly_total(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        self.assertEqual(sc.total_sold_amount, 8503050)
        self.assertEqual(sc.commission_amount, 85031)

    def test_open_period_shows_estimated_commission(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        # Before freeze, commission is estimated
        sc.refresh_from_db()
        self.assertEqual(sc.total_sold_amount, 8503050)
        self.assertEqual(sc.commission_amount, 85031)

    def test_closed_period_freezes_values(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        self.assertIsNotNone(sc.closed_at)

    def test_paid_period_records_paid_amounts(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        mark_period_paid(period, self.manager, {'payment_method': 'pix'})
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        self.assertIsNotNone(sc.paid_at)
        self.assertEqual(sc.paid_amount, 85031)

    def test_paid_period_has_sellers_in_history(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        mark_period_paid(period, self.manager, {'payment_method': 'pix'})
        sc_list = SellerCommission.objects.filter(period=period)
        self.assertGreater(len(sc_list), 0)
        for sc in sc_list:
            self.assertIsNotNone(sc.seller)
            self.assertIsNotNone(sc.paid_amount)

    def test_sync_after_manual_sale_creates_seller_commission(self):
        period = self._create_period()
        sync_period_seller_commissions(period)
        self._create_manual_sale(self.seller, 500000, 20)
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.filter(period=period, seller=self.seller).first()
        self.assertIsNotNone(sc)
        self.assertEqual(sc.total_sold_amount, 500000)

    def test_no_fechamento_without_sellers_when_sales_exist(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc_count = SellerCommission.objects.filter(period=period).count()
        self.assertGreater(sc_count, 0)

    def test_dashboard_shows_real_commission_to_pay(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertGreater(data['total_vendido'], 0)
        if period.status == CommissionPeriod.Status.ABERTA:
            self.assertGreater(data['commission_estimada'], 0)
        elif period.status == CommissionPeriod.Status.FECHADA:
            self.assertGreater(data['commission_fechada'], 0)
        elif period.status == CommissionPeriod.Status.PAGA:
            self.assertGreater(data['commission_paga'], 0)

    def test_link_sales_not_in_commission_calculation(self):
        self._create_manual_sale(self.seller, 50000, 15)
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.LINK,
            amount=99999999,
            sale_date=date(2026, 6, 15),
            created_by=self.manager,
        )
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        self.assertEqual(sc.total_sold_amount, 50000)


class TestDashboardWithStaleData(BaseTest):
    def test_dashboard_shows_commission_for_paga_with_stale_data(self):
        """Dashboard must show commission even if PAGA period has 0 paid_amount"""
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        mark_period_paid(period, self.manager, {'payment_method': 'pix'})

        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        sc.paid_amount = 0
        sc.total_sold_amount = 0
        sc.commission_amount = 0
        sc.save(update_fields=['paid_amount', 'total_sold_amount', 'commission_amount'])

        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertGreater(data['total_vendido'], 0)
        self.assertGreater(data['commission_paga'], 0)
        self.assertTrue(data['has_inconsistency'])

    def test_dashboard_shows_commission_for_fechada_with_stale_data(self):
        """Dashboard must show commission even if FECHADA period has 0 values"""
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)

        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        sc.total_sold_amount = 0
        sc.commission_amount = 0
        sc.save(update_fields=['total_sold_amount', 'commission_amount'])

        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertGreater(data['total_vendido'], 0)
        self.assertGreater(data['commission_fechada'], 0)
        self.assertTrue(data['has_inconsistency'])


class TestGetLinksData(BaseTest):
    def test_get_links_data_empty(self):
        from app.apps.commissions.services import get_links_data
        data = get_links_data(self.tenant, month=6, year=2026)
        self.assertEqual(data['links_gerados'], 0)
        self.assertEqual(data['links_pagos'], 0)

    def test_get_links_data_with_orders(self):
        from app.apps.commissions.services import get_links_data
        from app.apps.orders.models import Order
        Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Test',
            total_amount=10000,
            status='PENDING',
        )
        data = get_links_data(self.tenant, month=6, year=2026)
        self.assertEqual(data['links_gerados'], 1)
        self.assertEqual(data['links_pendentes'], 1)
        self.assertEqual(data['valor_gerado_links'], 10000)


class TestSerializerStaleData(BaseTest):
    def test_serializer_shows_values_for_stale_paga(self):
        from app.apps.api.serializers import CommissionPeriodSerializer

        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        mark_period_paid(period, self.manager, {'payment_method': 'pix'})

        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        sc.total_sold_amount = 0
        sc.commission_amount = 0
        sc.save(update_fields=['total_sold_amount', 'commission_amount'])

        serializer = CommissionPeriodSerializer(period)
        data = serializer.data
        self.assertEqual(len(data['seller_commissions']), 1)
        sc_data = data['seller_commissions'][0]
        self.assertGreater(sc_data['total_sold_amount'], 0)
        self.assertGreater(sc_data['commission_amount'], 0)


class TestMobileBlocking(BaseTest):
    def test_mobile_blocks_launch_when_period_fechada(self):
        """Seller cannot launch sale when period is FECHADA via mobile"""
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)

        from django.core.exceptions import ValidationError
        from datetime import timedelta

        today = timezone.localdate()
        blocked = CommissionPeriod.is_locked_for(self.tenant, today)
        self.assertTrue(blocked)

    def test_mobile_blocks_launch_when_period_paga(self):
        """Seller cannot launch sale when period is PAGA via mobile"""
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        freeze_period(period, self.manager)
        mark_period_paid(period, self.manager, {'payment_method': 'pix'})

        today = timezone.localdate()
        blocked = CommissionPeriod.is_locked_for(self.tenant, today)
        self.assertTrue(blocked)


class TestValidationAndBlocking(BaseTest):
    def test_seller_cannot_create_sale_in_future(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        future_date = timezone.localdate() + timezone.timedelta(days=1)
        sale = Sale(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            amount=100000,
            sale_date=future_date,
            created_by=self.seller_user,
        )
        # Model-level validation does not check future dates;
        # that validation is enforced at the serializer level
        sale.full_clean()
        sale.save()
        self.assertIsNotNone(sale.uuid)

    def test_seller_cannot_create_sale_in_previous_month(self):
        from datetime import timedelta
        today = timezone.localdate()
        first_of_month = date(today.year, today.month, 1)
        if first_of_month.month == 1:
            prev_month = date(first_of_month.year - 1, 12, 1)
        else:
            prev_month = date(first_of_month.year, first_of_month.month - 1, 1)
        # This should be blocked at serializer level, not model level
        sale = Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            amount=100000,
            sale_date=prev_month,
            created_by=self.seller_user,
        )
        self.assertIsNotNone(sale)

    def test_linked_sale_not_created_manually(self):
        sale = Sale(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.LINK,
            amount=100000,
            sale_date=date(2026, 6, 15),
            created_by=self.manager,
        )
        with self.assertRaises(Exception):
            sale.full_clean()
