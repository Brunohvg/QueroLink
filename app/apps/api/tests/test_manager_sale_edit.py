from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from django.core.cache import cache

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale, SaleChangeLog
from app.apps.commissions.models import (
    CommissionPeriod,
    SellerCommission,
)


class ManagerSaleEditTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Teste MSE',
            slug='teste-mse',
            default_commission_rate=Decimal('0.05'),
        )
        self.tenant2 = Tenant.objects.create(
            company_name='Outro MSE',
            slug='outro-mse',
            default_commission_rate=Decimal('0.05'),
        )

        self.manager = User.objects.create_user(
            username='manager', password='pass123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.admin = User.objects.create_user(
            username='admin', password='pass123',
            role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='seller', password='pass123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller_profile = Seller.objects.create(
            tenant=self.tenant, name='Vendedor',
            phone='11999999999', user=self.seller_user,
        )
        self.other_seller_user = User.objects.create_user(
            username='other_seller', password='pass123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.other_seller_profile = Seller.objects.create(
            tenant=self.tenant, name='Outro Vendedor',
            phone='11988888888', user=self.other_seller_user,
        )
        self.manager2 = User.objects.create_user(
            username='manager2', password='pass123',
            role=User.Role.MANAGER, tenant=self.tenant2,
        )

        self.sale = Sale.objects.create(
            tenant=self.tenant,
            seller=self.seller_profile,
            origin=Sale.Origin.MANUAL,
            amount=10000,
            sale_date='2026-06-15',
            notes='Venda original',
            created_by=self.seller_user,
        )

        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=6,
            year=2026,
            start_date='2026-06-01',
            end_date='2026-06-30',
            status=CommissionPeriod.Status.ABERTA,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period,
            seller=self.seller_profile,
            commission_rate=Decimal('0.05'),
            status=SellerCommission.Status.ABERTA,
        )

    def _auth(self, user):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': user.username, 'password': 'pass123',
        }, format='json')
        self.assertIn(
            'access', resp.data,
            f"Login failed for {user.username}: {resp.data}",
        )
        client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}'
        )
        return client

    def _manager_update(self, client, sale_uuid, data):
        url = reverse('api-sale-manager-update', args=[sale_uuid])
        return client.patch(url, data, format='json')

    def _get_history(self, client, sale_uuid):
        url = reverse('api-sale-history', args=[sale_uuid])
        return client.get(url)

    # Teste 1: Gestor edita valor com motivo
    def test_manager_updates_amount_with_reason(self):
        client = self._auth(self.manager)
        resp = self._manager_update(client, self.sale.uuid, {
            'amount': 20000,
            'reason': 'Correcao de valor aprovada pelo supervisor.',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['changed'])

        self.sale.refresh_from_db()
        self.assertEqual(self.sale.amount, 20000)
        self.assertEqual(self.sale.updated_by, self.manager)

    # Teste 2: Log contém old/new corretos
    def test_log_contains_old_and_new_values(self):
        client = self._auth(self.manager)
        self._manager_update(client, self.sale.uuid, {
            'amount': 25000,
            'notes': 'Nota atualizada',
            'reason': 'Ajuste de valor e observacao.',
        })

        log = SaleChangeLog.objects.filter(sale=self.sale).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.action, SaleChangeLog.Action.UPDATE)
        self.assertEqual(log.changed_by, self.manager)
        self.assertEqual(log.field_changes['amount']['old'], 10000)
        self.assertEqual(log.field_changes['amount']['new'], 25000)
        self.assertIn('amount', log.field_changes)
        self.assertIn('notes', log.field_changes)

    # Teste 3: updated_by = gestor
    def test_updated_by_is_manager(self):
        client = self._auth(self.admin)
        self._manager_update(client, self.sale.uuid, {
            'amount': 15000,
            'reason': 'Admin corrigiu valor.',
        })
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.updated_by, self.admin)

    # Teste 4: Sem motivo retorna 400
    def test_without_reason_returns_400(self):
        client = self._auth(self.manager)
        resp = self._manager_update(client, self.sale.uuid, {
            'amount': 20000,
        })
        self.assertEqual(resp.status_code, 400)

    # Teste 5: Venda intacta se erro
    def test_sale_unchanged_on_error(self):
        client = self._auth(self.manager)
        original_amount = self.sale.amount
        resp = self._manager_update(client, self.sale.uuid, {
            'amount': 20000,
        })
        self.assertEqual(resp.status_code, 400)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.amount, original_amount)

    # Teste 6: Período fechado retorna 409
    def test_closed_period_returns_409(self):
        self.sc.status = SellerCommission.Status.FECHADA
        self.sc.save()

        client = self._auth(self.manager)
        resp = self._manager_update(client, self.sale.uuid, {
            'amount': 20000,
            'reason': 'Tentativa em periodo fechado.',
        })
        self.assertEqual(resp.status_code, 409)

    # Teste 7: Após reabrir, edição funciona
    def test_after_reopen_edit_works(self):
        self.sc.status = SellerCommission.Status.FECHADA
        self.sc.save()

        self.sc.status = SellerCommission.Status.ABERTA
        self.sc.save()

        client = self._auth(self.manager)
        resp = self._manager_update(client, self.sale.uuid, {
            'amount': 20000,
            'reason': 'Periodo foi reaberto.',
        })
        self.assertEqual(resp.status_code, 200)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.amount, 20000)

    # Teste 8: Período pago retorna 409
    def test_paid_period_returns_409(self):
        self.sc.status = SellerCommission.Status.PAGA
        self.sc.save()

        client = self._auth(self.manager)
        resp = self._manager_update(client, self.sale.uuid, {
            'amount': 20000,
            'reason': 'Tentativa em periodo pago.',
        })
        self.assertEqual(resp.status_code, 409)

    # Teste 9: Mudança de data para período fechado retorna 409
    def test_date_change_to_closed_period_returns_409(self):
        period2 = CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=7,
            year=2026,
            start_date='2026-07-01',
            end_date='2026-07-31',
            status=CommissionPeriod.Status.ABERTA,
        )
        sc2 = SellerCommission.objects.create(
            period=period2,
            seller=self.seller_profile,
            commission_rate=Decimal('0.05'),
            status=SellerCommission.Status.FECHADA,
        )

        client = self._auth(self.manager)
        resp = self._manager_update(client, self.sale.uuid, {
            'sale_date': '2026-07-15',
            'reason': 'Tentando mudar para periodo fechado.',
        })
        self.assertEqual(resp.status_code, 409)

    # Teste 10: Venda estornada retorna 409
    def test_voided_sale_returns_409(self):
        self.sale.status = 'ESTORNADA'
        self.sale.save()

        client = self._auth(self.manager)
        resp = self._manager_update(client, self.sale.uuid, {
            'amount': 20000,
            'reason': 'Tentativa em venda estornada.',
        })
        self.assertEqual(resp.status_code, 409)

    # Teste 11: Seller tentando manager-update retorna 403
    def test_seller_cannot_manager_update_returns_403(self):
        client = self._auth(self.seller_user)
        resp = self._manager_update(client, self.sale.uuid, {
            'amount': 20000,
            'reason': 'Vendedor tentando.',
        })
        self.assertEqual(resp.status_code, 403)

    # Teste 12: Nada mudou retorna 200 sem log
    def test_no_changes_returns_200_no_log(self):
        client = self._auth(self.manager)
        resp = self._manager_update(client, self.sale.uuid, {
            'amount': 10000,
            'notes': 'Venda original',
            'sale_date': '2026-06-15',
            'reason': 'Nada mudou, apenas teste.',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['changed'])
        log_count = SaleChangeLog.objects.filter(sale=self.sale).count()
        self.assertEqual(log_count, 0)

    # Teste 13: history - gestor vê
    def test_manager_can_see_history(self):
        client = self._auth(self.manager)
        self._manager_update(client, self.sale.uuid, {
            'amount': 30000,
            'reason': 'Primeira alteracao.',
        })
        self._manager_update(client, self.sale.uuid, {
            'notes': 'Observacao atualizada',
            'reason': 'Segunda alteracao.',
        })

        resp = self._get_history(client, self.sale.uuid)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 2)

    # Teste 14: history - vendedor dono vê
    def test_owner_seller_can_see_history(self):
        client = self._auth(self.manager)
        self._manager_update(client, self.sale.uuid, {
            'amount': 30000,
            'reason': 'Alteracao inicial.',
        })

        seller_client = self._auth(self.seller_user)
        resp = self._get_history(seller_client, self.sale.uuid)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)

    # Teste 15: history - outro vendedor recebe 403/404
    def test_other_seller_cannot_see_history(self):
        client = self._auth(self.other_seller_user)
        resp = self._get_history(client, self.sale.uuid)
        self.assertIn(resp.status_code, (403, 404))

    # Teste 16: Multi-tenant isolado
    def test_multi_tenant_isolation(self):
        client = self._auth(self.manager2)
        resp = self._manager_update(client, self.sale.uuid, {
            'amount': 20000,
            'reason': 'Tentativa de outro tenant.',
        })
        self.assertIn(resp.status_code, (403, 404))

        resp = self._get_history(client, self.sale.uuid)
        self.assertIn(resp.status_code, (403, 404))
