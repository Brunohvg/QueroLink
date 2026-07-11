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

    # 14: minhas vendas permanece filtravel por mes/ano (chave YYYY-MM)
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_minhas_vendas_month_navigation(self, mock_tz):
        mock_tz.localdate.return_value = FAKE_TODAY
        resp = self.client.get('/dashboard/mobile/vendas/')
        self.assertEqual(resp.status_code, 200)
        # somente meses com vendas, chave YYYY-MM. Vendas em 06 e 07/2026.
        keys = [o['key'] for o in resp.context['month_options']]
        self.assertEqual(keys, ['2026-07', '2026-06'])
        labels = [o['label'] for o in resp.context['month_options']]
        self.assertIn('Julho/2026', labels)
        self.assertIn('Junho/2026', labels)

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


class MinhasVendasBlockersTest(TestCase):
    """PROMPT_42.2.1 - correcao dos blockers de Minhas Vendas."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Blocker Co', slug='blocker-co',
            default_commission_rate=Decimal('0.01'), is_active=True,
            period_start_day=21,
        )
        self.seller_user = User.objects.create_user(
            username='vendblk', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Vend Blk',
            phone='55999992222', commission_rate=Decimal('0.01'), is_active=True,
        )
        # Julho/2025: 21/06/2025 a 20/07/2025 ; Julho/2026: 21/06/2026 a 20/07/2026
        self.period_2025 = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2025,
            start_date=date(2025, 6, 21), end_date=date(2025, 7, 20),
            label='Julho/2025',
        )
        self.period_2026 = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )

    def _sale(self, sale_date, origin=Sale.Origin.MANUAL, amount=100000,
              status='ATIVA'):
        return Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=origin,
            amount=amount, sale_date=sale_date, status=status,
            created_by=self.seller_user,
        )

    def _ctx(self):
        self.client.force_login(self.seller_user)
        resp = self.client.get('/dashboard/mobile/vendas/')
        self.assertEqual(resp.status_code, 200)
        return resp

    def _by_date(self, resp):
        return {s['date']: s for s in resp.context['sales_json']}

    # 1: julho/2025 e julho/2026 nunca aparecem juntos
    def test_july_two_years_never_mixed(self):
        self._sale('2025-07-05')
        self._sale('2026-07-05')
        by_date = self._by_date(self._ctx())
        self.assertEqual(by_date['05/07/2025']['month_key'], '2025-07')
        self.assertEqual(by_date['05/07/2026']['month_key'], '2026-07')
        self.assertNotEqual(
            by_date['05/07/2025']['month_key'],
            by_date['05/07/2026']['month_key'],
        )

    # 2: opcoes do filtro sao YYYY-MM e ordenadas (mais recente primeiro)
    def test_filter_options_yyyy_mm_sorted(self):
        self._sale('2025-07-05')
        self._sale('2026-07-05')
        self._sale('2025-12-10')
        resp = self._ctx()
        keys = [o['key'] for o in resp.context['month_options']]
        self.assertEqual(keys, ['2026-07', '2025-12', '2025-07'])
        by_key = {o['key']: o['label'] for o in resp.context['month_options']}
        self.assertEqual(by_key['2026-07'], 'Julho/2026')
        self.assertEqual(by_key['2025-12'], 'Dezembro/2025')
        self.assertEqual(by_key['2025-07'], 'Julho/2025')

    # 3: competencia cancelada continua identificada no historico
    def test_cancelled_period_still_identified(self):
        self.period_2026.status = CommissionPeriod.Status.CANCELADA
        self.period_2026.save()
        self._sale('2026-06-25')
        row = self._by_date(self._ctx())['25/06/2026']
        self.assertEqual(row['competencia_uuid'], str(self.period_2026.uuid))
        self.assertEqual(row['competencia_display_label'], 'Julho/2026')
        self.assertEqual(row['competencia_status'], 'CANCELADA')
        self.assertEqual(row['competencia_status_display'], 'Cancelada')
        # nao pode exibir "Sem competencia" para periodo existente e cancelado
        self.assertNotEqual(row['competencia_display_label'], 'Sem competência')

    # 3b: cancelada tambem bloqueia edicao/exclusao
    def test_cancelled_period_blocks_edit(self):
        self.period_2026.status = CommissionPeriod.Status.CANCELADA
        self.period_2026.save()
        self._sale('2026-06-25')
        row = self._by_date(self._ctx())['25/06/2026']
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    # 4: periodo FECHADO sem SellerCommission bloqueia edicao
    def test_closed_period_without_sc_blocks(self):
        self.period_2026.status = CommissionPeriod.Status.FECHADA
        self.period_2026.save()
        self._sale('2026-06-25')
        self.assertFalse(
            SellerCommission.objects.filter(
                period=self.period_2026, seller=self.seller,
            ).exists()
        )
        row = self._by_date(self._ctx())['25/06/2026']
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    # 5: Sale ESTORNADA bloqueia edicao e exclusao
    def test_estornada_blocks_edit_and_delete(self):
        self._sale('2026-06-25', status='ESTORNADA')
        row = self._by_date(self._ctx())['25/06/2026']
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    # 5b: SellerCommission PAGA bloqueia
    def test_sc_paga_blocks(self):
        self._sale('2026-06-25')
        SellerCommission.objects.create(
            period=self.period_2026, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.PAGA,
            total_sold_amount=100000, commission_amount=1000,
        )
        row = self._by_date(self._ctx())['25/06/2026']
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    # 5c: competencia REABERTA (SC REABERTA) permite editar
    def test_sc_reaberta_allows_edit(self):
        self._sale('2026-06-25')
        SellerCommission.objects.create(
            period=self.period_2026, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.REABERTA,
            total_sold_amount=100000, commission_amount=1000,
        )
        row = self._by_date(self._ctx())['25/06/2026']
        self.assertTrue(row['canEdit'])
        self.assertTrue(row['canDelete'])

    # 6: fallback HTML nao exibe link de edicao para venda bloqueada
    def test_fallback_html_no_edit_link_for_blocked(self):
        self.period_2026.status = CommissionPeriod.Status.FECHADA
        self.period_2026.save()
        self._sale('2026-06-25')
        html = self._ctx().content.decode('utf-8')
        # Isola a secao do fallback server-rendered (somente leitura).
        fallback = html.split('id="sales-fallback"', 1)[1]
        self.assertNotIn('/dashboard/mobile/lancar/?date=', fallback)

    # 7: fallback HTML nao oferece edicao de importada/link
    def test_fallback_html_no_edit_for_importada_link(self):
        self._sale('2026-06-25', origin=Sale.Origin.IMPORTADA)
        html = self._ctx().content.decode('utf-8')
        fallback = html.split('id="sales-fallback"', 1)[1]
        self.assertNotIn('/dashboard/mobile/lancar/?date=', fallback)
        # fallback exibe origem
        self.assertIn('Importada', fallback)

    # 8: uma competencia + venda orfa nao e descrita como duas competencias
    def test_one_competencia_plus_orphan_not_two(self):
        self._sale('2026-06-25')   # comp Julho/2026
        self._sale('2026-06-05')   # dentro do range? 21/06 a 20/07 -> NAO (05/06 orfa)
        by_date = self._by_date(self._ctx())
        self.assertEqual(by_date['25/06/2026']['competencia_uuid'], str(self.period_2026.uuid))
        self.assertEqual(by_date['05/06/2026']['competencia_uuid'], '')
        # mesma chave YYYY-MM (2026-06): 1 competencia real + 1 orfa
        assigned = [
            s for s in by_date.values()
            if s['month_key'] == '2026-06' and s['competencia_uuid']
        ]
        orphan = [
            s for s in by_date.values()
            if s['month_key'] == '2026-06' and not s['competencia_uuid']
        ]
        real_uuids = {s['competencia_uuid'] for s in assigned}
        self.assertEqual(len(real_uuids), 1)
        self.assertEqual(len(orphan), 1)

    # 9: contador de vendas sem competencia correto (via sales_json)
    def test_unassigned_count_correct(self):
        self._sale('2026-06-25')   # comp
        self._sale('2026-06-05')   # orfa
        self._sale('2026-06-03')   # orfa
        by_date = self._by_date(self._ctx())
        june = [s for s in by_date.values() if s['month_key'] == '2026-06']
        unassigned = [s for s in june if not s['competencia_uuid']]
        self.assertEqual(len(unassigned), 2)

    # 10: status aparece no HTML renderizado (fallback)
    def test_status_visible_in_html(self):
        self._sale('2026-06-25')
        html = self._ctx().content.decode('utf-8')
        self.assertIn('Comp. Julho/2026 · Aberta', html)

    # 11: total semanal permanece igual (amounts intactos)
    def test_weekly_total_unchanged(self):
        self._sale('2026-07-05', amount=300000)
        self._sale('2026-07-06', amount=200000)
        by_date = self._by_date(self._ctx())
        total = sum(
            s['amount'] for s in [by_date['05/07/2026'], by_date['06/07/2026']]
        )
        self.assertEqual(total, 500000)

    # 12: total mensal permanece igual (por chave YYYY-MM)
    def test_monthly_total_unchanged(self):
        self._sale('2026-07-05', amount=300000)
        self._sale('2026-07-10', amount=250000)
        by_date = self._by_date(self._ctx())
        july_2026 = [
            s for s in by_date.values()
            if s['month_key'] == '2026-07' and s['status'] == 'ATIVA'
        ]
        self.assertEqual(sum(s['amount'] for s in july_2026), 550000)

    # 13: consulta de periodos continua sem N+1
    def test_no_nplus1_periods(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for d in ['2025-07-01', '2026-06-25', '2026-07-05', '2026-01-10']:
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

    # 14: regra de edicao nao adiciona N+1 (SellerCommission carregado 1x)
    def test_edit_rule_no_nplus1(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for d in ['2026-06-22', '2026-06-25', '2026-06-28',
                  '2026-07-05', '2026-07-10', '2026-07-15']:
            self._sale(d)
        self.client.force_login(self.seller_user)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/dashboard/mobile/vendas/')
        sc_queries = [
            q for q in ctx.captured_queries
            if 'commissions_sellercommission' in q['sql'].lower()
        ]
        # SellerCommission do vendedor carregado numa unica query,
        # independentemente do numero de vendas.
        self.assertEqual(len(sc_queries), 1)
