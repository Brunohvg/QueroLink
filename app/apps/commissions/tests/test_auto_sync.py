from datetime import date

from django.test import TestCase

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.commissions.services import ensure_seller_commission, sync_period_seller_commissions
from app.apps.sales.models import Sale


class EnsureSellerCommissionTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Test', slug='test-sync', is_active=True)
        self.user = User.objects.create_user(username='mgr', password='p', role=User.Role.MANAGER, tenant=self.tenant)
        self.seller = Seller.objects.create(tenant=self.tenant, user=self.user, name='Vendedor A')

    def test_periodo_aberto_cria_sc(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026, status=CommissionPeriod.Status.ABERTA, expected_working_days=22,
        )
        sale_date = date(2026, 7, 4)
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, amount=50000, sale_date=sale_date,
            origin=Sale.Origin.MANUAL, status='ATIVA',
        )
        sc = ensure_seller_commission(self.seller, sale_date)
        self.assertIsNotNone(sc)
        self.assertEqual(SellerCommission.objects.filter(period=period, seller=self.seller).count(), 1)

    def test_periodo_fechada_nao_cria_sc(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026, status=CommissionPeriod.Status.FECHADA, expected_working_days=22,
        )
        sale_date = date(2026, 7, 4)
        sc = ensure_seller_commission(self.seller, sale_date)
        self.assertIsNone(sc)
        self.assertEqual(SellerCommission.objects.filter(period=period, seller=self.seller).count(), 0)

    def test_retrieve_nao_duplica(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026, status=CommissionPeriod.Status.ABERTA, expected_working_days=22,
        )
        for _ in range(3):
            sync_period_seller_commissions(period)
        self.assertEqual(SellerCommission.objects.filter(period=period, seller=self.seller).count(), 1)

    def test_list_sync_cria_sc_para_seller_novo(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026, status=CommissionPeriod.Status.ABERTA, expected_working_days=22,
        )
        seller_novo = Seller.objects.create(
            tenant=self.tenant, user=User.objects.create_user(username='s2', password='p', role=User.Role.SELLER, tenant=self.tenant),
            name='Vendedor B',
        )
        sync_period_seller_commissions(period)
        self.assertTrue(SellerCommission.objects.filter(period=period, seller=seller_novo).exists())

    def test_list_sync_nao_duplica(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026, status=CommissionPeriod.Status.ABERTA, expected_working_days=22,
        )
        for _ in range(3):
            sync_period_seller_commissions(period)
        self.assertEqual(SellerCommission.objects.filter(period=period).count(), 1)

    def test_list_sync_ignora_fechada(self):
        period_aberta = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026, status=CommissionPeriod.Status.ABERTA, expected_working_days=22,
        )
        period_fechada = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026, status=CommissionPeriod.Status.FECHADA, expected_working_days=22,
        )
        seller_novo = Seller.objects.create(
            tenant=self.tenant, user=User.objects.create_user(username='s3', password='p', role=User.Role.SELLER, tenant=self.tenant),
            name='Vendedor C',
        )
        sync_period_seller_commissions(period_aberta)
        self.assertTrue(SellerCommission.objects.filter(period=period_aberta, seller=seller_novo).exists())
        self.assertFalse(SellerCommission.objects.filter(period=period_fechada, seller=seller_novo).exists())
