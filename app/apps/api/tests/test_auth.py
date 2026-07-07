from django.test import TestCase
from django.urls import reverse
from django.core.cache import cache
from rest_framework.test import APIClient
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller


class JWTAuthTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(company_name="Bibelo", cnpj="11111111111111")
        self.user = User.objects.create_user(
            username="vendedor", password="senha123",
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name="Vendedor", phone="111",
            user=self.user,
        )
        self.client = APIClient()

    def test_jwt_login_returns_tokens(self):
        response = self.client.post(reverse('api-login'), {
            'username': 'vendedor', 'password': 'senha123',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_jwt_login_invalid_credentials(self):
        response = self.client.post(reverse('api-login'), {
            'username': 'vendedor', 'password': 'errada',
        }, format='json')
        self.assertEqual(response.status_code, 401)

    def test_jwt_refresh_returns_new_access(self):
        login_resp = self.client.post(reverse('api-login'), {
            'username': 'vendedor', 'password': 'senha123',
        }, format='json')
        refresh = login_resp.data['refresh']

        response = self.client.post(reverse('api-refresh'), {
            'refresh': refresh,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(reverse('api-seller-sales'))
        self.assertIn(response.status_code, (401, 403))

    def test_authenticated_request_with_bearer(self):
        login_resp = self.client.post(reverse('api-login'), {
            'username': 'vendedor', 'password': 'senha123',
        }, format='json')
        token = login_resp.data['access']

        auth_client = APIClient()
        auth_client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        response = auth_client.get(reverse('api-seller-sales'))
        self.assertEqual(response.status_code, 200)

