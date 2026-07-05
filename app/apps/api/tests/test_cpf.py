from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.sellers.validators import normalize_and_validate_cpf


VALID_CPF_DIGITS = '52998224725'
VALID_CPF_FORMATTED = '529.982.247-25'


class CpfValidatorTest(TestCase):
    def test_valid_cpf_formatted(self):
        self.assertEqual(normalize_and_validate_cpf(VALID_CPF_FORMATTED), VALID_CPF_DIGITS)

    def test_valid_cpf_digits(self):
        self.assertEqual(normalize_and_validate_cpf(VALID_CPF_DIGITS), VALID_CPF_DIGITS)

    def test_invalid_cpf_all_same(self):
        with self.assertRaises(ValueError):
            normalize_and_validate_cpf('111.111.111-11')

    def test_invalid_cpf_short(self):
        with self.assertRaises(ValueError):
            normalize_and_validate_cpf('123')

    def test_invalid_cpf_wrong_dv(self):
        with self.assertRaises(ValueError):
            normalize_and_validate_cpf('529.982.247-26')

    def test_empty_cpf(self):
        self.assertIsNone(normalize_and_validate_cpf(''))
        self.assertIsNone(normalize_and_validate_cpf(None))


class CpfApiTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='CPF API Test', slug='cpf-api-test', is_active=True,
        )
        self.admin = User.objects.create_user(
            username='admin@cpf.com', email='admin@cpf.com',
            password='Senha@12345678', role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.client.force_login(self.admin)

        self.create_url = reverse('api-seller-list')

    def test_create_seller_cpf_formatted_normalizes(self):
        response = self.client.post(self.create_url, {
            'name': 'Novo CPF', 'phone': '11977777777', 'cpf': VALID_CPF_FORMATTED,
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201, f'Response: {response.status_code} {response.data}')
        seller = Seller.objects.get(name='Novo CPF')
        self.assertEqual(seller.cpf, VALID_CPF_DIGITS)

    def test_create_seller_duplicate_cpf_400(self):
        u1 = User.objects.create_user(username='u1@cpf.com', password='p', role=User.Role.SELLER, tenant=self.tenant)
        Seller.objects.create(tenant=self.tenant, user=u1, name='Primeiro', phone='11999999999', cpf=VALID_CPF_DIGITS)

        u2 = User.objects.create_user(username='u2@cpf.com', password='p', role=User.Role.SELLER, tenant=self.tenant)
        response = self.client.post(self.create_url, {
            'name': 'Duplicado', 'phone': '11977777777', 'cpf': VALID_CPF_FORMATTED,
        }, content_type='application/json')
        self.assertEqual(response.status_code, 400, f'Expected 400, got {response.status_code}: {response.data}')
        self.assertIn('Ja existe', str(response.data.get('cpf', '')))

    def test_create_seller_invalid_cpf_400(self):
        response = self.client.post(self.create_url, {
            'name': 'CPF Errado', 'phone': '11966666666', 'cpf': '111.111.111-11',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 400,
                         f'Expected 400, got {response.status_code}: {response.data}')

    def test_update_seller_own_cpf_ok(self):
        u = User.objects.create_user(username='update@cpf.com', password='p', role=User.Role.SELLER, tenant=self.tenant)
        seller = Seller.objects.create(tenant=self.tenant, user=u, name='Update', phone='11944444444', cpf=None)

        url = reverse('api-seller-detail', kwargs={'pk': str(seller.uuid)})
        response = self.client.patch(url, {
            'cpf': VALID_CPF_DIGITS,
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200, f'Update failed: {response.status_code} {response.data}')
        seller.refresh_from_db()
        self.assertEqual(seller.cpf, VALID_CPF_DIGITS)

    def test_update_seller_to_duplicate_cpf_400(self):
        u1 = User.objects.create_user(username='dupes1@cpf.com', password='p', role=User.Role.SELLER, tenant=self.tenant)
        Seller.objects.create(tenant=self.tenant, user=u1, name='Dono', phone='11955555555', cpf=VALID_CPF_DIGITS)

        u2 = User.objects.create_user(username='dupes2@cpf.com', password='p', role=User.Role.SELLER, tenant=self.tenant)
        seller2 = Seller.objects.create(tenant=self.tenant, user=u2, name='Alvo', phone='11966666666', cpf=None)

        url = reverse('api-seller-detail', kwargs={'pk': str(seller2.uuid)})
        response = self.client.patch(url, {
            'cpf': VALID_CPF_FORMATTED,
        }, content_type='application/json')
        self.assertEqual(response.status_code, 400,
                         f'Expected 400, got {response.status_code}: {response.data}')

    def test_import_csv_duplicate_cpf_internal_second_without_cpf(self):
        csv_content = (
            "nome,telefone,cpf\n"
            f"Seller A,11911111111,{VALID_CPF_FORMATTED}\n"
            f"Seller B,11922222222,{VALID_CPF_FORMATTED}\n"
        )
        csv_file = SimpleUploadedFile('test.csv', csv_content.encode('utf-8'), content_type='text/csv')
        url = reverse('api-seller-import-sellers')
        response = self.client.post(url, {'file': csv_file}, format='multipart')
        self.assertEqual(response.status_code, 200, f'Import failed: {response.data}')

        data = response.data
        created = data.get('created', [])
        errors = data.get('errors', [])
        self.assertEqual(len(created), 2,
                         f'Ambos vendedores devem ser criados. Criados: {len(created)}. Erros: {errors}')
        sellers = Seller.objects.filter(name__in=['Seller A', 'Seller B']).order_by('name')
        has_cpf = any(s.cpf == VALID_CPF_DIGITS for s in sellers)
        has_none = any(s.cpf is None for s in sellers)
        self.assertTrue(has_cpf, f'Nenhum seller ficou com CPF. CPFs: {[s.cpf for s in sellers]}')
        self.assertTrue(has_none, f'Ambos ficaram com CPF. CPFs: {[s.cpf for s in sellers]}')
