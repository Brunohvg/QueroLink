from datetime import date

from django.test import TestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User, is_working_day
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale
from app.apps.commissions.models import CommissionPeriod


class IsWorkingDayTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Loja Teste',
            slug='loja-teste',
            is_active=True,
        )

    def _set_config(self, weekdays=None, skip_holidays=True):
        self.tenant.working_weekdays = weekdays or []
        self.tenant.skip_national_holidays = skip_holidays
        self.tenant.save(update_fields=['working_weekdays', 'skip_national_holidays'])

    def test_saturday_with_seg_sex_config_suppressed(self):
        self._set_config(weekdays=[0, 1, 2, 3, 4])
        saturday = date(2026, 7, 4)
        self.assertEqual(saturday.weekday(), 5)
        self.assertFalse(is_working_day(self.tenant, saturday))

    def test_sunday_default_suppressed(self):
        sunday = date(2026, 7, 5)
        self.assertEqual(sunday.weekday(), 6)
        self.assertFalse(is_working_day(self.tenant, sunday))

    def test_national_holiday_skip_on_suppressed(self):
        self._set_config(weekdays=[0, 1, 2, 3, 4, 5], skip_holidays=True)
        holiday = date(2026, 9, 7)
        self.assertEqual(holiday.weekday(), 0)
        self.assertFalse(is_working_day(self.tenant, holiday))

    def test_national_holiday_skip_off_working(self):
        self._set_config(weekdays=[0, 1, 2, 3, 4, 5], skip_holidays=False)
        holiday = date(2026, 9, 7)
        self.assertEqual(holiday.weekday(), 0)
        self.assertTrue(is_working_day(self.tenant, holiday))

    def test_weekday_normal_working(self):
        monday = date(2026, 7, 6)
        self.assertEqual(monday.weekday(), 0)
        self.assertTrue(is_working_day(self.tenant, monday))

    def test_sunday_with_explicit_config_suppressed(self):
        self._set_config(weekdays=[0, 1, 2, 3, 4, 5, 6])
        sunday = date(2026, 7, 5)
        self.assertEqual(sunday.weekday(), 6)
        self.assertTrue(is_working_day(self.tenant, sunday))


class MissingDaysRespectsWorkingDaysTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Loja Missing',
            slug='loja-missing',
            is_active=True,
            working_weekdays=[0, 1, 2, 3, 4],
        )
        self.user = User.objects.create_user(
            username='vendedor@loja.com',
            password='Senha@12345678',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.user,
            name='Vendedor',
            phone='11999999999',
            is_active=True,
        )

    def test_missing_days_excludes_non_working_days(self):
        from app.apps.commissions.services import get_missing_days_before_today

        month, year = 7, 2026
        today = timezone.localdate()
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=month,
            year=year,
        )

        for day in [1, 2, 3]:
            Sale.objects.create(
                tenant=self.tenant,
                seller=self.seller,
                origin=Sale.Origin.MANUAL,
                amount=10000,
                status='ATIVA',
                sale_date=date(year, month, day),
            )

        missing = get_missing_days_before_today(self.seller, month, year)
        for m in missing:
            self.assertIn(m.weekday(), self.tenant.working_weekdays,
                          f'Sabado/domingo nao deve aparecer como pendencia: {m}')

    def test_alert_suppressed_when_not_working_day(self):
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        response = client.get('/dashboard/mobile/')
        self.assertEqual(response.status_code, 200)
