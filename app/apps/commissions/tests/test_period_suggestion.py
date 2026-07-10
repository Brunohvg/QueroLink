from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale
from app.apps.commissions.models import CommissionPeriod
from app.apps.commissions.services import (
    suggest_period_range,
    get_or_create_period,
    count_sales_outside_periods,
)

User = get_user_model()


class SuggestPeriodRangeTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Sugestao Empresa',
            slug='sugestao-empresa',
            is_active=True,
        )

    def test_day_21_june_2026(self):
        self.tenant.period_start_day = 21
        self.tenant.save()
        start, end = suggest_period_range(self.tenant, 6, 2026)
        self.assertEqual(start, date(2026, 5, 21))
        self.assertEqual(end, date(2026, 6, 20))

    def test_day_1_calendar_month(self):
        self.tenant.period_start_day = 1
        self.tenant.save()
        start, end = suggest_period_range(self.tenant, 6, 2026)
        self.assertEqual(start, date(2026, 6, 1))
        self.assertEqual(end, date(2026, 6, 30))

    def test_day_21_january_crosses_year(self):
        self.tenant.period_start_day = 21
        self.tenant.save()
        start, end = suggest_period_range(self.tenant, 1, 2026)
        self.assertEqual(start, date(2025, 12, 21))
        self.assertEqual(end, date(2026, 1, 20))


class GetOrCreatePeriodSuggestionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Auto Empresa',
            slug='auto-empresa',
            period_start_day=21,
            is_active=True,
        )

    def test_auto_create_uses_suggested_range(self):
        period, created = get_or_create_period(self.tenant, 6, 2026)
        self.assertTrue(created)
        self.assertEqual(period.start_date, date(2026, 5, 21))
        self.assertEqual(period.end_date, date(2026, 6, 20))

    def test_explicit_dates_preserved(self):
        period, created = get_or_create_period(
            self.tenant, 6, 2026,
            start_date=date(2026, 6, 3), end_date=date(2026, 6, 28),
        )
        self.assertTrue(created)
        self.assertEqual(period.start_date, date(2026, 6, 3))
        self.assertEqual(period.end_date, date(2026, 6, 28))


class GapCountTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Gap Empresa',
            slug='gap-empresa',
            is_active=True,
        )
        self.seller_user = User.objects.create_user(
            username='gapseller', password='x',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user,
            name='GapVendedor', phone='55999999900',
            commission_rate=Decimal('0.01'), is_active=True,
        )

    def _sale(self, day=5, month=6, year=2026):
        return Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date=date(year, month, day), created_by=self.seller_user,
        )

    def test_sale_without_period_counted(self):
        self._sale(day=5, month=6, year=2026)
        self.assertEqual(count_sales_outside_periods(self.tenant), 1)

    def test_sale_covered_by_period_not_counted(self):
        self._sale(day=5, month=6, year=2026)
        CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
        )
        self.assertEqual(count_sales_outside_periods(self.tenant), 0)


class PeriodStartDayConfigTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Config Empresa',
            slug='config-empresa',
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username='cfgadmin', password='pass',
            role=User.Role.ADMIN, tenant=self.tenant,
        )

    def _post(self, value):
        self.client.login(username='cfgadmin', password='pass')
        return self.client.post(
            reverse('dashboard:gestor_configuracoes'),
            data={'period_start_day': value},
        )

    def test_valid_day_saved(self):
        self._post('21')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.period_start_day, 21)

    def test_rejects_day_zero(self):
        self._post('0')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.period_start_day, 1)

    def test_rejects_day_29(self):
        self._post('29')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.period_start_day, 1)
