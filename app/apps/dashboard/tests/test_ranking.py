from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from app.apps.accounts.models import Tenant
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

        other_total_formatted = '1.500,00'
        self.assertNotIn(other_total_formatted, content)

        self.assertIn('ranking detalhado esta desativado', content)

    def test_ranking_no_sales_yet(self):
        response = self._get_ranking(self.seller_user)
        content = response.content.decode()

        self.assertIn('ainda nao pontuou', content)
