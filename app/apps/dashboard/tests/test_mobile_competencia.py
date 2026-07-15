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

    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_mobile_home_goal_excludes_link_sales(self, mock_tz):
        from app.apps.orders.models import Order
        mock_tz.localdate.return_value = FAKE_TODAY
        goal_amount = 10_000_000
        from app.apps.sellers.models import SellerGoal
        SellerGoal.objects.create(
            seller=self.seller,
            month=7,
            year=2026,
            target_amount=goal_amount,
        )
        order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            total_amount=8_000_000,
            customer_name='Cliente Link',
            status=Order.Status.COMPLETED,
        )
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.LINK,
            amount=8_000_000,
            sale_date='2026-07-06',
            order=order,
        )

        resp = self.client.get('/dashboard/mobile/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['combined_month_total'], 9951859)
        self.assertEqual(resp.context['goal_remaining'], 48141)

    # 12: ranking mobile usa o range da competencia (25/06 conta)
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_mobile_ranking_uses_period_range(self, mock_tz):
        mock_tz.localdate.return_value = FAKE_TODAY
        resp = self.client.get('/dashboard/mobile/ranking/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['my_total'], 9951859)
        self.assertEqual(resp.context['seller_pos'], 1)

    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_mobile_ranking_excludes_link_sales(self, mock_tz):
        from app.apps.orders.models import Order
        mock_tz.localdate.return_value = FAKE_TODAY
        order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            total_amount=8_000_000,
            customer_name='Cliente Link',
            status=Order.Status.COMPLETED,
        )
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.LINK,
            amount=8_000_000,
            sale_date='2026-07-06',
            order=order,
        )

        resp = self.client.get('/dashboard/mobile/ranking/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['my_total'], 9951859)
        self.assertEqual(resp.context['combined_month_total'], 9951859)

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

    # minhas vendas: competence-first (seletor principal + filtro 2o por mes)
    def test_minhas_vendas_competence_first_selector_and_months(self):
        resp = self.client.get('/dashboard/mobile/vendas/')
        self.assertEqual(resp.status_code, 200)
        # competencia selecionada por default (unica com vendas)
        self.assertEqual(
            resp.context['selected_competence']['uuid'], str(self.period.uuid),
        )
        # filtro secundario: apenas meses que interceptam a competencia
        keys = [o['key'] for o in resp.context['month_options']]
        self.assertEqual(keys, ['2026-06', '2026-07'])

    def test_minhas_vendas_only_range_sales(self):
        # venda fora do range (25/07) nao aparece na competencia Julho/2026
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=1000, sale_date='2026-07-25', created_by=self.seller_user,
        )
        resp = self.client.get(
            '/dashboard/mobile/vendas/?period=' + str(self.period.uuid),
        )
        dates = {s['date'] for s in resp.context['sales_json']}
        self.assertIn('25/06/2026', dates)
        self.assertIn('05/07/2026', dates)
        self.assertNotIn('25/07/2026', dates)

    def test_minhas_vendas_no_nplus1_periods_loaded_once(self):
        # Todos os periodos do tenant sao carregados numa unica query,
        # independentemente do numero de vendas.
        for d in ['2026-06-26', '2026-06-27', '2026-07-06', '2026-07-07']:
            Sale.objects.create(
                tenant=self.tenant, seller=self.seller,
                origin=Sale.Origin.MANUAL, amount=1000, sale_date=d,
                created_by=self.seller_user,
            )
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


class MinhasVendasCompetenceFirstTest(TestCase):
    """PROMPT_44 - Minhas Vendas competence-first (LOTE 10)."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='CF Co', slug='cf-co',
            default_commission_rate=Decimal('0.01'), is_active=True,
            period_start_day=21,
        )
        self.seller_user = User.objects.create_user(
            username='vendcf', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Vend CF',
            phone='55999993333', commission_rate=Decimal('0.01'),
            is_active=True,
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
        self.client.force_login(self.seller_user)

    def _sale(self, sale_date, origin=Sale.Origin.MANUAL, amount=100000,
              status='ATIVA'):
        return Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=origin,
            amount=amount, sale_date=sale_date, status=status,
            created_by=self.seller_user,
        )

    def _ctx(self, period=None):
        url = '/dashboard/mobile/vendas/'
        if period is not None:
            url += '?period=' + str(period)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return resp.context

    # 1: competencia 21/06-20/07 mostra vendas de junho e julho juntas
    def test_competence_shows_june_and_july_together(self):
        self._sale('2026-06-25')
        self._sale('2026-07-05')
        ctx = self._ctx(self.period_jul.uuid)
        dates = {s['date'] for s in ctx['sales_json']}
        self.assertIn('25/06/2026', dates)
        self.assertIn('05/07/2026', dates)

    # 2: venda em 21/07 nao entra na competencia anterior
    def test_sale_2107_not_in_previous_competence(self):
        self._sale('2026-06-25')
        self._sale('2026-07-21')
        ctx = self._ctx(self.period_jul.uuid)
        dates = {s['date'] for s in ctx['sales_json']}
        self.assertNotIn('21/07/2026', dates)
        # 21/07 pertence a Agosto/2026
        ctx_ago = self._ctx(self.period_ago.uuid)
        self.assertIn('21/07/2026', {s['date'] for s in ctx_ago['sales_json']})

    # 3/4/5: semanas recalculadas a partir do inicio da competencia
    def test_week_ranges_from_competence_start(self):
        self._sale('2026-06-25')
        weeks = {w['index']: w['range'] for w in self._ctx(self.period_jul.uuid)['weeks_json']}
        self.assertEqual(weeks[1], '21/06 a 27/06')
        self.assertEqual(weeks[2], '28/06 a 04/07')
        self.assertEqual(weeks[5], '19/07 a 20/07')

    def test_sale_week_index_by_competence(self):
        s23 = self._sale('2026-06-23')  # semana 1
        s30 = self._sale('2026-06-30')  # semana 2
        by_uuid = {s['uuid']: s for s in self._ctx(self.period_jul.uuid)['sales_json']}
        self.assertEqual(by_uuid[str(s23.uuid)]['week_index'], 1)
        self.assertEqual(by_uuid[str(s30.uuid)]['week_index'], 2)

    # 6: filtro Junho mostra apenas 21/06-30/06 (reducao visual por month_key)
    def test_month_filter_june_only_june_sales(self):
        self._sale('2026-06-25')
        self._sale('2026-07-05')
        sales = self._ctx(self.period_jul.uuid)['sales_json']
        june = [s for s in sales if s['month_key'] == '2026-06']
        self.assertEqual([s['date'] for s in june], ['25/06/2026'])

    # 7: filtro Julho mostra apenas 01/07-20/07
    def test_month_filter_july_only_july_sales(self):
        self._sale('2026-06-25')
        self._sale('2026-07-05')
        sales = self._ctx(self.period_jul.uuid)['sales_json']
        july = [s for s in sales if s['month_key'] == '2026-07']
        self.assertEqual([s['date'] for s in july], ['05/07/2026'])

    # 8: filtro mensal nunca inclui venda de outra competencia
    def test_month_filter_never_includes_other_competence(self):
        self._sale('2026-07-05')   # Julho/2026
        self._sale('2026-07-25')   # Agosto/2026 (mesmo mes-calendario 07)
        sales = self._ctx(self.period_jul.uuid)['sales_json']
        july_calendar = [s for s in sales if s['month_key'] == '2026-07']
        # so a venda de 05/07 (Julho/2026); 25/07 pertence a Agosto e nunca entra
        self.assertEqual([s['date'] for s in july_calendar], ['05/07/2026'])

    # 9: default escolhe competencia operacional atual
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_default_picks_current_operational(self, mock_tz):
        # hoje dentro do range de Agosto/2026 -> competencia operacional atual
        mock_tz.localdate.return_value = date(2026, 7, 25)
        self._sale('2026-06-25')   # so Julho tem venda
        ctx = self._ctx()
        self.assertEqual(
            ctx['selected_competence']['uuid'], str(self.period_ago.uuid),
        )

    # 10: sem atual, escolhe a mais recente com vendas
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_default_picks_most_recent_with_sales(self, mock_tz):
        # hoje fora de qualquer competencia -> sem operacional atual
        mock_tz.localdate.return_value = date(2026, 12, 31)
        self._sale('2026-06-25')   # Julho/2026
        self._sale('2026-07-25')   # Agosto/2026 (mais recente)
        ctx = self._ctx()
        self.assertEqual(
            ctx['selected_competence']['uuid'], str(self.period_ago.uuid),
        )

    # 11: venda sem competencia nao entra no total
    def test_orphan_sale_not_in_total(self):
        self._sale('2026-06-25', amount=100000)   # Julho/2026
        self._sale('2026-01-10', amount=999999)   # orfa
        ctx = self._ctx(self.period_jul.uuid)
        self.assertEqual(ctx['competence_total'], 100000)
        self.assertTrue(ctx['has_orphan'])
        self.assertEqual(ctx['unassigned_count'], 1)
        # a orfa nao aparece nas vendas da competencia
        self.assertNotIn('10/01/2026', {s['date'] for s in ctx['sales_json']})

    def test_orphan_state_lists_only_orphans(self):
        self._sale('2026-06-25')                  # Julho/2026
        self._sale('2026-01-10', amount=999999)   # orfa
        ctx = self._ctx('sem-competencia')
        self.assertTrue(ctx['show_unassigned'])
        dates = {s['date'] for s in ctx['sales_json']}
        self.assertEqual(dates, {'10/01/2026'})

    # 14: backend nao envia todas as vendas historicas
    def test_backend_only_sends_range_sales(self):
        self._sale('2026-06-25')   # Julho
        self._sale('2026-07-25')   # Agosto
        self._sale('2026-01-10')   # orfa
        ctx = self._ctx(self.period_jul.uuid)
        self.assertEqual(len(ctx['sales_json']), 1)
        self.assertEqual(ctx['sales_json'][0]['date'], '25/06/2026')

    # 15: sem N+1 (periodos 1 query; SellerCommission em nro constante de
    # queries: 1 para os IDs do seletor + 1 do resolver de permissao).
    def test_no_nplus1(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for d in ['2026-06-22', '2026-06-25', '2026-06-28',
                  '2026-07-05', '2026-07-10', '2026-07-15']:
            self._sale(d)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(
                '/dashboard/mobile/vendas/?period=' + str(self.period_jul.uuid),
            )
        period_queries = [
            q for q in ctx.captured_queries
            if 'commissions_commissionperiod' in q['sql']
            and 'sellercommission' not in q['sql'].lower()
        ]
        sc_queries = [
            q for q in ctx.captured_queries
            if 'commissions_sellercommission' in q['sql'].lower()
        ]
        self.assertEqual(len(period_queries), 1)
        self.assertEqual(len(sc_queries), 2)

    # 16: total das semanas = total da competencia
    def test_sum_of_weeks_equals_competence_total(self):
        self._sale('2026-06-23', amount=300000)   # semana 1
        self._sale('2026-06-30', amount=200000)   # semana 2
        self._sale('2026-07-05', amount=150000)   # semana 3
        ctx = self._ctx(self.period_jul.uuid)
        weeks_total = {}
        for s in ctx['sales_json']:
            if s['status'] != 'ESTORNADA':
                weeks_total[s['week_index']] = (
                    weeks_total.get(s['week_index'], 0) + s['amount']
                )
        self.assertEqual(sum(weeks_total.values()), ctx['competence_total'])
        self.assertEqual(ctx['competence_total'], 650000)

    # 17: vendas estornadas nao entram no total
    def test_estornada_not_in_total(self):
        self._sale('2026-06-25', amount=100000)
        self._sale('2026-06-26', amount=500000, status='ESTORNADA')
        ctx = self._ctx(self.period_jul.uuid)
        self.assertEqual(ctx['competence_total'], 100000)

    # 13: datas nao deslocam por timezone (data civil intacta no JSON)
    def test_civil_dates_not_shifted(self):
        self._sale('2026-06-30')
        row = {s['date']: s for s in self._ctx(self.period_jul.uuid)['sales_json']}['30/06/2026']
        self.assertEqual(row['date_iso'], '2026-06-30')
        self.assertEqual(row['month_key'], '2026-06')

    # LOTE 1: cancelada nao aparece no seletor principal
    def test_cancelled_period_not_in_selector(self):
        self.period_ago.status = CommissionPeriod.Status.CANCELADA
        self.period_ago.save()
        self._sale('2026-07-25')   # dentro do range cancelado
        self._sale('2026-06-25')   # Julho
        ctx = self._ctx(self.period_jul.uuid)
        option_uuids = {o['uuid'] for o in ctx['competence_options']}
        self.assertNotIn(str(self.period_ago.uuid), option_uuids)

    # LOTE 9: competencia sem vendas -> estado vazio proprio
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_selected_competence_without_sales_empty(self, mock_tz):
        # Agosto sem vendas, mas listado por ser a competencia operacional atual
        mock_tz.localdate.return_value = date(2026, 7, 25)
        ctx = self._ctx(self.period_ago.uuid)
        self.assertEqual(ctx['sales_json'], [])
        self.assertEqual(ctx['competence_total'], 0)


class MinhasVendasPermissionsTest(TestCase):
    """PROMPT_44 LOTE 7/12/18 - permissoes preservadas (regra central)."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Perm Co', slug='perm-co',
            default_commission_rate=Decimal('0.01'), is_active=True,
            period_start_day=21,
        )
        self.seller_user = User.objects.create_user(
            username='vendperm', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Vend Perm',
            phone='55999994444', commission_rate=Decimal('0.01'),
            is_active=True,
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        self.client.force_login(self.seller_user)

    def _sale(self, origin=Sale.Origin.MANUAL, status='ATIVA', order=None):
        return Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=origin,
            amount=100000, sale_date='2026-06-25', status=status,
            order=order, created_by=self.seller_user,
        )

    def _row(self):
        resp = self.client.get(
            '/dashboard/mobile/vendas/?period=' + str(self.period.uuid),
        )
        self.assertEqual(resp.status_code, 200)
        return {s['date']: s for s in resp.context['sales_json']}['25/06/2026']

    def test_manual_open_editable(self):
        self._sale()
        row = self._row()
        self.assertTrue(row['canEdit'])
        self.assertTrue(row['canDelete'])

    def test_importada_read_only(self):
        self._sale(origin=Sale.Origin.IMPORTADA)
        row = self._row()
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    def test_link_sale_is_hidden_from_seller_sales(self):
        from app.apps.orders.models import Order
        order = Order.objects.create(
            tenant=self.tenant, seller=self.seller, total_amount=100000,
            customer_name='Cliente Teste', status=Order.Status.COMPLETED,
        )
        self._sale(origin=Sale.Origin.LINK, order=order)
        resp = self.client.get(
            '/dashboard/mobile/vendas/?period=' + str(self.period.uuid),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['sales_json'], [])
        self.assertEqual(resp.context['competence_total'], 0)

    def test_estornada_read_only(self):
        self._sale(status='ESTORNADA')
        row = self._row()
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    # 18: competencia fechada permanece somente leitura
    def test_closed_period_read_only(self):
        self._sale()
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.FECHADA,
            total_sold_amount=100000, commission_amount=1000,
        )
        row = self._row()
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    def test_closed_period_without_sc_blocks(self):
        self._sale()
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        row = self._row()
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    def test_sc_paga_blocks(self):
        self._sale()
        SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.PAGA,
            total_sold_amount=100000, commission_amount=1000,
        )
        row = self._row()
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    def test_sc_reaberta_allows_edit(self):
        self._sale()
        SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.REABERTA,
            total_sold_amount=100000, commission_amount=1000,
        )
        row = self._row()
        self.assertTrue(row['canEdit'])
        self.assertTrue(row['canDelete'])

    def test_fallback_html_read_only(self):
        # O fallback server-rendered nunca oferece link de edicao.
        self._sale()
        html = self.client.get(
            '/dashboard/mobile/vendas/?period=' + str(self.period.uuid),
        ).content.decode('utf-8')
        fallback = html.split('id="sales-fallback"', 1)[1]
        self.assertNotIn('/dashboard/mobile/lancar/?date=', fallback)


class MinhasVendasResponsiveWeeksTest(TestCase):
    """PROMPT_46B - ordem cronologica das semanas e responsividade."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Resp Co', slug='resp-co',
            default_commission_rate=Decimal('0.01'), is_active=True,
            period_start_day=21,
        )
        self.seller_user = User.objects.create_user(
            username='vendresp', role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Vend Resp',
            phone='55999996666', commission_rate=Decimal('0.01'),
            is_active=True,
        )
        # Julho/2026: 21/06 a 20/07 (5 semanas)
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        self.client.force_login(self.seller_user)

    def _sale(self, sale_date, origin=Sale.Origin.MANUAL, amount=100000,
              status='ATIVA', notes=''):
        return Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=origin,
            amount=amount, sale_date=sale_date, status=status, notes=notes,
            created_by=self.seller_user,
        )

    def _ctx(self):
        resp = self.client.get(
            '/dashboard/mobile/vendas/?period=' + str(self.period.uuid),
        )
        self.assertEqual(resp.status_code, 200)
        return resp

    # 1/4/5/6: weeks_json em ordem crescente com ranges corretos
    def test_weeks_json_ascending_with_ranges(self):
        self._sale('2026-06-25')
        weeks = self._ctx().context['weeks_json']
        indices = [w['index'] for w in weeks]
        self.assertEqual(indices, sorted(indices))
        self.assertEqual(indices[0], 1)
        by_index = {w['index']: w['range'] for w in weeks}
        self.assertEqual(by_index[1], '21/06 a 27/06')
        self.assertEqual(by_index[2], '28/06 a 04/07')
        self.assertEqual(by_index[3], '05/07 a 11/07')
        self.assertEqual(by_index[5], '19/07 a 20/07')

    # 1/2: o JS ordena as semanas de forma crescente (Semana 1 primeiro)
    def test_js_sorts_weeks_ascending(self):
        self._sale('2026-06-25')
        html = self._ctx().content.decode('utf-8')
        self.assertIn('return a.index - b.index;', html)
        self.assertNotIn('return b.index - a.index;', html)

    # 3: init expande o primeiro grupo (Semana 1 apos ordenacao crescente)
    def test_init_opens_first_group(self):
        self._sale('2026-06-25')
        html = self._ctx().content.decode('utf-8')
        self.assertIn('this.expandedWeeks[weeks[0].key] = true;', html)
        # troca de filtro reabre a primeira semana visivel se nenhuma aberta
        self.assertIn('onFilterChange()', html)

    # 11: o valor monetario nao usa truncate e usa whitespace-nowrap
    def test_value_nowrap_without_truncate(self):
        self._sale('2026-06-25', amount=123456789)
        html = self._ctx().content.decode('utf-8')
        self.assertIn(
            'font-semibold text-[14px] whitespace-nowrap', html,
        )
        # a antiga classe de truncar o valor nao deve existir
        self.assertNotIn('text-[14px] truncate block', html)

    # 10: regiao de badges usa flex-wrap; observacao em linha propria
    def test_badges_region_flex_wrap(self):
        self._sale('2026-06-25', notes='obs')
        html = self._ctx().content.decode('utf-8')
        self.assertIn('flex flex-wrap items-center gap-1.5 mt-1.5', html)
        # observacao com acesso ao conteudo completo (title) e sem truncate
        self.assertIn(':title="sale.notes"', html)
        self.assertIn('basis-full text-[11px] text-gray-400 break-words', html)

    # 12: resumo com regra responsiva
    def test_summary_responsive_classes(self):
        self._sale('2026-06-25')
        html = self._ctx().content.decode('utf-8')
        self.assertIn('grid grid-cols-2 sm:grid-cols-3', html)
        self.assertIn('col-span-2 sm:col-span-1', html)

    # 13: filtro por mes com regra responsiva
    def test_month_filter_responsive_classes(self):
        self._sale('2026-06-25')
        self._sale('2026-07-05')
        html = self._ctx().content.decode('utf-8')
        self.assertIn('flex flex-col sm:flex-row sm:items-center gap-2', html)

    # 7: total das semanas = total da competencia
    def test_sum_of_weeks_equals_total(self):
        self._sale('2026-06-23', amount=300000)   # S1
        self._sale('2026-06-30', amount=200000)   # S2
        self._sale('2026-07-05', amount=150000)   # S3
        ctx = self._ctx().context
        weeks_total = {}
        for s in ctx['sales_json']:
            if s['status'] != 'ESTORNADA':
                weeks_total[s['week_index']] = (
                    weeks_total.get(s['week_index'], 0) + s['amount']
                )
        self.assertEqual(sum(weeks_total.values()), ctx['competence_total'])
        self.assertEqual(ctx['competence_total'], 650000)

    # 16: estornada fora do total
    def test_estornada_not_in_total(self):
        self._sale('2026-06-25', amount=100000)
        self._sale('2026-06-26', amount=500000, status='ESTORNADA')
        self.assertEqual(self._ctx().context['competence_total'], 100000)

    # 17: datas civis 23/06 e 30/06 nao deslocam
    def test_civil_dates_not_shifted(self):
        self._sale('2026-06-23')
        self._sale('2026-06-30')
        by_iso = {s['date_iso']: s for s in self._ctx().context['sales_json']}
        self.assertEqual(by_iso['2026-06-23']['date'], '23/06/2026')
        self.assertEqual(by_iso['2026-06-30']['date'], '30/06/2026')

    # 14/15: acoes conforme canEdit/canDelete; importada somente leitura
    def test_actions_respect_permissions(self):
        self._sale('2026-06-24', origin=Sale.Origin.MANUAL)
        self._sale('2026-06-25', origin=Sale.Origin.IMPORTADA)
        by_date = {s['date']: s for s in self._ctx().context['sales_json']}
        self.assertTrue(by_date['24/06/2026']['canEdit'])
        self.assertTrue(by_date['24/06/2026']['canDelete'])
        self.assertFalse(by_date['25/06/2026']['canEdit'])
        self.assertFalse(by_date['25/06/2026']['canDelete'])

    # 18: nenhuma migration pendente/criada
    def test_no_pending_migrations(self):
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
        self.assertFalse(changed, 'Migrations pendentes foram detectadas')
