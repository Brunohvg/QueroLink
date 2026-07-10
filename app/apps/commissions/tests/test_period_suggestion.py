from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from app.apps.accounts.models import Tenant
from app.apps.commissions.services import suggest_period_range

User = get_user_model()


class SuggestPeriodRangeServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Sugestao Empresa', slug='sugestao-empresa',
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


class SuggestEndpointTests(TestCase):
    TEST_ONLY_PASSWORD = 'test-only-password-2211'

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Endpoint Empresa', slug='endpoint-empresa',
            period_start_day=21, is_active=True,
        )
        self.manager = User.objects.create_user(
            username='gestor_sug', password=self.TEST_ONLY_PASSWORD,
            role=User.Role.MANAGER, tenant=self.tenant,
        )

    def _auth(self):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': self.manager.username, 'password': self.TEST_ONLY_PASSWORD,
        }, format='json')
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}')
        return client

    def test_suggest_returns_cycle_dates(self):
        client = self._auth()
        resp = client.get('/api/commissions/periods/suggest/?month=8&year=2026')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['start_date'], '2026-07-21')
        self.assertEqual(resp.data['end_date'], '2026-08-20')
        self.assertEqual(resp.data['period_start_day'], 21)

    def test_suggest_rejects_invalid_month(self):
        client = self._auth()
        resp = client.get('/api/commissions/periods/suggest/?month=13&year=2026')
        self.assertEqual(resp.status_code, 400)


class PeriodStartDayConfigTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Config Empresa', slug='config-empresa',
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
        self.assertEqual(self.tenant.period_start_day, 21)

    def test_rejects_day_29(self):
        self._post('29')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.period_start_day, 21)
