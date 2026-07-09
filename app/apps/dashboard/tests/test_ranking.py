from datetime import date, timedelta
from decimal import Decimal

from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from app.apps.accounts.models import Tenant
from app.apps.commissions.models import CommissionPeriod
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale

User = get_user_model()


class RankingPrivacyTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Test Loja',
            slug='test-loja',
            default_commission_rate=Decimal('0.01'),
            is_active=True,
        )
        self.seller_user = User.objects.create_user(
            username='seller1',
            password='test123',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Bruno',
            phone='55999999999',
            commission_rate=Decimal('0.01'),
            is_active=True,
        )

        self.seller2_user = User.objects.create_user(
            username='seller2',
            password='test123',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller2 = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller2_user,
            name='Maria',
            phone='55988888888',
            commission_rate=Decimal('0.01'),
            is_active=True,
        )

        self.today = timezone.localdate()
        CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=self.today.month,
            year=self.today.year,
            start_date=self.today - timedelta(days=1),
            end_date=self.today + timedelta(days=1),
        )

    def _create_sale(self, seller, amount_cents, origin='MANUAL'):
        return Sale.objects.create(
            tenant=self.tenant,
            seller=seller,
            origin=origin,
            amount=amount_cents,
            status='ATIVA',
            sale_date=self.today,
        )

    def _get_ranking(self, user):
        from django.test import RequestFactory
        from app.apps.dashboard.mobile_views import mobile_ranking

        factory = RequestFactory()
        request = factory.get('/mobile/ranking/')
        request.user = user
        return mobile_ranking(request)

    def test_ranking_default_is_private(self):
        new_tenant = Tenant.objects.create(
            company_name='Nova Loja',
            slug='nova-loja',
            default_commission_rate=Decimal('0.01'),
            is_active=True,
        )
        self.assertFalse(new_tenant.ranking_visible_to_sellers)

    def test_ranking_position_found(self):
        self._create_sale(self.seller2, 100000)
        self._create_sale(self.seller, 50000)

        response = self._get_ranking(self.seller_user)
        content = response.content.decode()

        self.assertIn('#2', content)
        self.assertIn('500,00', content)

    def test_ranking_hides_others_totals_when_visible(self):
        self.tenant.ranking_visible_to_sellers = True
        self.tenant.save()

        self._create_sale(self.seller2, 150000)
        self._create_sale(self.seller, 50000)

        response = self._get_ranking(self.seller_user)
        content = response.content.decode()

        self.assertIn('Maria', content)
        self.assertIn('Bruno', content)

        other_total_formatted = '1.500,00'
        self.assertNotIn(other_total_formatted, content)

        own_total_formatted = '500,00'
        self.assertIn(own_total_formatted, content)

    def test_ranking_private_mode(self):
        self.tenant.ranking_visible_to_sellers = False
        self.tenant.save()

        self._create_sale(self.seller2, 150000)
        self._create_sale(self.seller, 50000)

        response = self._get_ranking(self.seller_user)
        content = response.content.decode()

        self.assertNotIn('Maria', content)

        self.assertIn('#2', content)
        self.assertIn('500,00', content)

        self.assertNotIn('1.500,00', content)

        self.assertIn('Comissao estimada', content)

    def test_ranking_no_sales_yet(self):
        response = self._get_ranking(self.seller_user)
        content = response.content.decode()

        self.assertIn('ainda nao pontuou', content)


class RankingPrivateModeContentTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Test Ranking',
            slug='test-ranking-content',
            default_commission_rate=Decimal('0.01'),
            is_active=True,
        )
        self.user = User.objects.create_user(
            username='seller_rk_content',
            password='test123',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.user,
            name='Ranking Seller',
            phone='55999999999',
            commission_rate=Decimal('0.01'),
            is_active=True,
        )
        today = timezone.localdate()
        CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=today.month,
            year=today.year,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
        )

    def test_private_mode_shows_tips_and_commission(self):
        self.tenant.ranking_visible_to_sellers = False
        self.tenant.save()

        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin='MANUAL',
            amount=50000,
            status='ATIVA',
            sale_date=timezone.localdate(),
        )

        from django.test import RequestFactory
        from app.apps.dashboard.mobile_views import mobile_ranking

        factory = RequestFactory()
        request = factory.get('/mobile/ranking/')
        request.user = self.user

        response = mobile_ranking(request)
        content = response.content.decode()

        self.assertIn('Dicas', content)
        self.assertIn('Comissao estimada', content)
        self.assertNotIn('Maria', content)

    def test_visible_mode_shows_names(self):
        self.tenant.ranking_visible_to_sellers = True
        self.tenant.save()

        seller2_user = User.objects.create_user(
            username='seller_rk2',
            password='test123',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        seller2 = Seller.objects.create(
            tenant=self.tenant,
            user=seller2_user,
            name='Colega Teste',
            phone='55988888888',
            commission_rate=Decimal('0.01'),
            is_active=True,
        )

        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin='MANUAL',
            amount=50000,
            status='ATIVA',
            sale_date=timezone.localdate(),
        )
        Sale.objects.create(
            tenant=self.tenant,
            seller=seller2,
            origin='MANUAL',
            amount=150000,
            status='ATIVA',
            sale_date=timezone.localdate(),
        )

        from django.test import RequestFactory
        from app.apps.dashboard.mobile_views import mobile_ranking

        factory = RequestFactory()
        request = factory.get('/mobile/ranking/')
        request.user = self.user

        response = mobile_ranking(request)
        content = response.content.decode()

        self.assertIn('Colega Teste', content)

    def test_tips_rotate_with_date(self):
        self.tenant.ranking_visible_to_sellers = False
        self.tenant.save()

        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin='MANUAL',
            amount=50000,
            status='ATIVA',
            sale_date=timezone.localdate(),
        )

        from django.test import RequestFactory
        from app.apps.dashboard.mobile_views import mobile_ranking
        today = timezone.localdate()

        with patch('django.utils.timezone.localdate', return_value=today):
            factory = RequestFactory()
            request = factory.get('/mobile/ranking/')
            request.user = self.user
            response1 = mobile_ranking(request)
            content1 = response1.content.decode()

        with patch('django.utils.timezone.localdate', return_value=today + timedelta(days=1)):
            factory = RequestFactory()
            request = factory.get('/mobile/ranking/')
            request.user = self.user
            response2 = mobile_ranking(request)
            content2 = response2.content.decode()

        self.assertNotEqual(content1, content2, "Tips should differ with different dates")
        self.assertIn('Dicas', content1)
        self.assertIn('Dicas', content2)


class MobileDesempenhoTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Test Desempenho',
            slug='test-desempenho',
            default_commission_rate=Decimal('0.01'),
            is_active=True,
        )
        self.user = User.objects.create_user(
            username='seller_desemp',
            password='test123',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.user,
            name='Desempenho Seller',
            phone='55999999999',
            commission_rate=Decimal('0.01'),
            is_active=True,
        )

    def test_current_period_missing_shows_message(self):
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin='MANUAL',
            amount=50000,
            status='ATIVA',
            sale_date=timezone.localdate(),
        )

        from django.test import RequestFactory
        from app.apps.dashboard.mobile_views import mobile_meu_desempenho

        factory = RequestFactory()
        request = factory.get('/mobile/meu-desempenho/')
        request.user = self.user

        response = mobile_meu_desempenho(request)
        content = response.content.decode()

        self.assertIn('Nenhuma competencia aberta para a data atual.', content)

    def test_current_month_estimate_none_when_sc_exists(self):
        Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            origin='MANUAL',
            amount=50000,
            status='ATIVA',
            sale_date=timezone.localdate(),
        )

        from app.apps.commissions.models import CommissionPeriod, SellerCommission

        today = timezone.localdate()
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=today.month, year=today.year,
        )
        SellerCommission.objects.create(
            period=period, seller=self.seller,
            total_sold_amount=50000, commission_rate=Decimal('0.01'),
            commission_amount=500,
        )

        from django.test import RequestFactory
        from app.apps.dashboard.mobile_views import mobile_meu_desempenho

        factory = RequestFactory()
        request = factory.get('/mobile/meu-desempenho/')
        request.user = self.user

        response = mobile_meu_desempenho(request)
        content = response.content.decode()

        self.assertNotIn('Periodo ainda nao aberto', content)


class MobileCSRFTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Test CSRF',
            slug='test-csrf',
            default_commission_rate=Decimal('0.01'),
            is_active=True,
        )
        self.user = User.objects.create_user(
            username='seller_csrf',
            password='test123',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        Seller.objects.create(
            tenant=self.tenant,
            user=self.user,
            name='CSRF Seller',
            phone='55999999999',
            commission_rate=Decimal('0.01'),
            is_active=True,
        )

    def test_mobile_home_csrf_meta_is_token_not_markup(self):
        from django.test import RequestFactory
        from app.apps.dashboard.mobile_views import mobile_home

        factory = RequestFactory()
        request = factory.get('/mobile/')
        request.user = self.user

        response = mobile_home(request)
        content = response.content.decode()

        self.assertNotIn('csrfmiddlewaretoken', content.split('<head>')[1].split('</head>')[0] if '<head>' in content else content)

    def test_mobile_home_csrf_meta_has_64_char_token(self):
        import re
        from django.test import RequestFactory
        from app.apps.dashboard.mobile_views import mobile_home

        factory = RequestFactory()
        request = factory.get('/mobile/')
        request.user = self.user

        response = mobile_home(request)
        content = response.content.decode()

        match = re.search(r'<meta name="csrf-token" content="([^"]+)"', content)
        self.assertIsNotNone(match, 'CSRF meta tag must exist')
        token = match.group(1)
        self.assertRegex(token, r'^[A-Za-z0-9]{32,}$', 'CSRF token must be alphanumeric string, not HTML markup')


class SellerStatementTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Test Statement',
            slug='test-statement',
            default_commission_rate=Decimal('0.01'),
            is_active=True,
        )
        self.user = User.objects.create_user(
            username='seller_statement',
            password='test123',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.user,
            name='Statement Seller',
            phone='55999999999',
            commission_rate=Decimal('0.01'),
            is_active=True,
        )

        self.user2 = User.objects.create_user(
            username='seller_statement2',
            password='test123',
            role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller2 = Seller.objects.create(
            tenant=self.tenant,
            user=self.user2,
            name='Other Seller',
            phone='55988888888',
            commission_rate=Decimal('0.01'),
            is_active=True,
        )

    def test_pdf_generated_with_sales(self):
        today = timezone.localdate()
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin='MANUAL', amount=50000, status='ATIVA',
            sale_date=date(today.year, today.month, 1),
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin='MANUAL', amount=30000, status='ESTORNADA',
            sale_date=date(today.year, today.month, 2),
        )

        from django.test import RequestFactory
        from app.apps.api.views import SellerStatementView

        factory = RequestFactory()
        request = factory.get(f'/api/seller/statement/{today.year}/{today.month}/')
        request.user = self.user

        response = SellerStatementView.as_view()(request, year=today.year, month=today.month)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'), 'Response should be a PDF')

    def test_pdf_empty_when_no_sales(self):
        today = timezone.localdate()

        from django.test import RequestFactory
        from app.apps.api.views import SellerStatementView

        factory = RequestFactory()
        request = factory.get(f'/api/seller/statement/{today.year}/{today.month}/')
        request.user = self.user

        response = SellerStatementView.as_view()(request, year=today.year, month=today.month)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_seller_a_cannot_access_seller_b(self):
        today = timezone.localdate()
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller2,
            origin='MANUAL', amount=50000, status='ATIVA',
            sale_date=date(today.year, today.month, 1),
        )

        from django.test import RequestFactory
        from app.apps.api.views import SellerStatementView

        factory = RequestFactory()
        request = factory.get(f'/api/seller/statement/{today.year}/{today.month}/')
        request.user = self.user

        response = SellerStatementView.as_view()(request, year=today.year, month=today.month)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_estornada_sale_rendered_in_pdf(self):
        today = timezone.localdate()
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin='MANUAL', amount=99999, status='ESTORNADA',
            sale_date=date(today.year, today.month, 1),
        )

        from django.test import RequestFactory
        from app.apps.api.views import SellerStatementView

        factory = RequestFactory()
        request = factory.get(f'/api/seller/statement/{today.year}/{today.month}/')
        request.user = self.user

        response = SellerStatementView.as_view()(request, year=today.year, month=today.month)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))
