from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.db.models import Sum
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale
from app.apps.orders.models import Order
from app.apps.commissions.models import CommissionPeriod, SellerCommission


class MultiTenantIsolationTest(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(
            company_name='Loja A', cnpj='11111111111111',
        )
        self.tenant_b = Tenant.objects.create(
            company_name='Loja B', cnpj='22222222222222',
        )

        self.user_a = User.objects.create_user(
            username='admin_a', password='pass',
            role=User.Role.ADMIN, tenant=self.tenant_a,
        )
        self.seller_a = Seller.objects.create(
            tenant=self.tenant_a, name='Vendedor A', phone='1111-1111',
            user=self.user_a,
        )

        self.user_b = User.objects.create_user(
            username='admin_b', password='pass',
            role=User.Role.ADMIN, tenant=self.tenant_b,
        )
        self.seller_b = Seller.objects.create(
            tenant=self.tenant_b, name='Vendedor B', phone='2222-2222',
            user=self.user_b,
        )

        self.order_a = Order.objects.create(
            tenant=self.tenant_a,
            seller=self.seller_a,
            customer_name='Cliente A',
            total_amount=10000,
        )

    def test_seller_cannot_be_in_sale_of_other_tenant(self):
        sale = Sale(
            tenant=self.tenant_a,
            seller=self.seller_b,
            order=self.order_a,
            origin=Sale.Origin.MANUAL,
            amount=5000,
            sale_date='2026-06-01',
            created_by=self.user_a,
        )
        with self.assertRaises(ValidationError):
            sale.full_clean()

    def test_seller_sales_all_no_recursion(self):
        Sale.objects.create(
            tenant=self.tenant_a,
            seller=self.seller_a,
            order=self.order_a,
            origin=Sale.Origin.MANUAL,
            amount=5000,
            sale_date='2026-06-01',
            created_by=self.user_a,
        )
        Sale.objects.create(
            tenant=self.tenant_a,
            seller=self.seller_a,
            origin=Sale.Origin.MANUAL,
            amount=3000,
            sale_date='2026-06-02',
            created_by=self.user_a,
        )
        count = self.seller_a.sales.count()
        self.assertEqual(count, 2)


class DataMigrationTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import importlib
        cls.migration = importlib.import_module(
            'app.apps.sellers.migrations.0003_link_sellers_to_users',
        )

    def test_generate_unique_usernames_no_collision(self):
        generate_unique_username = self.migration.generate_unique_username

        existing = set()
        self.assertEqual(
            generate_unique_username('Maria', existing), 'maria',
        )
        self.assertIn('maria', existing)

        self.assertEqual(
            generate_unique_username('Maria', existing), 'maria-2',
        )
        self.assertIn('maria-2', existing)

        self.assertEqual(
            generate_unique_username('Leo', existing), 'leo',
        )
        self.assertIn('leo', existing)

        self.assertEqual(
            generate_unique_username('Leonardo Bruno', existing),
            'leonardo-bruno',
        )
        self.assertIn('leonardo-bruno', existing)

    def test_link_sellers_to_users_creates_unique_usernames(self):
        link_sellers_to_users = self.migration.link_sellers_to_users

        tenant = Tenant.objects.create(
            company_name='Bibelo', cnpj='33333333333333',
        )
        user_maria = User.objects.create_user(
            username='_temp_maria', password='pass',
            role=User.Role.SELLER, tenant=tenant,
        )
        user_jose = User.objects.create_user(
            username='_temp_jose', password='pass',
            role=User.Role.SELLER, tenant=tenant,
        )
        seller1 = Seller.objects.create(
            tenant=tenant, name='Maria', phone='1111', user=user_maria,
        )
        seller2 = Seller.objects.create(
            tenant=tenant, name='Jose', phone='2222', user=user_jose,
        )

        self.assertIsNotNone(seller1.user)
        self.assertIsNotNone(seller2.user)

        from django.apps import apps as django_apps
        link_sellers_to_users(django_apps, None)

        seller1.refresh_from_db()
        seller2.refresh_from_db()

        self.assertIsNotNone(seller1.user)
        self.assertIsNotNone(seller2.user)
        self.assertNotEqual(seller1.user.username, seller2.user.username)


class SellerCommissionRecalculateTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Bibelo', cnpj='44444444444444',
        )
        self.user = User.objects.create_user(
            username='vendedor_teste', password='pass',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Vendedor Teste', phone='3333',
            user=self.user, commission_rate=0.05,
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=0.05,
        )

    def test_recalculate_sums_manual_sales_only(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date='2026-06-10', created_by=self.user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=20000,
            sale_date='2026-06-15', created_by=self.user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=5000,
            sale_date='2026-07-01', created_by=self.user,
        )

        self.sc.recalculate()

        self.assertEqual(self.sc.total_sold_amount, 30000)
        self.assertEqual(self.sc.commission_amount, int(30000 * 0.05))

    def test_recalculate_empty_period(self):
        self.sc.recalculate()
        self.assertEqual(self.sc.total_sold_amount, 0)
        self.assertEqual(self.sc.commission_amount, 0)


class SellerGoalTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Goal Test', slug='goal-test',
            is_active=True, ranking_visible_to_sellers=True,
        )
        self.user = User.objects.create_user(
            username='seller_goal', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Goal Seller', phone='55999999999',
            user=self.user, is_active=True,
        )

    def test_goal_progress_percent(self):
        from app.apps.sellers.models import SellerGoal
        goal = SellerGoal.objects.create(
            seller=self.seller, month=7, year=2026,
            target_amount=100000, created_by=self.user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=25000,
            sale_date='2026-07-15', status='ATIVA',
            created_by=self.user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=25000,
            sale_date='2026-07-16', status='ATIVA',
            created_by=self.user,
        )
        self.assertEqual(goal.progress_percent, 50)

    def test_goal_excludes_estornada_sales(self):
        from app.apps.sellers.models import SellerGoal
        goal = SellerGoal.objects.create(
            seller=self.seller, month=7, year=2026,
            target_amount=100000, created_by=self.user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=50000,
            sale_date='2026-07-15', status='ESTORNADA',
            created_by=self.user,
        )
        self.assertEqual(goal.progress_percent, 0)

    def test_goal_ranking_respects_flag(self):
        from app.apps.sellers.models import SellerGoal
        self.tenant.ranking_visible_to_sellers = False
        self.tenant.save()
        goal = SellerGoal.objects.create(
            seller=self.seller, month=7, year=2026,
            target_amount=100000, created_by=self.user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=50000,
            sale_date='2026-07-15', status='ATIVA',
            created_by=self.user,
        )
        self.assertEqual(goal.progress_percent, 50)
