from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase
from django.db.migrations.loader import MigrationLoader

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


class CpfTest(TransactionTestCase):
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

    def test_dedupe_funcao_real_mantem_mais_antigo(self):
        import importlib
        from django.db import connection
        from django.apps import apps as django_apps

        if connection.vendor != 'postgresql':
            self.skipTest('raw SQL DROP CONSTRAINT requires PostgreSQL')

        mig = importlib.import_module('app.apps.sellers.migrations.0012_dedupe_cpf')

        s1 = Seller.objects.create(
            tenant=self.tenant, user=self.user1, name='Seller A', phone='11911111111',
        )
        s2 = Seller.objects.create(
            tenant=self.tenant, user=self.user2, name='Seller B', phone='11922222222',
        )

        cpf = '52998224725'
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE sellers_seller DROP CONSTRAINT IF EXISTS unique_cpf_per_tenant')
            cursor.execute('UPDATE sellers_seller SET cpf = %s WHERE uuid = %s', [cpf, s1.pk])
            cursor.execute('UPDATE sellers_seller SET cpf = %s WHERE uuid = %s', [cpf, s2.pk])

        mig.dedupe_cpf(django_apps, None)

        s1.refresh_from_db()
        s2.refresh_from_db()
        older, newer = (s1, s2) if s1.created_at <= s2.created_at else (s2, s1)
        self.assertEqual(older.cpf, cpf)
        self.assertIsNone(newer.cpf)


class CpfMigrationOrderTests(TestCase):
    def test_dedupe_roda_antes_da_constraint(self):
        from django.db import connection
        loader = MigrationLoader(connection, load=True)
        graph = loader.graph
        dedupe = ('sellers', '0012_dedupe_cpf')
        constraint = ('sellers', '0010_add_unique_cpf')
        normalize = ('sellers', '0011_normalize_cpf_digits')

        plan = [node for node in graph.forwards_plan(constraint)]
        self.assertIn(dedupe, plan, 'dedupe (0012) nao esta no caminho ate a constraint (0010)')
        self.assertLess(
            plan.index(normalize), plan.index(dedupe),
            'normalize (0011) deve rodar antes da dedupe (0012)',
        )
        self.assertLess(
            plan.index(dedupe), plan.index(constraint),
            'REGRESSAO: dedupe (0012) deve rodar ANTES da constraint (0010)',
        )
