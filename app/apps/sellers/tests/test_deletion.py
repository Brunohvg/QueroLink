from django.test import TestCase
from django.db.utils import IntegrityError
from django.core.management import call_command
from io import StringIO

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale


class SellerDeletionTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Loja Delete', slug='loja-delete', is_active=True,
        )
        self.admin = User.objects.create_user(
            username='admin@delete.com', email='admin@delete.com',
            password='Senha@12345678', role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='seller@delete.com', password='p', role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Deletavel',
            phone='11999999999',
        )

    def test_delete_seller_without_sales_removes_both(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        url = reverse('api-seller-detail', kwargs={'pk': str(self.seller.uuid)})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204,
                         f'Delete failed: {response.status_code} {getattr(response, "data", "")}')
        self.assertFalse(Seller.objects.filter(uuid=self.seller.uuid).exists())
        self.assertFalse(User.objects.filter(username='seller@delete.com').exists())

    def test_delete_seller_with_sales_returns_400(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=10000, status='ATIVA',
            sale_date='2026-07-01',
        )
        from django.urls import reverse
        self.client.force_login(self.admin)
        url = reverse('api-seller-detail', kwargs={'pk': str(self.seller.uuid)})
        response = self.client.delete(url)
        self.assertIn(response.status_code, [400, 403],
                      f'Expected 400/403, got {response.status_code}: {getattr(response, "data", "")}')
        self.assertTrue(Seller.objects.filter(uuid=self.seller.uuid).exists())

    def test_protect_constraint_blocks_direct_orm_delete(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=10000, status='ATIVA',
            sale_date='2026-07-01',
        )
        with self.assertRaises(IntegrityError):
            self.seller.delete()


class OrphanSellerTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Loja Orfa', slug='loja-orfa', is_active=True,
        )

    def test_get_seller_profile_orphan_returns_redirect(self):
        orphan_user = User.objects.create_user(
            username='orfao@teste.com', password='p', role=User.Role.SELLER, tenant=self.tenant,
        )
        from django.test import Client
        client = Client()
        client.force_login(orphan_user)
        response = client.get('/dashboard/mobile/')
        self.assertEqual(response.status_code, 302,
                         f'Expected redirect, got {response.status_code}: {getattr(response, "content", b"")[:200]}')

    def test_fix_orphan_users_dry_run(self):
        User.objects.create_user(
            username='orfao2@teste.com', password='p', role=User.Role.SELLER, tenant=self.tenant,
        )
        out = StringIO()
        call_command('fix_orphan_users', stdout=out)
        output = out.getvalue()
        self.assertIn('ORFAO', output)
        self.assertIn('orfao2', output)
        self.assertIn('Dry-run', output)
