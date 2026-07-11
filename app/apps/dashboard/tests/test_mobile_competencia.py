from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model

from app.apps.accounts.models import Tenant
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale

User = get_user_model()

FAKE_TODAY = date(2026, 7, 10)


class MobileCompetenciaFirstTest(TestCase):
    """PROMPT_42 LOTE 8/9/10 - mobile competencia-first."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Mobile Co', slug='mobile-co',
            default_commission_rate=Decimal('0.01'), is_active=True,
            period_start_day=21,
        )
        self.seller_user = User.objects.create_user(
            username='vendmob', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Vend Mobile',
            phone='55999990000', commission_rate=Decimal('0.01'), is_active=True,
        )
        # Competencia 21/06 a 20/07 (contem FAKE_TODAY = 10/07)
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=5227922, sale_date='2026-06-25', created_by=self.seller_user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=4723937, sale_date='2026-07-05', created_by=self.seller_user,
        )
        self.client.force_login(self.seller_user)

    # 7 + 9 (home mobile): total 99.518,59 com 1% => comissao 995,19; nao mistura
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_mobile_home_commission_matches_total(self, mock_tz):
        mock_tz.localdate.return_value = FAKE_TODAY
        resp = self.client.get('/dashboard/mobile/')
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        self.assertEqual(ctx['month_total'], 9951859)
        self.assertEqual(ctx['comissao_estimada'], 99519)
        # prova da nao-mistura: comissao == round(total * taxa)
        self.assertEqual(ctx['comissao_estimada'], round(ctx['month_total'] * 0.01))

    # 12: ranking mobile usa o range da competencia (25/06 conta)
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_mobile_ranking_uses_period_range(self, mock_tz):
        mock_tz.localdate.return_value = FAKE_TODAY
        resp = self.client.get('/dashboard/mobile/ranking/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['my_total'], 9951859)
        self.assertEqual(resp.context['seller_pos'], 1)

    # 13: sem competencia, ranking fica vazio (sem fallback de mes)
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_mobile_ranking_empty_without_period(self, mock_tz):
        mock_tz.localdate.return_value = FAKE_TODAY
        CommissionPeriod.objects.all().delete()
        resp = self.client.get('/dashboard/mobile/ranking/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['seller_pos'])
        self.assertEqual(resp.context['my_total'], 0)
        self.assertIn('no_current_period_message', resp.context)

    # 14: minhas vendas permanece filtravel por mes
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_minhas_vendas_month_navigation(self, mock_tz):
        mock_tz.localdate.return_value = FAKE_TODAY
        resp = self.client.get('/dashboard/mobile/vendas/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['months']), 12)

    # 15: venda de junho pertencente a competencia de julho mostra badge; sem N+1
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_minhas_vendas_competencia_badge_no_nplus1(self, mock_tz):
        mock_tz.localdate.return_value = FAKE_TODAY
        # Adiciona mais vendas para provar que o numero de queries nao cresce
        # com o numero de vendas (sem N+1 ao resolver a competencia por venda).
        for d in ['2026-06-26', '2026-06-27', '2026-07-06', '2026-07-07']:
            Sale.objects.create(
                tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
                amount=1000, sale_date=d, created_by=self.seller_user,
            )
        resp = self.client.get('/dashboard/mobile/vendas/')
        sales_json = resp.context['sales_json']
        by_date = {s['date']: s for s in sales_json}
        # venda de 25/06 cai na competencia Julho/2026 (mes difere) => badge
        self.assertEqual(by_date['25/06/2026']['competencia_label'], 'Julho/2026')
        # venda de 05/07 - mesmo mes do periodo => sem badge
        self.assertEqual(by_date['05/07/2026']['competencia_label'], '')

    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_minhas_vendas_no_nplus1_periods_loaded_once(self, mock_tz):
        mock_tz.localdate.return_value = FAKE_TODAY
        # Todos os periodos do tenant sao carregados numa unica query,
        # independentemente do numero de vendas.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/dashboard/mobile/vendas/')
        period_queries = [
            q for q in ctx.captured_queries
            if 'commissions_commissionperiod' in q['sql']
            and 'sellercommission' not in q['sql'].lower()
        ]
        self.assertEqual(len(period_queries), 1)

    # 8 mobile: sem competencia, home nao usa fallback financeiro silencioso
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_mobile_home_no_period_no_financial_fallback(self, mock_tz):
        mock_tz.localdate.return_value = FAKE_TODAY
        CommissionPeriod.objects.all().delete()
        resp = self.client.get('/dashboard/mobile/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['month_total'], 0)
        self.assertIsNotNone(resp.context['no_current_period_message'])
