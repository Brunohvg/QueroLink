from django.test import TestCase
from django.urls import reverse
from django.core.cache import cache
from django.db.utils import IntegrityError
from datetime import date
from rest_framework.test import APIClient
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.commissions.models import (
    CommissionPeriod,
    SellerCommission,
    CommissionAdjustment,
)
from app.apps.sales.models import Sale


class CommissionPeriodStatusTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Bibelo', cnpj='11111111111111',
        )
        self.manager = User.objects.create_user(
            username='gestor', password='gestor123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.financeiro = User.objects.create_user(
            username='financeiro', password='fin123',
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='vendedor', password='senha123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Vendedor Teste', phone='111',
            user=self.seller_user,
        )

        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=0.05,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date='2026-06-10', created_by=self.seller_user,
        )

    def _auth(self, user, password):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': user.username, 'password': password,
        }, format='json')
        self.assertIn('access', resp.data, f"Login failed for {user.username}")
        client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}',
        )
        return client

    def _auth_manager(self):
        return self._auth(self.manager, 'gestor123')

    def _auth_financeiro(self):
        return self._auth(self.financeiro, 'fin123')

    def _url(self, action):
        url_action = action.replace('_', '-')
        return reverse(
            f'api-commission-period-{url_action}', args=[self.period.uuid],
        )

    def test_close_aberta_works(self):
        self.assertEqual(
            self.period.status, CommissionPeriod.Status.ABERTA,
        )
        client = self._auth_manager()
        response = client.post(self._url('close'))
        self.assertEqual(response.status_code, 200)
        self.period.refresh_from_db()
        self.assertEqual(
            self.period.status, CommissionPeriod.Status.FECHADA,
        )
        self.assertIsNotNone(self.period.closed_at)
        self.assertEqual(self.period.closed_by, self.manager)
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.total_sold_amount, 10000)

    def test_cannot_close_twice(self):
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        client = self._auth_manager()
        response = client.post(self._url('close'))
        self.assertEqual(response.status_code, 400)

    def test_mark_paid_from_fechada_works(self):
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        client = self._auth_manager()
        resp = client.post(self._url('mark_paid'), {
            'payment_date': '2026-07-01',
            'payment_method': 'pix',
            'payment_notes': 'Pago conforme acordado',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, CommissionPeriod.Status.PAGA)
        self.assertIsNotNone(self.period.paid_at)
        self.assertEqual(self.period.paid_by, self.manager)
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.paid_by, self.manager)
        self.assertEqual(self.sc.paid_amount, self.sc.commission_amount)
        self.assertEqual(self.sc.payment_method, 'pix')
        self.assertEqual(
            self.sc.payment_date, date(2026, 7, 1),
        )

    def test_cannot_mark_paid_before_closed(self):
        client = self._auth_manager()
        resp = client.post(self._url('mark_paid'))
        self.assertEqual(resp.status_code, 400)

    def test_cannot_mark_paid_when_already_paid(self):
        self.period.status = CommissionPeriod.Status.PAGA
        self.period.save()
        client = self._auth_manager()
        resp = client.post(self._url('mark_paid'))
        self.assertEqual(resp.status_code, 400)

    def test_financeiro_can_mark_paid(self):
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        client = self._auth_financeiro()
        resp = client.post(self._url('mark_paid'), format='json')
        self.assertEqual(resp.status_code, 200)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, CommissionPeriod.Status.PAGA)

    def test_adjust_requires_reason(self):
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        client = self._auth_manager()
        resp = client.post(self._url('adjust'), {
            'adjustments': [{
                'seller_commission_id': self.sc.id,
                'new_amount': 600,
            }],
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_adjust_from_fechada_works(self):
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        self.sc.commission_amount = 500
        self.sc.save()
        client = self._auth_manager()
        resp = client.post(self._url('adjust'), {
            'reason': 'Ajuste pos-conferencia',
            'adjustments': [{
                'seller_commission_id': self.sc.id,
                'new_amount': 600,
            }],
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.period.refresh_from_db()
        self.assertEqual(
            self.period.status, CommissionPeriod.Status.AJUSTADA,
        )
        self.assertIsNotNone(self.period.adjusted_at)
        self.assertEqual(
            self.period.adjustment_reason, 'Ajuste pos-conferencia',
        )
        self.sc.refresh_from_db()
        self.assertEqual(self.sc.commission_amount, 600)
        adj = CommissionAdjustment.objects.first()
        self.assertIsNotNone(adj)
        self.assertEqual(adj.previous_amount, 500)
        self.assertEqual(adj.new_amount, 600)
        self.assertEqual(adj.difference, 100)

    def test_cancel_from_fechada_works(self):
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        client = self._auth_manager()
        resp = client.post(self._url('cancel'), {
            'reason': 'Erro operacional no fechamento',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.period.refresh_from_db()
        self.assertEqual(
            self.period.status, CommissionPeriod.Status.CANCELADA,
        )
        self.assertEqual(
            self.period.cancel_reason, 'Erro operacional no fechamento',
        )

    def test_cancel_requires_reason(self):
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        client = self._auth_manager()
        resp = client.post(self._url('cancel'), {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_cannot_cancel_paid(self):
        self.period.status = CommissionPeriod.Status.PAGA
        self.period.save()
        client = self._auth_manager()
        resp = client.post(self._url('cancel'), {
            'reason': 'Tentativa invalida',
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_full_flow_close_mark_paid(self):
        client_mgr = self._auth_manager()

        resp = client_mgr.post(self._url('close'))
        self.assertEqual(resp.status_code, 200)
        self.period.refresh_from_db()
        self.assertEqual(
            self.period.status, CommissionPeriod.Status.FECHADA,
        )

        resp = client_mgr.post(self._url('mark_paid'), {
            'payment_date': '2026-07-05',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.period.refresh_from_db()
        self.assertEqual(
            self.period.status, CommissionPeriod.Status.PAGA,
        )


class SaleManualEntryBusinessRulesTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Bibelo', cnpj='22222222222222',
        )
        self.seller_user = User.objects.create_user(
            username='vendedor', password='senha123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Vendedor Teste', phone='111',
            user=self.seller_user,
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
        )
        SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=0.05,
        )

    def _auth_seller(self):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': 'vendedor', 'password': 'senha123',
        }, format='json')
        client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}',
        )
        return client

    def _create_sale(self, client, amount=5000, sale_date='2026-06-15'):
        return client.post(reverse('api-sale-list'), {
            'origin': 'MANUAL',
            'amount': amount,
            'sale_date': sale_date,
        }, format='json')

    def test_seller_creates_manual_sale(self):
        client = self._auth_seller()
        resp = self._create_sale(client)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Sale.objects.filter(
            seller=self.seller, origin=Sale.Origin.MANUAL,
        ).count(), 1)

    def test_cannot_create_duplicate_manual_same_date(self):
        client = self._auth_seller()
        resp = self._create_sale(client, sale_date='2026-06-15')
        self.assertEqual(resp.status_code, 201)

        resp = self._create_sale(client, amount=7000, sale_date='2026-06-15')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('ja existe', str(resp.data).lower() or 'sale_date')

    def test_cannot_create_future_date(self):
        client = self._auth_seller()
        resp = self._create_sale(client, sale_date='2099-01-01')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('futura', str(resp.data).lower())

    def test_cannot_create_previous_month(self):
        client = self._auth_seller()
        resp = self._create_sale(client, sale_date='2026-05-01')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('anteriores', str(resp.data).lower())

    def test_can_create_same_month_previous_day(self):
        client = self._auth_seller()
        resp = self._create_sale(client, sale_date='2026-06-01')
        self.assertEqual(resp.status_code, 201)

    def test_cannot_create_in_closed_period(self):
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        client = self._auth_seller()
        resp = self._create_sale(client, sale_date='2026-06-20')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('fechado', str(resp.data).lower())


class SellerCommissionRecalculateTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Bibelo', cnpj='44444444444444',
        )
        self.user = User.objects.create_user(
            username='vendedor_teste', password='pass',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Vendedor Teste', phone='3333',
            user=self.user, commission_rate=0.05,
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=0.05,
        )

    def test_recalculate_sums_only_manual_sales(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date='2026-06-10', created_by=self.user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=20000,
            sale_date='2026-06-15', created_by=self.user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.LINK, amount=999999,
            sale_date='2026-06-12', created_by=self.user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=5000,
            sale_date='2026-07-01', created_by=self.user,
        )

        self.sc.recalculate()

        self.assertEqual(self.sc.total_sold_amount, 30000)
        self.assertEqual(self.sc.commission_amount, int(30000 * 0.05))

    def test_recalculate_empty_period(self):
        self.sc.recalculate()
        self.assertEqual(self.sc.total_sold_amount, 0)
        self.assertEqual(self.sc.commission_amount, 0)

    def test_link_sales_not_counted(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.LINK, amount=500000,
            sale_date='2026-06-10', created_by=self.user,
        )
        self.sc.recalculate()
        self.assertEqual(self.sc.total_sold_amount, 0)

    def test_freeze_sets_values_and_marks_closed_by(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=80000,
            sale_date='2026-06-10', created_by=self.user,
        )
        user = User.objects.create_user(
            username='gestor_test', password='pass',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.sc.freeze(user)
        self.assertEqual(self.sc.total_sold_amount, 80000)
        self.assertIsNotNone(self.sc.closed_at)
        self.assertEqual(self.sc.closed_by, user)

    def test_recalculate_manual_only_ignores_other_origins(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=15000,
            sale_date='2026-06-05', created_by=self.user,
        )
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.LINK, amount=500000,
            sale_date='2026-06-10', created_by=self.user,
        )
        self.sc.recalculate()
        self.assertEqual(self.sc.total_sold_amount, 15000)


class CommissionAdjustmentModelTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Bibelo', cnpj='55555555555555',
        )
        self.user = User.objects.create_user(
            username='vendedor', password='pass',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Vendedor', phone='111',
            user=self.user,
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_amount=500,
        )
        self.gestor = User.objects.create_user(
            username='gestor', password='pass',
            role=User.Role.MANAGER, tenant=self.tenant,
        )

    def test_adjustment_creates_correct_difference(self):
        adj = CommissionAdjustment.objects.create(
            seller_commission=self.sc,
            previous_amount=500,
            new_amount=700,
            difference=200,
            reason='Correcao apos auditoria',
            adjusted_by=self.gestor,
        )
        self.assertEqual(adj.difference, 200)
        self.assertEqual(adj.previous_amount, 500)
        self.assertEqual(adj.new_amount, 700)
        self.assertEqual(self.sc.adjustments.count(), 1)


class CommissionPeriodLockedTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Bibelo', cnpj='66666666666666',
        )
        self.period_aberta = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
            status=CommissionPeriod.Status.ABERTA,
        )
        self.period_fechada = CommissionPeriod.objects.create(
            tenant=self.tenant, month=5, year=2026,
            status=CommissionPeriod.Status.FECHADA,
        )
        self.period_paga = CommissionPeriod.objects.create(
            tenant=self.tenant, month=4, year=2026,
            status=CommissionPeriod.Status.PAGA,
        )

    def test_is_locked_for_fechada(self):
        self.assertTrue(
            CommissionPeriod.is_locked_for(
                self.tenant, date(2026, 5, 15),
            ),
        )

    def test_is_locked_for_paga(self):
        self.assertTrue(
            CommissionPeriod.is_locked_for(
                self.tenant, date(2026, 4, 15),
            ),
        )

    def test_is_not_locked_for_aberta(self):
        self.assertFalse(
            CommissionPeriod.is_locked_for(
                self.tenant, date(2026, 6, 15),
            ),
        )
