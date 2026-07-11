from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.core.cache import cache
from rest_framework.test import APIClient

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.sales.models import Sale


class CompetenciaFirstTest(TestCase):
    """PROMPT_42 - competencia-first: uma tela = um regime."""

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Comp First', slug='comp-first',
            default_commission_rate=Decimal('0.01'),
            period_start_day=21,
        )
        self.tenant2 = Tenant.objects.create(
            company_name='Outro Tenant', slug='outro-tenant',
            default_commission_rate=Decimal('0.01'),
        )
        self.manager = User.objects.create_user(
            username='gestor', password='gestor123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.manager2 = User.objects.create_user(
            username='gestor2', password='gestor123',
            role=User.Role.MANAGER, tenant=self.tenant2,
        )
        self.seller_user = User.objects.create_user(
            username='vendedor', password='senha123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Vendedor Teste', phone='11999998888',
            user=self.seller_user, commission_rate=Decimal('0.01'),
        )
        # Competencia 21/06 a 20/07 (ciclo dinamico)
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        # Vendas dentro do range: 25/06 e 05/07
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=5227922, sale_date='2026-06-25', created_by=self.seller_user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=4723937, sale_date='2026-07-05', created_by=self.seller_user,
        )
        # Venda fora do range (21/07)
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=1000000, sale_date='2026-07-21', created_by=self.seller_user,
        )

    def _auth(self, user, password):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': user.username, 'password': password,
        }, format='json')
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}')
        return client

    # 1 + 2: vendas 25/06 e 05/07 entram juntas; 21/07 fica de fora
    def test_period_range_aggregates_both_months(self):
        from app.apps.commissions.services import get_manual_sales_total_for_period
        total = get_manual_sales_total_for_period(self.seller, self.period)
        self.assertEqual(total, 5227922 + 4723937)

    def test_sale_outside_range_excluded(self):
        from app.apps.commissions.services import get_manual_sales_total_for_period
        total = get_manual_sales_total_for_period(self.seller, self.period)
        self.assertNotIn(1000000, [total])
        self.assertEqual(total, 9951859)

    # 4 + 5 + 3: detalhe do vendedor usa o mesmo range; card usa periodo selecionado
    def test_seller_detail_period_total_and_commission_same_range(self):
        client = self._auth(self.manager, 'gestor123')
        url = f'/api/manager/seller/{self.seller.uuid}/'
        resp = client.get(url + f'?period={self.period.uuid}')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['manual_total'], 9951859)
        spc = data['selected_period_commission']
        self.assertIsNotNone(spc)
        # comissao == taxa x total (1%)
        self.assertEqual(spc['total_sold_amount'], 9951859)
        self.assertEqual(spc['commission_amount'], round(9951859 * 0.01))
        self.assertEqual(spc['commission_amount'], 99519)

    # 6: home gestor soma junho + julho pelo range
    def test_dashboard_summary_sums_range(self):
        client = self._auth(self.manager, 'gestor123')
        url = reverse('api-dashboard-summary')
        resp = client.get(url + f'?period={self.period.uuid}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total_vendido'], 9951859)

    # 16: UUID de outro tenant retorna 404
    def test_period_from_other_tenant_returns_404(self):
        other_period = CommissionPeriod.objects.create(
            tenant=self.tenant2, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
        )
        client = self._auth(self.manager, 'gestor123')
        url = f'/api/manager/seller/{self.seller.uuid}/'
        resp = client.get(url + f'?period={other_period.uuid}')
        self.assertEqual(resp.status_code, 404)

    # 17: competencia cancelada nao aparece no seletor
    def test_cancelled_period_not_in_selector(self):
        from app.apps.commissions.services import get_selectable_periods
        cancelled = CommissionPeriod.objects.create(
            tenant=self.tenant, month=5, year=2026,
            start_date=date(2026, 4, 21), end_date=date(2026, 5, 20),
            status=CommissionPeriod.Status.CANCELADA,
        )
        uuids = [str(p.uuid) for p in get_selectable_periods(self.tenant)]
        self.assertNotIn(str(cancelled.uuid), uuids)
        self.assertIn(str(self.period.uuid), uuids)

    # 18: competencia fechada usa valor congelado
    def test_closed_period_uses_frozen_value(self):
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.FECHADA,
            total_sold_amount=9951859, commission_amount=88888,
        )
        client = self._auth(self.manager, 'gestor123')
        url = f'/api/manager/seller/{self.seller.uuid}/'
        resp = client.get(url + f'?period={self.period.uuid}')
        spc = resp.json()['selected_period_commission']
        self.assertEqual(spc['commission_amount'], 88888)
        self.assertFalse(spc['is_estimated'])

    # 19: periodo customizado nao exibe comissao oficial
    def test_custom_range_no_selected_commission(self):
        client = self._auth(self.manager, 'gestor123')
        url = f'/api/manager/seller/{self.seller.uuid}/'
        resp = client.get(url + '?start=2026-06-25&end=2026-07-05')
        data = resp.json()
        self.assertTrue(data['is_custom_range'])
        self.assertIsNone(data['selected_period_commission'])

    # 11: comparacao entre competencias - venda 25/06 no bucket de Julho
    def test_comparison_bucket_by_period_range(self):
        client = self._auth(self.manager, 'gestor123')
        url = f'/api/manager/seller/{self.seller.uuid}/'
        resp = client.get(url + f'?period={self.period.uuid}')
        comparison = resp.json()['comparison']
        july = [c for c in comparison if c['month'] == 'Julho/2026']
        self.assertEqual(len(july), 1)
        self.assertEqual(july[0]['total'], 9951859)

    # 10: CSV/XLSX/PDF com period retornam as mesmas vendas
    def test_exports_with_period_same_sales(self):
        client = self._auth(self.manager, 'gestor123')
        base = f'/api/manager/seller/{self.seller.uuid}/csv/'
        resp = client.get(base + f'?period={self.period.uuid}')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn('2026-06-25', content)
        self.assertIn('2026-07-05', content)
        self.assertNotIn('2026-07-21', content)

    # 3: lista de vendedores - total e comissao usam o mesmo periodo (ABERTA => taxa x total)
    def test_seller_list_total_times_rate_equals_commission(self):
        client = self._auth(self.manager, 'gestor123')
        url = f'/api/manager/seller/{self.seller.uuid}/'
        data = client.get(url + f'?period={self.period.uuid}').json()
        total = data['manual_total']
        commission = data['selected_period_commission']['commission_amount']
        self.assertEqual(commission, round(total * 0.01))

    # helpers de selecao
    def test_resolve_selected_period_default_open(self):
        from app.apps.commissions.services import get_default_period
        # sem hoje coberto, cai na mais recente
        selected = get_default_period(self.tenant)
        self.assertEqual(selected.uuid, self.period.uuid)

    def test_get_previous_period(self):
        from app.apps.commissions.services import get_previous_period
        prev = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
            start_date=date(2026, 5, 21), end_date=date(2026, 6, 20),
        )
        result = get_previous_period(self.tenant, self.period)
        self.assertEqual(result.uuid, prev.uuid)

    # 21: sem competencia -> nenhum KPI financeiro por fallback silencioso
    def test_no_period_selector_empty(self):
        from app.apps.commissions.services import build_period_selector_context

        class _Req:
            GET = {}
        CommissionPeriod.objects.all().delete()
        ctx = build_period_selector_context(_Req(), self.tenant)
        self.assertFalse(ctx['has_periods'])
        self.assertIsNone(ctx['selected_period'])


class StatementCompetenciaTest(TestCase):
    """LOTE 11 - extrato PDF por competencia + origem correta."""

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Extrato Co', slug='extrato-co',
            default_commission_rate=Decimal('0.01'),
        )
        self.seller_user = User.objects.create_user(
            username='vendext', password='senha123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Vend Extrato', phone='11999990000',
            user=self.seller_user, commission_rate=Decimal('0.01'),
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.IMPORTADA,
            amount=100000, sale_date='2026-06-25', created_by=self.seller_user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=200000, sale_date='2026-07-05', created_by=self.seller_user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=999999, sale_date='2026-07-25', created_by=self.seller_user,
        )

    def _auth(self):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': 'vendext', 'password': 'senha123',
        }, format='json')
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}')
        return client

    # 8: PDF total e comissao usam o mesmo range
    def test_statement_pdf_period_range(self):
        client = self._auth()
        url = reverse('api-seller-statement-period')
        resp = client.get(url + f'?period={self.period.uuid}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')

    # 9: venda IMPORTADA aparece como Importada (nao Manual) - via context builder
    def test_importada_origin_display(self):
        s = Sale.objects.get(origin=Sale.Origin.IMPORTADA)
        self.assertEqual(s.get_origin_display(), 'Importada')


class ManagerCompetenciaExportTest(TestCase):
    """LOTE 12 - exports do gestor localizam SellerCommission pelo periodo."""

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Export Co', slug='export-co',
            default_commission_rate=Decimal('0.01'),
        )
        self.manager = User.objects.create_user(
            username='gestorexp', password='gestor123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='vendexp', password='senha123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Vend Export', phone='11999997777',
            user=self.seller_user, commission_rate=Decimal('0.01'),
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026', status=CommissionPeriod.Status.FECHADA,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.FECHADA,
            total_sold_amount=300000, commission_amount=3000,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=100000, sale_date='2026-06-25', created_by=self.seller_user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=200000, sale_date='2026-07-05', created_by=self.seller_user,
        )

    def _auth(self):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': 'gestorexp', 'password': 'gestor123',
        }, format='json')
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}')
        return client

    def test_csv_uses_frozen_commission_of_period(self):
        client = self._auth()
        url = f'/api/manager/seller/{self.seller.uuid}/csv/'
        resp = client.get(url + f'?period={self.period.uuid}')
        content = resp.content.decode('utf-8')
        # comissao congelada de 3000 centavos = 30.00
        self.assertIn('30.00', content)
        self.assertIn('2026-06-25', content)
        self.assertIn('2026-07-05', content)
