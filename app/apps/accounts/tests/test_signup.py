from django.test import TestCase, Client
from django.urls import reverse
from django.core.cache import cache
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale


VALID_CNPJ = '11222333000181'


class TenantRegistrationFormTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse('accounts:signup')

    def _post(self, **overrides):
        data = {
            'company_name': 'Loja Teste',
            'cnpj': VALID_CNPJ,
            'responsible_name': 'Fulano da Silva',
            'email': 'fulano@teste.com.br',
            'password': 'Senha@12345678',
            'password_confirm': 'Senha@12345678',
            'plan': 'ESSENCIAL',
            'billing_cycle': 'MONTHLY',
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_signup_page_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Criar conta')

    def test_successful_registration_creates_tenant_and_user(self):
        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('dashboard:gestor_home'))

        tenant = Tenant.objects.get(company_name='Loja Teste')
        self.assertEqual(tenant.cnpj, VALID_CNPJ)
        self.assertTrue(tenant.is_active)

        user = User.objects.get(email='fulano@teste.com.br')
        self.assertEqual(user.username, 'fulano@teste.com.br')
        self.assertEqual(user.first_name, 'Fulano da Silva')
        self.assertEqual(user.role, User.Role.ADMIN)
        self.assertEqual(user.tenant, tenant)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)

    def test_registered_user_is_not_superuser(self):
        self._post()
        user = User.objects.get(email='fulano@teste.com.br')
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertEqual(user.role, User.Role.ADMIN)

    def test_cnpj_already_registered(self):
        Tenant.objects.create(company_name='Existente', cnpj=VALID_CNPJ)
        response = self._post()
        self.assertContains(response, 'CNPJ')
        self.assertEqual(Tenant.objects.filter(cnpj=VALID_CNPJ).count(), 1)

    def test_email_already_registered(self):
        existing_tenant = Tenant.objects.create(company_name='Outra Loja', cnpj='99999999000199')
        User.objects.create_user(
            username='fulano@teste.com.br',
            email='fulano@teste.com.br',
            password='Senha@12345678',
            role=User.Role.ADMIN,
            tenant=existing_tenant,
        )
        response = self._post()
        self.assertContains(response, 'e-mail')

    def test_invalid_cnpj_wrong_check_digit(self):
        response = self._post(cnpj='11222333000182')
        self.assertContains(response, 'CNPJ')

    def test_invalid_cnpj_short(self):
        response = self._post(cnpj='123')
        self.assertContains(response, 'CNPJ')

    def test_invalid_cnpj_all_same_digits(self):
        response = self._post(cnpj='11111111111111')
        self.assertContains(response, 'CNPJ')

    def test_password_mismatch(self):
        response = self._post(password='Senha@12345678', password_confirm='Diferente@123')
        self.assertContains(response, 'senhas')

    def test_weak_password(self):
        response = self._post(password='123', password_confirm='123')
        self.assertContains(response, 'senha')

    def test_weak_password_similar_to_email(self):
        response = self._post(password='fulano@teste.com.br', password_confirm='fulano@teste.com.br')
        self.assertContains(response, 'senha')

    def test_honeypot_rejects_bot(self):
        response = self._post(website='http://spam-site.com')
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(Tenant.objects.filter(company_name='Loja Teste').count(), 0)

    def test_missing_company_name(self):
        response = self._post(company_name='')
        self.assertContains(response, 'empresa')
        self.assertEqual(Tenant.objects.filter(cnpj=VALID_CNPJ).count(), 0)

    def test_missing_email(self):
        response = self._post(email='')
        self.assertContains(response, 'E-mail')
        self.assertEqual(Tenant.objects.filter(cnpj=VALID_CNPJ).count(), 0)

    def test_authenticated_user_redirected(self):
        tenant = Tenant.objects.create(company_name='Loja Logada')
        user = User.objects.create_user(
            username='logado@teste.com',
            email='logado@teste.com',
            password='Senha@12345678',
            role=User.Role.ADMIN,
            tenant=tenant,
        )
        self.client.force_login(user)
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, reverse('dashboard:gestor_home'))


class TenantIsolationAfterSignupTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

        self.tenant_a = Tenant.objects.create(company_name='Loja A', cnpj='11111111111111')
        self.admin_a = User.objects.create_user(
            username='admin_a@loja.com',
            email='admin_a@loja.com',
            password='Senha@12345678',
            role=User.Role.ADMIN,
            tenant=self.tenant_a,
        )
        self.seller_a = User.objects.create_user(
            username='vendedor_a', password='Senha@12345678',
            role=User.Role.SELLER, tenant=self.tenant_a,
        )
        self.seller_a_profile = Seller.objects.create(
            tenant=self.tenant_a, name='Vendedor A', phone='111', user=self.seller_a,
        )
        Sale.objects.create(
            tenant=self.tenant_a, seller=self.seller_a_profile,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date='2026-06-01', created_by=self.seller_a,
        )

        self.client.force_login(self.admin_a)

    def test_admin_a_cannot_see_tenant_b_data_after_b_registers(self):
        signup_url = reverse('accounts:signup')
        login = self.client
        login.logout()

        response_b = login.post(signup_url, {
            'company_name': 'Loja B',
            'cnpj': VALID_CNPJ,
            'responsible_name': 'Admin B',
            'email': 'admin_b@loja.com',
            'password': 'Senha@12345678',
            'password_confirm': 'Senha@12345678',
            'plan': 'ESSENCIAL',
            'billing_cycle': 'MONTHLY',
        })
        self.assertEqual(response_b.status_code, 302)

        tenant_b = Tenant.objects.get(company_name='Loja B')
        admin_b = User.objects.get(email='admin_b@loja.com')

        seller_b = User.objects.create_user(
            username='vendedor_b', password='Senha@12345678',
            role=User.Role.SELLER, tenant=tenant_b,
        )
        seller_b_profile = Seller.objects.create(
            tenant=tenant_b, name='Vendedor B', phone='222', user=seller_b,
        )
        Sale.objects.create(
            tenant=tenant_b, seller=seller_b_profile,
            origin=Sale.Origin.MANUAL, amount=20000,
            sale_date='2026-06-01', created_by=seller_b,
        )

        self.client.force_login(self.admin_a)
        response = self.client.get(reverse('dashboard:gestor_home'))
        self.assertEqual(response.status_code, 200)

        tenant_a_sellers = Seller.objects.filter(tenant=self.tenant_a)
        tenant_a_names = set(tenant_a_sellers.values_list('name', flat=True))
        self.assertIn('Vendedor A', tenant_a_names)
        self.assertNotIn('Vendedor B', tenant_a_names)

        tenant_b_sales = Sale.objects.filter(tenant=tenant_b)
        self.assertEqual(tenant_b_sales.count(), 1)
        self.assertEqual(tenant_b_sales.first().seller.tenant, tenant_b)

        self.assertEqual(self.admin_a.tenant, self.tenant_a)
        self.assertEqual(admin_b.tenant, tenant_b)
        self.assertNotEqual(self.admin_a.tenant, admin_b.tenant)
