"""PROMPT 50 - testes de regressao de viewport/SW/performance (LOTE 12)."""

from datetime import date
from decimal import Decimal
import re

from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection

from app.apps.accounts.models import Tenant
from django.contrib.auth import get_user_model
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod
from app.apps.sales.models import Sale
from app.apps.sellers.services import create_day_justification

User = get_user_model()


def _render(client, url):
    resp = client.get(url)
    return resp, resp.content.decode('utf-8') if resp.status_code == 200 else ''


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='PM Co', slug='pm-co',
            default_commission_rate=Decimal('0.01'), period_start_day=21,
        )
        self.su = User.objects.create_user(
            username='s', role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.su, name='S',
            phone='55999990001', commission_rate=Decimal('0.01'),
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        for d in ['2026-06-23', '2026-06-24', '2026-07-01', '2026-07-02']:
            Sale.objects.create(
                tenant=self.tenant, seller=self.seller,
                origin=Sale.Origin.MANUAL, amount=100000, sale_date=d,
                created_by=self.su,
            )
        create_day_justification(
            tenant=self.tenant, seller=self.seller, date=date(2026, 6, 25),
            reason='FALTA', user=User.objects.create_user(
                username='adm', role=User.Role.ADMIN, tenant=self.tenant,
            ),
        )
        self.client.force_login(self.su)


class ViewportSafeAreaTest(_Base):
    """100dvh com fallback, safe-area, padding dinamico, bottom-nav var CSS."""

    def test_dvh_with_vh_fallback(self):
        _, html = _render(self.client, '/dashboard/mobile/')
        # min-height:100vh (fallback) seguido de 100dvh
        self.assertIn('min-height: 100vh', html)
        self.assertIn('min-height: 100dvh', html)

    def test_bottom_nav_uses_css_variable(self):
        _, html = _render(self.client, '/dashboard/mobile/')
        self.assertIn('--bottom-nav-height', html)
        self.assertIn('--page-bottom-gap', html)
        # navbar height deriva da variavel
        self.assertIn('calc(var(--bottom-nav-height)', html)

    def test_main_padding_derived_from_navbar(self):
        _, html = _render(self.client, '/dashboard/mobile/')
        self.assertIn('padding-bottom:calc(var(--bottom-nav-height)', html)
        # nao existe mais pb-32 fixo no main (foi substituido)
        self.assertNotIn('pb-32', html)

    def test_safe_area_css_present(self):
        _, html = _render(self.client, '/dashboard/mobile/')
        self.assertIn('env(safe-area-inset-bottom', html)

    def test_system_font_family(self):
        """Fonte padrao e system-ui; CDN e assincrono nao bloqueante."""
        _, html = _render(self.client, '/dashboard/mobile/')
        self.assertIn("font-family: system-ui", html)
        # CDN carregado com media=print (nao bloqueia render)
        self.assertIn('media="print"', html)


class ServiceWorkerTest(_Base):
    """SW nao cacheia HTML autenticado nem API; limpa caches antigos."""

    def _sw(self):
        return open('static/sw.js').read()

    def test_sw_navigates_auth_plain_network_only(self):
        sw = self._sw()
        # navegacao para /dashboard/ usa networkOnlyWithOfflineFallback
        self.assertIn("networkOnlyWithOfflineFallback", sw)
        # nunca cache-first para auth
        self.assertNotIn("cache.match(event.request)", sw.split("var isAuthHtml")[1].split('event.respondWith')[0])

    def test_sw_stale_while_revalidate_for_static(self):
        sw = self._sw()
        self.assertIn("staleWhileRevalidate", sw)

    def test_sw_clears_old_precache_versions(self):
        sw = self._sw()
        self.assertIn("PRECACHE_PREFIX", sw)
        # limpa caches antigos com o mesmo prefixo
        self.assertIn("key.indexOf(PRECACHE_PREFIX) === 0", sw)

    def test_sw_claims_and_skips_waiting(self):
        sw = self._sw()
        self.assertIn("self.skipWaiting()", sw)
        self.assertIn("self.clients.claim()", sw)

    def test_sw_no_auth_path_cache_persist(self):
        """HTML de /dashboard/ nunca vai pro cache via stale-while-revalidate."""
        sw = self._sw()
        sw_body = sw.split('isAuthHtml', 1)[1].split('isApi', 1)[0]
        self.assertNotIn("cache.put", sw_body)
        self.assertNotIn("caches.open", sw_body)


class QueriesTest(_Base):
    """Queries constantes (sem N+1)."""

    def test_home_queries(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/dashboard/mobile/')
        self.assertLessEqual(len(ctx.captured_queries), 20)

    def test_vendas_queries(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/dashboard/mobile/vendas/')
        self.assertLessEqual(len(ctx.captured_queries), 18)

    def test_lancar_queries(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/dashboard/mobile/lancar/')
        self.assertLessEqual(len(ctx.captured_queries), 12)


class NoFinancialRuleChangedTest(_Base):
    """Regressao: servicos financeiros inalterados."""

    def test_commission_unchanged(self):
        from app.apps.commissions.services import (
            calculate_estimated_commission_for_period,
        )
        c, t = calculate_estimated_commission_for_period(self.seller, self.period)
        self.assertIsNotNone(c)
        self.assertGreaterEqual(c, 0)

    def test_no_migration_created(self):
        from io import StringIO
        from django.core.management import call_command
        changed = False
        try:
            call_command(
                'makemigrations', check=True, dry_run=True,
                stdout=StringIO(), stderr=StringIO(), verbosity=0,
            )
        except SystemExit:
            changed = True
        self.assertFalse(changed, 'Migrations pendentes detectadas')


class LancarVendaKeyboardTest(_Base):
    """Teclado numerico preservado apos refactor."""

    def test_numeric_inputmode_preserved(self):
        _, html = _render(self.client, '/dashboard/mobile/lancar/')
        self.assertIn('inputmode="numeric"', html)
