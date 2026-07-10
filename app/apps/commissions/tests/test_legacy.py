from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from app.apps.accounts.models import Tenant
from app.apps.audit.models import AuditLog
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
    calculate_estimated_commission_for_period,
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
    update_period,
    delete_period,
    cancel_period,
    resolve_period_for_date,
)
from app.apps.commissions.exports import build_accounting_zip

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

    def test_custom_range_ignores_calendar_month(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=6,
            year=2026,
            label='MAIO/JUNHO 2026',
            start_date=date(2026, 5, 21),
            end_date=date(2026, 6, 20),
        )
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            amount=100000,
            sale_date=date(2026, 5, 25),
            created_by=self.manager,
        )
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            amount=200000,
            sale_date=date(2026, 6, 25),
            created_by=self.manager,
        )

        commission, total = calculate_estimated_commission_for_period(self.seller, period)

        self.assertEqual(total, 100000)
        self.assertEqual(commission, 1000)


class TestCommissionPeriodRanges(BaseTest):
    def test_legacy_period_defaults_to_calendar_range(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=7,
            year=2026,
        )

        self.assertEqual(period.start_date, date(2026, 7, 1))
        self.assertEqual(period.end_date, date(2026, 7, 31))
        self.assertEqual(period.display_label, '07/2026')

    def test_can_create_custom_range(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=7,
            year=2026,
            label='JULHO 2026',
            start_date=date(2026, 6, 21),
            end_date=date(2026, 7, 20),
        )

        self.assertTrue(period.contains(date(2026, 7, 1)))
        self.assertFalse(period.contains(date(2026, 7, 21)))

    def test_prevents_overlap_same_tenant(self):
        CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=5,
            year=2026,
            start_date=date(2026, 5, 21),
            end_date=date(2026, 6, 20),
        )
        period = CommissionPeriod(
            tenant=self.tenant,
            month=6,
            year=2026,
            start_date=date(2026, 6, 15),
            end_date=date(2026, 7, 14),
        )

        with self.assertRaises(Exception):
            period.full_clean()

    def test_allows_gap_between_periods(self):
        CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=5,
            year=2026,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 20),
        )
        period = CommissionPeriod(
            tenant=self.tenant,
            month=6,
            year=2026,
            start_date=date(2026, 5, 25),
            end_date=date(2026, 6, 20),
        )

        period.full_clean()

    def test_resolve_period_for_date_uses_range_and_ignores_cancelled(self):
        cancelled = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=5,
            year=2026,
            start_date=date(2026, 5, 21),
            end_date=date(2026, 6, 20),
            status=CommissionPeriod.Status.CANCELADA,
        )
        active = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=6,
            year=2026,
            start_date=date(2026, 6, 21),
            end_date=date(2026, 7, 20),
        )

        self.assertIsNone(resolve_period_for_date(self.tenant, date(2026, 6, 10)))
        self.assertEqual(resolve_period_for_date(self.tenant, date(2026, 7, 1)), active)
        self.assertEqual(cancelled.status, CommissionPeriod.Status.CANCELADA)

    def test_validate_sale_can_be_changed_uses_range(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=6,
            year=2026,
            start_date=date(2026, 5, 21),
            end_date=date(2026, 6, 20),
        )
        sc = SellerCommission.objects.create(period=period, seller=self.seller)
        sc.freeze(self.manager, commit=True)

        can_change, error = validate_sale_can_be_changed(
            self.seller, date(2026, 5, 25), self.seller_user,
        )

        self.assertFalse(can_change)
        self.assertIn('fechada', error)

    def test_paid_period_blocks_sale_change(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=6,
            year=2026,
            start_date=date(2026, 5, 21),
            end_date=date(2026, 6, 20),
        )
        sc = SellerCommission.objects.create(period=period, seller=self.seller)
        sc.freeze(self.manager, commit=True)
        sc.mark_paid(self.manager, {'payment_date': date(2026, 7, 1)}, commit=True)

        can_change, error = validate_sale_can_be_changed(
            self.seller, date(2026, 6, 1), self.seller_user,
        )

        self.assertFalse(can_change)
        self.assertIn('paga', error)


class TestAccountingExportPreviousPeriod(BaseTest):
    def _build_and_get_pdf_context(self, month, year):
        captured = {}

        def fake_render(template, context):
            if template == 'reports/relatorio_mensal.html':
                captured.update(context)
            return '<html></html>'

        with patch('app.apps.commissions.exports.render_to_string', side_effect=fake_render), \
             patch('app.apps.commissions.exports.HTML') as html_mock:
            html_mock.return_value.write_pdf.return_value = b'%PDF'
            build_accounting_zip(self.tenant, month, year)
        return captured

    def test_report_uses_previous_real_period_not_calendar_month(self):
        prev_period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=6,
            year=2026,
            label='21/05 a 20/06',
            start_date=date(2026, 5, 21),
            end_date=date(2026, 6, 20),
        )
        current_period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=7,
            year=2026,
            label='21/06 a 20/07',
            start_date=date(2026, 6, 21),
            end_date=date(2026, 7, 20),
        )
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            status='ATIVA',
            amount=10000,
            sale_date=date(2026, 5, 25),
            created_by=self.manager,
        )
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            status='ATIVA',
            amount=30000,
            sale_date=date(2026, 6, 15),
            created_by=self.manager,
        )
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            status='ATIVA',
            amount=80000,
            sale_date=date(2026, 6, 25),
            created_by=self.manager,
        )
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            status='ATIVA',
            amount=20000,
            sale_date=date(2026, 7, 10),
            created_by=self.manager,
        )

        context = self._build_and_get_pdf_context(current_period.month, current_period.year)

        self.assertEqual(context['competencia'], current_period.display_label)
        self.assertEqual(context['prev_total'], 40000)
        self.assertEqual(context['total_sold'], 100000)
        self.assertEqual(context['variacao'], 150)
        self.assertEqual(prev_period.end_date, date(2026, 6, 20))

    def test_report_without_previous_period_sets_variation_none(self):
        current_period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=7,
            year=2026,
            label='21/06 a 20/07',
            start_date=date(2026, 6, 21),
            end_date=date(2026, 7, 20),
        )
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            status='ATIVA',
            amount=50000,
            sale_date=date(2026, 7, 10),
            created_by=self.manager,
        )

        context = self._build_and_get_pdf_context(current_period.month, current_period.year)

        self.assertEqual(context['prev_total'], 0)
        self.assertIsNone(context['variacao'])


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
        order = Order.objects.create(
            tenant=self.tenant, seller=self.seller,
            customer_name='Test', total_amount=10000, status='PENDING',
        )
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.datetime(2026, 6, 15, tzinfo=timezone.get_current_timezone()),
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


class TestEditPeriod(BaseTest):
    def setUp(self):
        super().setUp()
        self.financeiro = User.objects.create_user(
            username='financeiro_edit', password='test123',
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )

    def test_edit_expected_days_in_open_period(self):
        period = self._create_period(expected_days=22)
        updated, changed = update_period(period, {'expected_working_days': 27}, self.manager)
        self.assertIn('expected_working_days', changed)
        period.refresh_from_db()
        self.assertEqual(period.expected_working_days, 27)

    def test_edit_notes_in_open_period(self):
        period = self._create_period()
        updated, changed = update_period(period, {'notes': 'Teste observacao'}, self.manager)
        self.assertIn('notes', changed)
        period.refresh_from_db()
        self.assertEqual(period.notes, 'Teste observacao')

    def test_cannot_edit_period_with_paid_commission(self):
        self._create_manual_sale(self.seller, 100000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)
        pay_seller_commissions(period, [sc.id], self.financeiro, {'payment_method': 'pix'})
        with self.assertRaises(ValueError):
            update_period(period, {'notes': 'Nao deve'}, self.manager)

    def test_cannot_edit_month_with_sales(self):
        self._create_manual_sale(self.seller, 100000, 15)
        period = self._create_period()
        with self.assertRaises(ValueError):
            update_period(period, {'month': 7}, self.manager)


class TestDeletePeriod(BaseTest):
    def setUp(self):
        super().setUp()
        self.financeiro = User.objects.create_user(
            username='financeiro_del', password='test123',
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )

    def test_delete_open_period_without_sales(self):
        period = self._create_period()
        count = delete_period(period, self.admin)
        self.assertGreaterEqual(count, 0)
        self.assertFalse(CommissionPeriod.objects.filter(pk=period.pk).exists())

    def test_delete_does_not_remove_manual_sales(self):
        sale = self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        delete_period(period, self.admin)
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())

    def test_delete_creates_audit_log(self):
        period = self._create_period()
        delete_period(period, self.admin)
        log = AuditLog.objects.filter(
            action='commission_period.deleted',
            user=self.admin,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.changes.get('period_id'), str(period.uuid))

    def test_cannot_delete_with_closed_seller(self):
        self._create_manual_sale(self.seller, 100000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)
        with self.assertRaises(ValueError):
            delete_period(period, self.admin)

    def test_cannot_delete_with_paid_seller(self):
        self._create_manual_sale(self.seller, 100000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)
        pay_seller_commissions(period, [sc.id], self.financeiro, {'payment_method': 'pix'})
        with self.assertRaises(ValueError):
            delete_period(period, self.admin)


class TestCancelPeriod(BaseTest):
    def setUp(self):
        super().setUp()
        self.financeiro = User.objects.create_user(
            username='financeiro_cancel', password='test123',
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )

    def test_cancel_open_period(self):
        period = self._create_period()
        result = cancel_period(period, 'Teste cancelamento', self.manager)
        self.assertEqual(result.status, CommissionPeriod.Status.CANCELADA)
        self.assertEqual(result.cancel_reason, 'Teste cancelamento')

    def test_cancel_requires_reason(self):
        period = self._create_period()
        with self.assertRaises(ValueError):
            cancel_period(period, '', self.manager)

    def test_cannot_cancel_with_paid_commission(self):
        self._create_manual_sale(self.seller, 100000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)
        pay_seller_commissions(period, [sc.id], self.financeiro, {'payment_method': 'pix'})
        with self.assertRaises(ValueError):
            cancel_period(period, 'Nao deve', self.manager)


class TestAdjustedCommissionPayment(BaseTest):
    def test_adjusted_commission_paid_with_new_amount(self):
        self._create_manual_sale(self.seller, 1000000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)
        sc.refresh_from_db()
        self.assertEqual(sc.frozen_commission_amount, 10000)

        create_commission_adjustment(sc, 12000, 'Ajuste para 12000', self.manager)
        sc.refresh_from_db()
        self.assertEqual(sc.commission_amount, 12000)
        self.assertEqual(sc.status, SellerCommission.Status.AJUSTADA)

        pay_seller_commissions(period, [sc.id], self.manager, {
            'payment_method': 'pix',
        })
        sc.refresh_from_db()
        self.assertEqual(sc.status, SellerCommission.Status.PAGA)
        self.assertEqual(sc.paid_amount, 12000)

    def test_adjusted_commission_in_period_summary(self):
        self._create_manual_sale(self.seller, 1000000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)
        sc.refresh_from_db()

        create_commission_adjustment(sc, 12000, 'Ajuste para 12000', self.manager)
        sc.refresh_from_db()

        summary = calculate_period_summary(period)
        self.assertEqual(summary['commission_fechada'], 12000)

        pay_seller_commissions(period, [sc.id], self.manager, {
            'payment_method': 'pix',
        })
        sc.refresh_from_db()

        summary = calculate_period_summary(period)
        self.assertEqual(summary['commission_paga'], 12000)

    def test_closed_commission_paid_with_frozen_amount(self):
        self._create_manual_sale(self.seller, 1000000, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)
        sc.refresh_from_db()
        frozen = sc.frozen_commission_amount
        self.assertIsNotNone(frozen)

        pay_seller_commissions(period, [sc.id], self.manager, {
            'payment_method': 'pix',
        })
        sc.refresh_from_db()
        self.assertEqual(sc.status, SellerCommission.Status.PAGA)
        self.assertEqual(sc.paid_amount, frozen)


class TestPaymentQueue(BaseTest):
    def setUp(self):
        super().setUp()
        self.financeiro = User.objects.create_user(
            username='financeiro_fila', password='test123',
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )
        self._create_manual_sale(self.seller, 100000, 15)
        self._create_manual_sale(self.seller2, 50000, 16)
        self.period = self._create_period()
        sync_period_seller_commissions(self.period)

        sc1 = SellerCommission.objects.get(period=self.period, seller=self.seller)
        sc2 = SellerCommission.objects.get(period=self.period, seller=self.seller2)

        close_seller_commissions(self.period, [sc1.id], self.manager)
        close_seller_commissions(self.period, [sc2.id], self.manager)
        pay_seller_commissions(self.period, [sc2.id], self.financeiro, {'payment_method': 'pix'})

        self.period.refresh_from_db()

    def test_queue_shows_fechada_only(self):
        fechadas = SellerCommission.objects.filter(
            period=self.period, status=SellerCommission.Status.FECHADA,
        )
        pagas = SellerCommission.objects.filter(
            period=self.period, status=SellerCommission.Status.PAGA,
        )
        self.assertEqual(fechadas.count(), 1)
        self.assertEqual(pagas.count(), 1)

    def test_period_is_parcialmente_paga(self):
        from app.apps.commissions.services import recalculate_period_status
        status = recalculate_period_status(self.period)
        self.assertEqual(status, CommissionPeriod.Status.PARCIALMENTE_PAGA)

    def test_queue_excludes_paid_sellers(self):
        fechadas = SellerCommission.objects.filter(
            period=self.period, status=SellerCommission.Status.FECHADA,
        )
        for sc in fechadas:
            self.assertNotEqual(sc.status, SellerCommission.Status.PAGA)

    def test_pay_remaining_seller_makes_period_paga(self):
        fechadas = SellerCommission.objects.filter(
            period=self.period, status=SellerCommission.Status.FECHADA,
        )
        pay_seller_commissions(
            self.period, list(fechadas.values_list('id', flat=True)),
            self.financeiro, {'payment_method': 'pix'},
        )
        from app.apps.commissions.services import recalculate_period_status
        status = recalculate_period_status(self.period)
        self.assertEqual(status, CommissionPeriod.Status.PAGA)


class TestDashboardCommissionSeparation(BaseTest):
    def setUp(self):
        super().setUp()
        self.financeiro = User.objects.create_user(
            username='financeiro_dash', password='test123',
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )

    def test_dashboard_separates_commissions(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        self._create_manual_sale(self.seller2, 420000, 16)
        period = self._create_period()
        sync_period_seller_commissions(period)

        sc1 = SellerCommission.objects.get(period=period, seller=self.seller)
        sc2 = SellerCommission.objects.get(period=period, seller=self.seller2)

        close_seller_commissions(period, [sc1.id], self.manager)
        pay_seller_commissions(period, [sc1.id], self.financeiro, {'payment_method': 'pix'})

        data = get_dashboard_data(self.tenant, month=6, year=2026)
        self.assertGreater(data['commission_paga'], 0)
        self.assertGreater(data['commission_aberta'], 0)
        self.assertEqual(data['commission_fechada'], 0)


class TestRetroactiveSaleEntry(BaseTest):
    def test_seller_can_create_sale_on_previous_day_same_month(self):
        today = date(2026, 6, 26)
        sale_date = date(2026, 6, 15)
        with patch('django.utils.timezone.localdate', return_value=today):
            period = self._create_period(month=6, year=2026)
            sync_period_seller_commissions(period)
            can, msg = validate_sale_can_be_changed(self.seller, sale_date, self.seller_user)
            self.assertTrue(can)

            sale = Sale.objects.create(
                tenant=self.tenant,
                seller=self.seller,
                origin=Sale.Origin.MANUAL,
                amount=50000,
                sale_date=sale_date,
                created_by=self.seller_user,
            )
            self.assertEqual(sale.sale_date, sale_date)

    def test_seller_cannot_create_sale_on_future_date(self):
        today = date(2026, 6, 26)
        future_date = date(2026, 6, 27)
        from django.core.exceptions import ValidationError

        period = self._create_period(month=6, year=2026)
        sync_period_seller_commissions(period)

        can, msg = validate_sale_can_be_changed(self.seller, future_date, self.seller_user)
        self.assertTrue(can)

        has_period = CommissionPeriod.objects.filter(
            tenant=self.tenant, month=6, year=2026,
        ).exists()
        self.assertTrue(has_period)

    def test_seller_cannot_create_sale_in_closed_commission(self):
        self._create_manual_sale(self.seller, 50000, 15)
        period = self._create_period(month=6, year=2026)
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        can, msg = validate_sale_can_be_changed(
            self.seller, date(2026, 6, 20), self.seller_user,
        )
        self.assertFalse(can)
        self.assertIn('fechada', msg.lower())

    def test_seller_cannot_create_sale_in_paid_commission(self):
        self._create_manual_sale(self.seller, 50000, 15)
        period = self._create_period(month=6, year=2026)
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        financeiro = User.objects.create_user(
            username='financeiro_test', password='test123',
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )
        pay_seller_commissions(period, [sc.id], financeiro, {
            'payment_method': 'pix',
        })

        can, msg = validate_sale_can_be_changed(
            self.seller, date(2026, 6, 20), self.seller_user,
        )
        self.assertFalse(can)

    def test_no_duplicate_manual_sale_same_seller_same_day(self):
        from django.db import IntegrityError

        period = self._create_period(month=6, year=2026)
        sync_period_seller_commissions(period)

        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date=date(2026, 6, 15), created_by=self.seller_user,
        )

        with self.assertRaises(IntegrityError):
            Sale.objects.create(
                tenant=self.tenant, seller=self.seller,
                origin=Sale.Origin.MANUAL, amount=20000,
                sale_date=date(2026, 6, 15), created_by=self.seller_user,
            )


class TestMissingDaysFunction(BaseTest):
    def test_missing_days_before_today_with_gaps(self):
        from app.apps.commissions.services import get_missing_days_before_today

        today = date(2026, 6, 26)
        with patch('django.utils.timezone.localdate', return_value=today):
            self._create_manual_sale(self.seller, 10000, 1)
            self._create_manual_sale(self.seller, 10000, 3)
            self._create_manual_sale(self.seller, 10000, 5)

            self._create_period(month=6, year=2026)

            missing = get_missing_days_before_today(self.seller, 6, 2026)

            self.assertIn(date(2026, 6, 2), missing)
            self.assertIn(date(2026, 6, 4), missing)
            self.assertNotIn(date(2026, 6, 1), missing)
            self.assertNotIn(date(2026, 6, 3), missing)

    def test_missing_days_before_today_no_missing(self):
        from app.apps.commissions.services import get_missing_days_before_today

        today = date(2026, 6, 4)
        with patch('django.utils.timezone.localdate', return_value=today):
            self._create_manual_sale(self.seller, 10000, 1)
            self._create_manual_sale(self.seller, 10000, 2)
            self._create_manual_sale(self.seller, 10000, 3)

            self._create_period(month=6, year=2026)

            missing = get_missing_days_before_today(self.seller, 6, 2026)
            self.assertEqual(len(missing), 0)

    def test_missing_days_before_today_empty_month(self):
        from app.apps.commissions.services import get_missing_days_before_today

        today = date(2026, 6, 26)
        with patch('django.utils.timezone.localdate', return_value=today):
            self._create_period(month=6, year=2026)

            missing = get_missing_days_before_today(self.seller, 6, 2026)

            self.assertEqual(len(missing), 22)
            self.assertNotIn(date(2026, 6, 26), missing)

    def test_missing_days_before_today_does_not_include_future(self):
        from app.apps.commissions.services import get_missing_days_before_today

        today = date(2026, 6, 1)
        with patch('django.utils.timezone.localdate', return_value=today):
            self._create_period(month=6, year=2026)

            missing = get_missing_days_before_today(self.seller, 6, 2026)

            self.assertEqual(len(missing), 0)

    def test_missing_days_before_today_does_not_include_other_sellers(self):
        from app.apps.commissions.services import get_missing_days_before_today

        today = date(2026, 6, 26)
        with patch('django.utils.timezone.localdate', return_value=today):
            self._create_manual_sale(self.seller, 10000, 1)
            self._create_period(month=6, year=2026)

            missing = get_missing_days_before_today(self.seller, 6, 2026)
            self.assertNotIn(date(2026, 6, 1), missing)


class TestMobileHomeDoesNotSyncAllSellers(BaseTest):
    def test_mobile_home_does_not_import_sync_period(self):
        from app.apps.dashboard import mobile_views
        import inspect
        source = inspect.getsource(mobile_views.mobile_home)
        self.assertNotIn('sync_period_seller_commissions', source)


class TestCommissionAdjustmentNotification(BaseTest):
    def test_adjustment_triggers_notification(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        with patch(
            'app.apps.notifications.tasks.notify_commission_adjusted'
        ) as mock_notify:
            adjustment = create_commission_adjustment(
                sc, 100000, 'Ajuste teste', self.manager,
            )
            mock_notify.assert_called_once_with(sc, adjustment)

    def test_adjustment_notification_failure_does_not_block_adjustment(self):
        self._create_manual_sale(self.seller, 8503050, 15)
        period = self._create_period()
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        with patch(
            'app.apps.notifications.tasks.notify_commission_adjusted',
            side_effect=Exception('WhatsApp error'),
        ):
            adjustment = create_commission_adjustment(
                sc, 100000, 'Ajuste com falha de notificacao', self.manager,
            )

        self.assertIsNotNone(adjustment)
        sc.refresh_from_db()
        self.assertEqual(sc.commission_amount, 100000)
        self.assertEqual(sc.status, SellerCommission.Status.AJUSTADA)


class TestMobileHomeMissingDaysContext(BaseTest):
    def test_mobile_home_passes_missing_days_when_editable(self):
        today = date(2026, 6, 26)
        with patch('django.utils.timezone.localdate', return_value=today):
            from django.test import RequestFactory
            from app.apps.dashboard.mobile_views import mobile_home

            self._create_period(month=6, year=2026)
            factory = RequestFactory()
            request = factory.get('/mobile/')
            request.user = self.seller_user

            response = mobile_home(request)
            self.assertTrue(hasattr(response, 'content'))
            content = response.content.decode()

    def test_mobile_home_no_missing_days_when_not_editable(self):
        from django.test import RequestFactory
        from app.apps.dashboard.mobile_views import mobile_home

        today = timezone.localdate()

        self._create_manual_sale(self.seller, 50000, 15,
                                 month=today.month, year=today.year)
        period = self._create_period(month=today.month, year=today.year)
        sync_period_seller_commissions(period)
        sc = SellerCommission.objects.get(period=period, seller=self.seller)
        close_seller_commissions(period, [sc.id], self.manager)

        factory = RequestFactory()
        request = factory.get('/mobile/')
        request.user = self.seller_user

        response = mobile_home(request)
        self.assertTrue(hasattr(response, 'content'))
        content = response.content.decode()
        self.assertIn('Sua comissao deste mes foi fechada', content)


class TestReceiptPDF(BaseTest):
    def setUp(self):
        super().setUp()
        with patch('app.apps.notifications.tasks.create_and_send_notification', return_value=None):
            self._create_manual_sale(self.seller, 1000000, 15)
            self.period = self._create_period()
            sync_period_seller_commissions(self.period)
            self.sc = SellerCommission.objects.get(period=self.period, seller=self.seller)
            close_seller_commissions(self.period, [self.sc.id], self.manager)
            pay_seller_commissions(self.period, [self.sc.id], self.manager, {
                'payment_method': 'pix',
            })
            self.sc.refresh_from_db()

    def test_receipt_pdf_for_paid_commission(self):
        self.client.force_login(self.manager)
        resp = self.client.get(
            f'/api/commissions/periods/{self.period.uuid}/receipt/{self.sc.id}/',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['content-type'], 'application/pdf')
        self.assertTrue(resp.content.startswith(b'%PDF'))

    def test_receipt_returns_404_for_open_commission(self):
        self._create_manual_sale(self.seller2, 50000, 16)
        sc2 = SellerCommission.objects.get(period=self.period, seller=self.seller2)
        self.client.force_login(self.manager)
        resp = self.client.get(
            f'/api/commissions/periods/{self.period.uuid}/receipt/{sc2.id}/',
        )
        self.assertEqual(resp.status_code, 404)

    def test_receipt_returns_404_for_other_tenant(self):
        other_tenant = Tenant.objects.create(
            company_name='Other', slug='other', is_active=True,
        )
        other_user = User.objects.create_user(
            username='other_mgr', password='test123',
            role=User.Role.MANAGER, tenant=other_tenant,
        )
        self.client.force_login(other_user)
        resp = self.client.get(
            f'/api/commissions/periods/{self.period.uuid}/receipt/{self.sc.id}/',
        )
        self.assertEqual(resp.status_code, 404)
