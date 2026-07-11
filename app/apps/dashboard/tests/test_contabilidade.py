from datetime import date

from django.test import TestCase, Client
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale
from app.apps.commissions.models import CommissionPeriod, SellerCommission


class GestorContabilidadeTest(TestCase):
    def setUp(self):
        self.client = Client()

        self.tenant = Tenant.objects.create(
            company_name='Loja Contabilidade',
            slug='loja-contabilidade',
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username='admin@loja.com',
            email='admin@loja.com',
            password='Senha@12345678',
            role=User.Role.ADMIN,
            tenant=self.tenant,
        )

        self.seller_user = User.objects.create_user(
            username='vendedor@loja.com',
            password='Senha@12345678',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )

        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Vendedor 1',
            phone='11999999999',
            is_active=True,
        )

        hoje = timezone.localdate()
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            year=hoje.year,
            month=hoje.month,
        )

        self.sale = Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            amount=10000,
            status='ATIVA',
            sale_date=hoje,
        )

        self.sc = SellerCommission.objects.create(
            period=self.period,
            seller=self.seller,
            status=SellerCommission.Status.ABERTA,
        )

        self.url = '/dashboard/gestor/contabilidade/'

    def test_gestor_contabilidade_returns_200(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Contabilidade')


class ContabilidadeCompetenciaRangeTest(TestCase):
    """PROMPT_42 LOTE 13 - contabilidade usa ranges reais, nao mes-calendario."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Loja Range', slug='loja-range', is_active=True,
        )
        self.admin = User.objects.create_user(
            username='adminrange', password='Senha@12345678',
            role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='vendrange', password='Senha@12345678',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Vend Range',
            phone='11999998888', is_active=True,
        )
        # Competencia 21/06 a 20/07
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        # venda 25/06 (dentro do range) e 21/07 (fora)
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=500000, status='ATIVA', sale_date='2026-06-25',
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=999999, status='ATIVA', sale_date='2026-07-21',
        )

    def test_contabilidade_totals_use_period_range(self):
        self.client.force_login(self.admin)
        resp = self.client.get('/dashboard/gestor/contabilidade/')
        self.assertEqual(resp.status_code, 200)
        comps = {c['label']: c for c in resp.context['competencias']}
        self.assertIn('Julho/2026', comps)
        # so a venda de 25/06 (dentro do range) entra; 21/07 fica de fora
        self.assertEqual(comps['Julho/2026']['total_sold'], 500000)
        self.assertEqual(comps['Julho/2026']['range'], '21/06/2026 a 20/07/2026')
