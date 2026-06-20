from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.cache import cache
from rest_framework.test import APIClient
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale


class SellerAPITestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(company_name="Bibelo", cnpj="11111111111111")
        self.manager = User.objects.create_user(
            username="gestor", password="gestor123",
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username="vendedor", password="senha123",
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller_profile = Seller.objects.create(
            tenant=self.tenant, name="Vendedor Teste", phone="31999999999",
            user=self.seller_user,
        )

    def _auth(self, user, password):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': user.username, 'password': password,
        }, format='json')
        self.assertIn('access', resp.data, f"Login failed for {user.username}")
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}')
        return client

    def _auth_manager(self):
        return self._auth(self.manager, 'gestor123')

    def _auth_seller(self):
        return self._auth(self.seller_user, 'senha123')


class SellerCreateAPITest(SellerAPITestBase):
    def test_manager_can_create_seller(self):
        client = self._auth_manager()
        response = client.post(reverse('api-seller-list'), {
            'name': 'Maria Silva',
            'phone': '(31) 91111-2222',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['name'], 'Maria Silva')

    def test_seller_cannot_create_another_seller(self):
        client = self._auth_seller()
        response = client.post(reverse('api-seller-list'), {
            'name': 'Outro Vendedor', 'phone': '(31) 92222-3333',
        }, format='json')
        self.assertEqual(response.status_code, 403)

    def test_manager_can_list_sellers(self):
        client = self._auth_manager()
        response = client.get(reverse('api-seller-list'))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data), 1)

    def test_seller_cannot_list_sellers(self):
        client = self._auth_seller()
        response = client.get(reverse('api-seller-list'))
        self.assertEqual(response.status_code, 403)


class SaleAPITest(SellerAPITestBase):
    def test_seller_can_create_own_sale(self):
        client = self._auth_seller()
        response = client.post(reverse('api-sale-list'), {
            'origin': 'MANUAL', 'amount': 15000, 'sale_date': '2026-06-20',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['amount'], 15000)

    def test_sale_amount_is_integer(self):
        client = self._auth_seller()
        response = client.post(reverse('api-sale-list'), {
            'origin': 'MANUAL', 'amount': 15000, 'sale_date': '2026-06-20',
        }, format='json')
        self.assertIsInstance(response.data['amount'], int)

    def test_seller_can_view_own_sales(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller_profile,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date='2026-06-01', created_by=self.seller_user,
        )
        client = self._auth_seller()
        response = client.get(reverse('api-seller-sales'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_manager_can_view_all_sales(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller_profile,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date='2026-06-01', created_by=self.manager,
        )
        client = self._auth_manager()
        response = client.get(reverse('api-manager-sales'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
