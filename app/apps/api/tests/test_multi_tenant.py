from django.test import TestCase
from django.urls import reverse
from django.core.cache import cache
from rest_framework.test import APIClient
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale


class MultiTenantAPITest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant_a = Tenant.objects.create(company_name="Loja A", cnpj="11111111111111")
        self.tenant_b = Tenant.objects.create(company_name="Loja B", cnpj="22222222222222")

        self.manager_a = User.objects.create_user(
            username="gestor_a", password="pass123",
            role=User.Role.MANAGER, tenant=self.tenant_a,
        )
        self.seller_a = User.objects.create_user(
            username="vendedor_a", password="pass123",
            role=User.Role.SELLER, tenant=self.tenant_a,
        )
        self.seller_a_profile = Seller.objects.create(
            tenant=self.tenant_a, name="Vendedor A", phone="111",
            user=self.seller_a,
        )

        self.manager_b = User.objects.create_user(
            username="gestor_b", password="pass123",
            role=User.Role.MANAGER, tenant=self.tenant_b,
        )
        self.seller_b = User.objects.create_user(
            username="vendedor_b", password="pass123",
            role=User.Role.SELLER, tenant=self.tenant_b,
        )
        self.seller_b_profile = Seller.objects.create(
            tenant=self.tenant_b, name="Vendedor B", phone="222",
            user=self.seller_b,
        )

        self.sale_a = Sale.objects.create(
            tenant=self.tenant_a, seller=self.seller_a_profile,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date='2026-06-01', created_by=self.seller_a,
        )
        self.sale_b = Sale.objects.create(
            tenant=self.tenant_b, seller=self.seller_b_profile,
            origin=Sale.Origin.MANUAL, amount=20000,
            sale_date='2026-06-01', created_by=self.seller_b,
        )

    def _auth(self, user):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': user.username, 'password': 'pass123',
        }, format='json')
        self.assertIn('access', resp.data, f"Login failed for {user.username}")
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}')
        return client

    def test_manager_a_cannot_see_tenant_b_sellers(self):
        client = self._auth(self.manager_a)
        response = client.get(reverse('api-seller-list'))
        names = [s['name'] for s in response.data]
        self.assertIn('Vendedor A', names)
        self.assertNotIn('Vendedor B', names)

    def test_manager_a_cannot_see_tenant_b_sales(self):
        client = self._auth(self.manager_a)
        response = client.get(reverse('api-manager-sales'))
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['seller_name'], 'Vendedor A')

    def test_seller_a_cannot_see_tenant_b_sales(self):
        client = self._auth(self.seller_a)
        response = client.get(reverse('api-seller-sales'))
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['seller_name'], 'Vendedor A')

    def test_cannot_create_sale_for_other_tenant_seller(self):
        client = self._auth(self.manager_a)
        response = client.post(reverse('api-sale-list'), {
            'seller': str(self.seller_b_profile.uuid),
            'origin': 'MANUAL', 'amount': 5000, 'sale_date': '2026-06-20',
        }, format='json')
        self.assertGreaterEqual(response.status_code, 400)

    def test_seller_cannot_view_sales_of_another_seller(self):
        seller_a2 = User.objects.create_user(
            username="vendedor_a2", password="pass123",
            role=User.Role.SELLER, tenant=self.tenant_a,
        )
        seller_a2_profile = Seller.objects.create(
            tenant=self.tenant_a, name="Vendedor A2", phone="333",
            user=seller_a2,
        )
        Sale.objects.create(
            tenant=self.tenant_a, seller=seller_a2_profile,
            origin=Sale.Origin.MANUAL, amount=5000,
            sale_date='2026-06-20', created_by=seller_a2,
        )

        client = self._auth(self.seller_a)
        response = client.get(reverse('api-seller-sales'))
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['seller_name'], 'Vendedor A')
