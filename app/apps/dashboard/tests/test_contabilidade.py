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
