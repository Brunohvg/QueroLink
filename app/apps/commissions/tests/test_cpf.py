from django.core.exceptions import ValidationError
from django.test import TestCase

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller


def _create_seller(tenant, user, name, cpf=None, phone='11999999999'):
    from decimal import Decimal
    seller = Seller(
        tenant=tenant, user=user, name=name, phone=phone,
        cpf=cpf, commission_rate=Decimal('0.01'),
    )
    seller.full_clean()
    seller.save()
    return seller


class CpfTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='CPF Test', slug='cpf-test', is_active=True)
        self.tenant2 = Tenant.objects.create(company_name='CPF Test 2', slug='cpf-test2', is_active=True)
        self.user1 = User.objects.create_user(username='usr1', password='p', role=User.Role.SELLER, tenant=self.tenant)
        self.user2 = User.objects.create_user(username='usr2', password='p', role=User.Role.SELLER, tenant=self.tenant)
        self.user3 = User.objects.create_user(username='usr3', password='p', role=User.Role.SELLER, tenant=self.tenant2)

    def test_cpf_valido_salva_digitos(self):
        seller = _create_seller(self.tenant, self.user1, 'A', '529.982.247-25', '11911111111')
        self.assertEqual(seller.cpf, '52998224725')

    def test_cpf_formatted(self):
        seller = _create_seller(self.tenant, self.user1, 'A', '52998224725', '11911111111')
        self.assertEqual(seller.cpf_formatted, '529.982.247-25')

    def test_cpf_invalido_erro(self):
        seller = Seller(tenant=self.tenant, user=self.user1, name='A', phone='11911111111', cpf='111.111.111-11')
        with self.assertRaises(ValidationError):
            seller.full_clean()

    def test_cpf_duplicado_mesmo_tenant_erro(self):
        _create_seller(self.tenant, self.user1, 'A', '52998224725', '11911111111')
        seller2 = Seller(tenant=self.tenant, user=self.user2, name='B', phone='11922222222', cpf='52998224725')
        with self.assertRaises(ValidationError):
            seller2.full_clean()

    def test_mesmo_cpf_tenants_diferentes_permitido(self):
        _create_seller(self.tenant, self.user1, 'A', '52998224725', '11911111111')
        seller2 = _create_seller(self.tenant2, self.user3, 'B', '52998224725', '11933333333')
        self.assertEqual(seller2.cpf, '52998224725')

    def test_dois_sellers_sem_cpf_permitido(self):
        _create_seller(self.tenant, self.user1, 'A', phone='11911111111')
        seller2 = _create_seller(self.tenant, self.user2, 'B', phone='11922222222')
        self.assertIsNone(seller2.cpf)
