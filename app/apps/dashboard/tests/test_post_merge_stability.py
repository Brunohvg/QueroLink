"""PROMPT 45 - estabilizacao pos-merge competence-first.

Cobre:
- LOTE 1: UUID explicito nunca cai em fallback (Http404).
- LOTE 2: deteccao de sobreposicao de competencia atual (erro controlado).
- LOTE 3: seletor completo (venda OU SellerCommission OU operacional atual).
- LOTE 4: ausencia de N+1 (queries constantes vs nro de vendas/competencias).
- LOTE 5: regressoes-chave (datas civis, outro tenant, totais).
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.contrib.auth import get_user_model

from app.apps.accounts.models import Tenant
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale

User = get_user_model()


def _period_query_count(ctx):
    return len([
        q for q in ctx.captured_queries
        if 'commissions_commissionperiod' in q['sql']
        and 'sellercommission' not in q['sql'].lower()
    ])


def _sc_query_count(ctx):
    return len([
        q for q in ctx.captured_queries
        if 'commissions_sellercommission' in q['sql'].lower()
    ])


class BaseSetup(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='PM Co', slug='pm-co',
            default_commission_rate=Decimal('0.01'), is_active=True,
            period_start_day=21,
        )
        self.seller_user = User.objects.create_user(
            username='vendpm', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Vend PM',
            phone='55999995555', commission_rate=Decimal('0.01'),
            is_active=True,
        )
        self.period_jul = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        self.client.force_login(self.seller_user)

    def _sale(self, sale_date, origin=Sale.Origin.MANUAL, amount=100000,
              status='ATIVA', seller=None):
        return Sale.objects.create(
            tenant=self.tenant, seller=seller or self.seller, origin=origin,
            amount=amount, sale_date=sale_date, status=status,
            created_by=self.seller_user,
        )

    def _get(self, period=None):
        url = '/dashboard/mobile/vendas/'
        if period is not None:
            url += '?period=' + str(period)
        return self.client.get(url)


class Lote1ExplicitUuidNoFallback(BaseSetup):
    """UUID explicito nunca substituido silenciosamente -> Http404."""

    def test_invalid_uuid_returns_404(self):
        self._sale('2026-06-25')
        self.assertEqual(self._get('not-a-uuid').status_code, 404)

    def test_other_tenant_period_returns_404(self):
        other = Tenant.objects.create(
            company_name='Other', slug='other',
            default_commission_rate=Decimal('0.01'), period_start_day=21,
        )
        other_period = CommissionPeriod.objects.create(
            tenant=other, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        self._sale('2026-06-25')
        self.assertEqual(self._get(other_period.uuid).status_code, 404)

    def test_cancelled_period_returns_404(self):
        self.period_jul.status = CommissionPeriod.Status.CANCELADA
        self.period_jul.save()
        self._sale('2026-06-25')  # dentro do range cancelado
        self.assertEqual(self._get(self.period_jul.uuid).status_code, 404)

    def test_valid_period_not_accessible_returns_404(self):
        # Periodo do proprio tenant, mas sem venda/SC do vendedor e nao atual.
        other_period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=8, year=2026,
            start_date=date(2026, 7, 21), end_date=date(2026, 8, 20),
            label='Agosto/2026',
        )
        self._sale('2026-06-25')  # so em Julho
        self.assertEqual(self._get(other_period.uuid).status_code, 404)

    def test_no_silent_fallback_on_invalid(self):
        # Com UUID invalido NAO pode retornar 200 com a competencia atual.
        self._sale('2026-06-25')
        resp = self._get('00000000-0000-0000-0000-000000000000')
        self.assertEqual(resp.status_code, 404)

    def test_valid_period_without_sales_accessible_via_sc(self):
        # UUID valido, sem vendas, mas acessivel por SellerCommission -> 200.
        SellerCommission.objects.create(
            period=self.period_jul, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.ABERTA,
        )
        resp = self._get(self.period_jul.uuid)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['sales_json'], [])
        self.assertEqual(resp.context['competence_total'], 0)


class Lote2OverlapDetection(BaseSetup):
    """Sobreposicao de competencia atual = erro controlado, sem escolha muda."""

    def _overlap_period(self, month, status=CommissionPeriod.Status.ABERTA):
        # Cria periodo sobreposto (bypassa clean via objects.create) para
        # simular estado corrompido/misconfig do tenant.
        return CommissionPeriod.objects.create(
            tenant=self.tenant, month=month, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            status=status, label=f'Comp {month}',
        )

    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_two_open_periods_covering_today_controlled_error(self, mock_tz):
        mock_tz.localdate.return_value = date(2026, 7, 10)
        # period_jul (month 7) ja cobre; cria outra (month 6) sobreposta ABERTA
        self._overlap_period(6)
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['selected_competence'])
        self.assertIn('sobrepostas', resp.context['error'])

    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_open_plus_partially_closed_overlap_error(self, mock_tz):
        mock_tz.localdate.return_value = date(2026, 7, 10)
        self._overlap_period(
            6, status=CommissionPeriod.Status.PARCIALMENTE_FECHADA,
        )
        resp = self._get()
        self.assertIsNone(resp.context['selected_competence'])
        self.assertIn('sobrepostas', resp.context['error'])

    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_cancelled_overlap_not_counted(self, mock_tz):
        mock_tz.localdate.return_value = date(2026, 7, 10)
        self._overlap_period(6, status=CommissionPeriod.Status.CANCELADA)
        self._sale('2026-06-25')
        resp = self._get()
        # cancelada nao conta -> apenas Julho e operacional atual, funciona
        self.assertIsNone(resp.context.get('error'))
        self.assertEqual(
            resp.context['selected_competence']['uuid'],
            str(self.period_jul.uuid),
        )

    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_single_valid_period_works(self, mock_tz):
        mock_tz.localdate.return_value = date(2026, 7, 10)
        self._sale('2026-06-25')
        resp = self._get()
        self.assertIsNone(resp.context.get('error'))
        self.assertEqual(
            resp.context['selected_competence']['uuid'],
            str(self.period_jul.uuid),
        )


class Lote3CompleteSelector(BaseSetup):
    """Seletor: venda OU SellerCommission OU operacional atual."""

    def _uuids(self, resp):
        return {o['uuid'] for o in resp.context['competence_options']}

    def test_period_with_sales_in_selector(self):
        self._sale('2026-06-25')
        self.assertIn(str(self.period_jul.uuid), self._uuids(self._get()))

    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_current_period_without_sales_in_selector(self, mock_tz):
        mock_tz.localdate.return_value = date(2026, 7, 10)
        resp = self._get()
        self.assertIn(str(self.period_jul.uuid), self._uuids(resp))

    def test_closed_period_with_sc_zero_sales_in_selector(self):
        self.period_jul.status = CommissionPeriod.Status.FECHADA
        self.period_jul.save()
        SellerCommission.objects.create(
            period=self.period_jul, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.FECHADA,
            total_sold_amount=0, commission_amount=0,
        )
        resp = self._get(self.period_jul.uuid)
        self.assertIn(str(self.period_jul.uuid), self._uuids(resp))
        self.assertEqual(resp.context['competence_total'], 0)
        self.assertEqual(resp.context['sales_json'], [])

    def test_seller_on_vacation_whole_period_appears_via_sc(self):
        # Sem nenhuma venda no periodo, mas com SellerCommission gerada.
        SellerCommission.objects.create(
            period=self.period_jul, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.ABERTA,
        )
        self.assertIn(str(self.period_jul.uuid), self._uuids(self._get()))

    def test_other_seller_period_not_in_selector(self):
        # Outro vendedor tem venda/SC em Agosto; este vendedor nao.
        other_user = User.objects.create_user(
            username='outro', password='x',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        other_seller = Seller.objects.create(
            tenant=self.tenant, user=other_user, name='Outro',
            phone='55999990001', commission_rate=Decimal('0.01'),
        )
        period_ago = CommissionPeriod.objects.create(
            tenant=self.tenant, month=8, year=2026,
            start_date=date(2026, 7, 21), end_date=date(2026, 8, 20),
            label='Agosto/2026',
        )
        Sale.objects.create(
            tenant=self.tenant, seller=other_seller, origin=Sale.Origin.MANUAL,
            amount=100000, sale_date='2026-07-25', created_by=other_user,
        )
        self._sale('2026-06-25')  # este vendedor so em Julho
        uuids = self._uuids(self._get())
        self.assertIn(str(self.period_jul.uuid), uuids)
        self.assertNotIn(str(period_ago.uuid), uuids)

    def test_cancelled_period_not_in_selector(self):
        self._sale('2026-06-25')
        cancel = CommissionPeriod.objects.create(
            tenant=self.tenant, month=8, year=2026,
            start_date=date(2026, 7, 21), end_date=date(2026, 8, 20),
            status=CommissionPeriod.Status.CANCELADA, label='Cancelada',
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=100000, sale_date='2026-07-25', created_by=self.seller_user,
        )
        self.assertNotIn(str(cancel.uuid), self._uuids(self._get()))


class Lote4Performance(BaseSetup):
    """Queries constantes independentes do nro de vendas/competencias."""

    def _counts(self, url):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(url)
        return _period_query_count(ctx), _sc_query_count(ctx)

    def test_queries_constant_vs_number_of_sales(self):
        for d in ['2026-06-22', '2026-06-25', '2026-06-28']:
            self._sale(d)
        p1, s1 = self._counts(
            '/dashboard/mobile/vendas/?period=' + str(self.period_jul.uuid),
        )
        # Preenche todos os demais dias do range (datas distintas: constraint
        # unique (seller, sale_date)).
        existing = set(
            Sale.objects.filter(seller=self.seller)
            .values_list('sale_date', flat=True)
        )
        d = date(2026, 6, 21)
        while d <= date(2026, 7, 20):
            if d not in existing:
                self._sale(d.isoformat())
            d += timedelta(days=1)
        p2, s2 = self._counts(
            '/dashboard/mobile/vendas/?period=' + str(self.period_jul.uuid),
        )
        # Nro de queries de periodo/SC nao cresce com o nro de vendas.
        self.assertEqual((p1, s1), (p2, s2))
        self.assertEqual(p1, 1)
        self.assertEqual(s1, 2)

    def test_queries_constant_vs_number_of_competences(self):
        self._sale('2026-06-25')
        p1, s1 = self._counts('/dashboard/mobile/vendas/')
        # 24 competencias (12 meses x 2 anos), sem sobreposicao com Julho/2026.
        for year in (2023, 2024):
            for month in range(1, 13):
                start = date(year, month, 1)
                end = date(year, month, 28)
                CommissionPeriod.objects.create(
                    tenant=self.tenant, month=month, year=year,
                    start_date=start, end_date=end, label=f'{month}/{year}',
                )
        p2, s2 = self._counts('/dashboard/mobile/vendas/')
        self.assertEqual(p1, 1)
        self.assertEqual(p2, 1)
        self.assertEqual(s1, s2)

    def test_backend_does_not_send_all_history(self):
        self._sale('2026-06-25')                       # Julho
        self._sale('2026-01-10')                       # orfa (fora)
        period_ago = CommissionPeriod.objects.create(
            tenant=self.tenant, month=8, year=2026,
            start_date=date(2026, 7, 21), end_date=date(2026, 8, 20),
            label='Agosto/2026',
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=100000, sale_date='2026-07-25', created_by=self.seller_user,
        )
        resp = self._get(self.period_jul.uuid)
        dates = {s['date'] for s in resp.context['sales_json']}
        self.assertEqual(dates, {'25/06/2026'})
        self.assertEqual(len(resp.context['sales_json']), 1)


class Lote5Regression(BaseSetup):
    """Regressoes-chave apos merge integrado."""

    def test_competence_shows_june_and_july(self):
        self._sale('2026-06-25')
        self._sale('2026-07-05')
        dates = {s['date'] for s in self._get(self.period_jul.uuid).context['sales_json']}
        self.assertIn('25/06/2026', dates)
        self.assertIn('05/07/2026', dates)

    def test_sale_2107_not_included(self):
        CommissionPeriod.objects.create(
            tenant=self.tenant, month=8, year=2026,
            start_date=date(2026, 7, 21), end_date=date(2026, 8, 20),
            label='Agosto/2026',
        )
        self._sale('2026-06-25')
        self._sale('2026-07-21')
        dates = {s['date'] for s in self._get(self.period_jul.uuid).context['sales_json']}
        self.assertNotIn('21/07/2026', dates)

    def test_civil_dates_not_shifted(self):
        self._sale('2026-06-23')
        self._sale('2026-06-30')
        by_iso = {s['date_iso']: s for s in self._get(self.period_jul.uuid).context['sales_json']}
        self.assertIn('2026-06-23', by_iso)
        self.assertIn('2026-06-30', by_iso)
        self.assertEqual(by_iso['2026-06-23']['date'], '23/06/2026')
        self.assertEqual(by_iso['2026-06-30']['date'], '30/06/2026')

    def test_weeks_follow_competence_start(self):
        self._sale('2026-06-25')
        weeks = {w['index']: w['range'] for w in self._get(self.period_jul.uuid).context['weeks_json']}
        self.assertEqual(weeks[1], '21/06 a 27/06')
        self.assertEqual(weeks[2], '28/06 a 04/07')
        self.assertEqual(weeks[5], '19/07 a 20/07')

    def test_orphan_not_in_total(self):
        self._sale('2026-06-25', amount=100000)
        self._sale('2026-01-10', amount=999999)
        ctx = self._get(self.period_jul.uuid).context
        self.assertEqual(ctx['competence_total'], 100000)
        self.assertTrue(ctx['has_orphan'])

    def test_sum_weeks_equals_total(self):
        self._sale('2026-06-23', amount=300000)
        self._sale('2026-07-05', amount=200000)
        ctx = self._get(self.period_jul.uuid).context
        weeks_total = {}
        for s in ctx['sales_json']:
            if s['status'] != 'ESTORNADA':
                weeks_total[s['week_index']] = (
                    weeks_total.get(s['week_index'], 0) + s['amount']
                )
        self.assertEqual(sum(weeks_total.values()), ctx['competence_total'])

    def test_estornada_not_in_total(self):
        self._sale('2026-06-25', amount=100000)
        self._sale('2026-06-26', amount=500000, status='ESTORNADA')
        self.assertEqual(
            self._get(self.period_jul.uuid).context['competence_total'], 100000,
        )

    def test_importada_link_estornada_read_only(self):
        from app.apps.orders.models import Order
        self._sale('2026-06-22', origin=Sale.Origin.IMPORTADA)
        order = Order.objects.create(
            tenant=self.tenant, seller=self.seller, total_amount=100000,
            customer_name='C', status=Order.Status.COMPLETED,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.LINK,
            amount=100000, sale_date='2026-06-23', order=order,
        )
        self._sale('2026-06-24', status='ESTORNADA')
        by_date = {s['date']: s for s in self._get(self.period_jul.uuid).context['sales_json']}
        for d in ['22/06/2026', '23/06/2026', '24/06/2026']:
            self.assertFalse(by_date[d]['canEdit'])
            self.assertFalse(by_date[d]['canDelete'])

    def test_closed_period_blocks_edit(self):
        self._sale('2026-06-25')
        self.period_jul.status = CommissionPeriod.Status.FECHADA
        self.period_jul.save()
        SellerCommission.objects.create(
            period=self.period_jul, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.FECHADA,
            total_sold_amount=100000, commission_amount=1000,
        )
        row = {s['date']: s for s in self._get(self.period_jul.uuid).context['sales_json']}['25/06/2026']
        self.assertFalse(row['canEdit'])
        self.assertFalse(row['canDelete'])

    def test_other_tenant_cannot_access_period(self):
        other = Tenant.objects.create(
            company_name='Other2', slug='other2',
            default_commission_rate=Decimal('0.01'), period_start_day=21,
        )
        other_period = CommissionPeriod.objects.create(
            tenant=other, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        self._sale('2026-06-25')
        self.assertEqual(self._get(other_period.uuid).status_code, 404)
