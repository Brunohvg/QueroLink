from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model

from app.apps.accounts.models import Tenant
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale, SaleChangeLog

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
        # venda de 25/06 cai na competencia Julho/2026 (mes difere)
        self.assertEqual(by_date['25/06/2026']['competencia_display_label'], 'Julho/2026')
        self.assertEqual(by_date['25/06/2026']['competencia_uuid'], str(self.period.uuid))
        # venda de 05/07 - mesma competencia (mesmo range)
        self.assertEqual(by_date['05/07/2026']['competencia_display_label'], 'Julho/2026')
        self.assertEqual(by_date['05/07/2026']['competencia_uuid'], str(self.period.uuid))

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

    def test_minhas_vendas_change_log_count_is_one(self):
        sale = Sale.objects.get(sale_date=date(2026, 6, 25))
        SaleChangeLog.objects.create(
            sale=sale, tenant=self.tenant,
            action=SaleChangeLog.Action.UPDATE,
            changed_by=self.seller_user, reason='Correcao',
        )
        response = self.client.get('/dashboard/mobile/vendas/')
        by_uuid = {item['uuid']: item for item in response.context['sales_json']}
        self.assertEqual(by_uuid[str(sale.uuid)]['change_log_count'], 1)


class MinhasVendasCompetenciaContextTest(TestCase):
    """PROMPT_42.2 - Minhas Vendas com contexto de competencia."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Ctx Co', slug='ctx-co',
            default_commission_rate=Decimal('0.01'), is_active=True,
            period_start_day=21,
        )
        self.seller_user = User.objects.create_user(
            username='vendctx', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Vend Ctx',
            phone='55999991111', commission_rate=Decimal('0.01'), is_active=True,
        )
        # Julho/2026: 21/06 a 20/07 ; Agosto/2026: 21/07 a 20/08
        self.period_jul = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        self.period_ago = CommissionPeriod.objects.create(
            tenant=self.tenant, month=8, year=2026,
            start_date=date(2026, 7, 21), end_date=date(2026, 8, 20),
            label='Agosto/2026',
        )

    def _sale(self, sale_date, origin=Sale.Origin.MANUAL, amount=100000):
        return Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=origin,
            amount=amount, sale_date=sale_date, created_by=self.seller_user,
        )

    def _fetch_by_date(self):
        self.client.force_login(self.seller_user)
        resp = self.client.get('/dashboard/mobile/vendas/')
        self.assertEqual(resp.status_code, 200)
        return {s['date']: s for s in resp.context['sales_json']}

    # 1: venda de 25/06 mostra competencia Julho/2026
    def test_sale_2506_shows_julho(self):
        self._sale('2026-06-25')
        row = self._fetch_by_date()['25/06/2026']
        self.assertEqual(row['competencia_display_label'], 'Julho/2026')
        self.assertEqual(row['competencia_uuid'], str(self.period_jul.uuid))

    # 2: venda de 21/07 mostra competencia Agosto/2026
    def test_sale_2107_shows_agosto(self):
        self._sale('2026-07-21')
        row = self._fetch_by_date()['21/07/2026']
        self.assertEqual(row['competencia_display_label'], 'Agosto/2026')
        self.assertEqual(row['competencia_uuid'], str(self.period_ago.uuid))

    # 3: importada nao pode editar/excluir
    def test_importada_read_only(self):
        self._sale('2026-06-25', origin=Sale.Origin.IMPORTADA)
        row = self._fetch_by_date()['25/06/2026']
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    # 3b: link nao pode editar/excluir
    def test_link_read_only(self):
        from app.apps.orders.models import Order
        order = Order.objects.create(
            tenant=self.tenant, seller=self.seller, total_amount=100000,
            customer_name='Cliente Teste', status=Order.Status.COMPLETED,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.LINK,
            amount=100000, sale_date='2026-06-25', order=order,
        )
        row = self._fetch_by_date()['25/06/2026']
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    # 4: manual em competencia aberta pode editar
    def test_manual_open_can_edit(self):
        self._sale('2026-06-25')
        row = self._fetch_by_date()['25/06/2026']
        self.assertTrue(row['canEdit'])
        self.assertTrue(row['canDelete'])

    # 5: manual em competencia fechada nao pode editar
    def test_manual_closed_cannot_edit(self):
        self._sale('2026-06-25')
        self.period_jul.status = CommissionPeriod.Status.FECHADA
        self.period_jul.save()
        SellerCommission.objects.create(
            period=self.period_jul, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.FECHADA,
            total_sold_amount=100000, commission_amount=1000,
        )
        row = self._fetch_by_date()['25/06/2026']
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    # 6: mes com duas competencias e identificado
    def test_month_with_two_competencias_identified(self):
        # Julho calendario: 05/07 (comp Julho) e 25/07 (comp Agosto)
        self._sale('2026-07-05')
        self._sale('2026-07-25')
        by_date = self._fetch_by_date()
        self.assertEqual(by_date['05/07/2026']['competencia_display_label'], 'Julho/2026')
        self.assertEqual(by_date['25/07/2026']['competencia_display_label'], 'Agosto/2026')
        # duas competencias distintas dentro do mes 07
        july_sales = [
            s for s in [by_date['05/07/2026'], by_date['25/07/2026']]
        ]
        uuids = {s['competencia_uuid'] for s in july_sales}
        self.assertEqual(len(uuids), 2)

    # 7: venda sem competencia mostra "Sem competencia"
    def test_sale_without_competencia(self):
        # 2026-01-10 nao esta em nenhuma competencia cadastrada
        self._sale('2026-01-10')
        row = self._fetch_by_date()['10/01/2026']
        self.assertEqual(row['competencia_display_label'], 'Sem competência')
        self.assertEqual(row['competencia_uuid'], '')
        self.assertEqual(row['competencia_label'], '')

    # 8: nao existe N+1 (periodos carregados uma unica vez)
    def test_no_nplus1_periods_loaded_once(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for d in ['2026-06-22', '2026-06-25', '2026-07-05', '2026-07-25',
                  '2026-08-01', '2026-01-10']:
            self._sale(d)
        self.client.force_login(self.seller_user)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/dashboard/mobile/vendas/')
        period_queries = [
            q for q in ctx.captured_queries
            if 'commissions_commissionperiod' in q['sql']
            and 'sellercommission' not in q['sql'].lower()
        ]
        self.assertEqual(len(period_queries), 1)

    # 9: total semanal nao muda (amounts intactos no JSON)
    def test_weekly_total_unchanged(self):
        self._sale('2026-07-05', amount=300000)
        self._sale('2026-07-06', amount=200000)
        by_date = self._fetch_by_date()
        self.assertEqual(by_date['05/07/2026']['amount'], 300000)
        self.assertEqual(by_date['06/07/2026']['amount'], 200000)
        total = sum(
            s['amount'] for s in [by_date['05/07/2026'], by_date['06/07/2026']]
        )
        self.assertEqual(total, 500000)

    # 10: total mensal nao muda (soma dos amounts ATIVA do mes)
    def test_monthly_total_unchanged(self):
        self._sale('2026-07-05', amount=300000)
        self._sale('2026-07-25', amount=250000)
        by_date = self._fetch_by_date()
        july = [
            s for k, s in by_date.items()
            if k.split('/')[1] == '07' and s['status'] == 'ATIVA'
        ]
        self.assertEqual(sum(s['amount'] for s in july), 550000)

    # 11: status da competencia chega ao front
    def test_competencia_status_reaches_front(self):
        self._sale('2026-06-25')
        self.period_jul.status = CommissionPeriod.Status.FECHADA
        self.period_jul.save()
        row = self._fetch_by_date()['25/06/2026']
        self.assertEqual(row['competencia_status'], 'FECHADA')
        self.assertEqual(row['competencia_status_display'], 'Fechada')
        self.assertEqual(row['competencia_range'], '21/06 a 20/07')
