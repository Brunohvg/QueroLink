from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.receivables.allocation_services import (
    AllocationDomainError,
    allocate_paid_boleto,
    reverse_allocation,
)
from app.apps.receivables.models import Boleto, IntegrationOutbox, ReceivableAllocation
from app.apps.receivables.services import mark_paid
from app.apps.receivables.tasks import process_outbox_batch
from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller


class ReceivableAllocationTests(TransactionTestCase):
    def setUp(self):
        self.tenant, self.user, self.seller = self.make_tenant('allocation')
        self.paid_at = timezone.now() - timedelta(days=1)

    def make_tenant(self, slug):
        tenant = Tenant.objects.create(company_name=slug, slug=slug)
        user = User.objects.create_user(
            username=f'{slug}-manager', tenant=tenant, role=User.Role.MANAGER,
        )
        seller_user = User.objects.create_user(
            username=f'{slug}-seller', tenant=tenant, role=User.Role.SELLER,
        )
        seller = Seller.objects.create(
            tenant=tenant, user=seller_user, name='Seller', phone='11999999999',
        )
        return tenant, user, seller

    def boleto(self, key, *, seller=None, tenant=None, amount=10000, paid=True):
        tenant = tenant or self.tenant
        seller = seller or self.seller
        boleto = Boleto.objects.create(
            tenant=tenant, seller=seller, created_by=self.user,
            payer_name='Maria', payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF, payer_phone='11999999999',
            payer_zip_code='01310100', payer_street='Rua A', payer_number='1',
            payer_neighborhood='Centro', payer_city='Sao Paulo', payer_state='SP',
            amount_cents=amount, due_date=timezone.localdate() + timedelta(days=10),
            idempotency_key=key,
            status=Boleto.Status.PAGO if paid else Boleto.Status.PENDENTE,
            paid_at=self.paid_at if paid else None,
            paid_amount_cents=amount if paid else None,
        )
        return boleto

    def test_two_boletos_same_seller_and_day_share_sale(self):
        first, _ = allocate_paid_boleto(self.boleto('one'), self.user)
        second, _ = allocate_paid_boleto(self.boleto('two', amount=5000), self.user)
        first.sale.refresh_from_db()
        self.assertEqual(first.sale_id, second.sale_id)
        self.assertEqual(first.sale.amount, 15000)

    def test_existing_manual_sale_is_incremented(self):
        sale = Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=2000, sale_date=timezone.localtime(self.paid_at).date(),
            created_by=self.user,
        )
        allocation, _ = allocate_paid_boleto(self.boleto('existing'), self.user)
        sale.refresh_from_db()
        self.assertEqual(allocation.sale_id, sale.pk)
        self.assertEqual(sale.amount, 12000)

    def test_two_sellers_have_distinct_sales(self):
        seller_user = User.objects.create_user(
            username='second-seller', tenant=self.tenant, role=User.Role.SELLER,
        )
        other = Seller.objects.create(
            tenant=self.tenant, user=seller_user, name='Other', phone='21999999999',
        )
        first, _ = allocate_paid_boleto(self.boleto('seller-one'), self.user)
        second, _ = allocate_paid_boleto(
            self.boleto('seller-two', seller=other), self.user
        )
        self.assertNotEqual(first.sale_id, second.sale_id)

    def test_duplicate_confirmation_does_not_sum_twice(self):
        boleto = self.boleto('duplicate')
        first, created = allocate_paid_boleto(boleto, self.user)
        second, duplicate_created = allocate_paid_boleto(boleto, self.user)
        first.sale.refresh_from_db()
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.sale.amount, 10000)

    def test_concurrent_confirmation_has_one_allocation(self):
        boleto = self.boleto('concurrent')

        def allocate():
            close_old_connections()
            try:
                return allocate_paid_boleto(
                    Boleto.objects.get(pk=boleto.pk),
                    User.objects.get(pk=self.user.pk),
                )[1]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: allocate(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(ReceivableAllocation.objects.count(), 1)

    def test_client_amount_and_date_are_ignored(self):
        boleto = self.boleto('ignored', amount=12345)
        allocation, _ = allocate_paid_boleto(
            boleto, self.user, amount_cents=1, sale_date=timezone.localdate()
        )
        self.assertEqual(allocation.amount_cents, 12345)
        self.assertEqual(allocation.sale_date, timezone.localtime(self.paid_at).date())

    def test_reversal_subtracts_only_allocation(self):
        first, _ = allocate_paid_boleto(self.boleto('reverse-one'), self.user)
        allocate_paid_boleto(self.boleto('reverse-two', amount=5000), self.user)
        reversed_allocation, changed = reverse_allocation(first, 'refund')
        first.sale.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(reversed_allocation.status, ReceivableAllocation.Status.REVERSED)
        self.assertEqual(first.sale.amount, 5000)
        self.assertEqual(first.sale.status, 'ATIVA')

    def test_reversal_that_zeros_sale_marks_it_reversed(self):
        allocation, _ = allocate_paid_boleto(self.boleto('zero'), self.user)
        reverse_allocation(allocation, 'refund')
        allocation.sale.refresh_from_db()
        self.assertEqual(allocation.sale.amount, 0)
        self.assertEqual(allocation.sale.status, 'ESTORNADA')

    def test_locked_commission_rejects_allocation(self):
        sale_date = timezone.localtime(self.paid_at).date()
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=sale_date.month, year=sale_date.year,
            start_date=sale_date, end_date=sale_date,
        )
        SellerCommission.objects.create(
            period=period, seller=self.seller, status=SellerCommission.Status.FECHADA,
        )
        with self.assertRaises(AllocationDomainError):
            allocate_paid_boleto(self.boleto('locked'), self.user)
        self.assertFalse(ReceivableAllocation.objects.exists())

    def test_tenant_isolation(self):
        other_tenant, other_user, other_seller = self.make_tenant('other-allocation')
        other_boleto = self.boleto(
            'other', tenant=other_tenant, seller=other_seller
        )
        with self.assertRaises(AllocationDomainError):
            allocate_paid_boleto(other_boleto, self.user)
        allocation, _ = allocate_paid_boleto(other_boleto, other_user)
        self.assertEqual(allocation.tenant_id, other_tenant.pk)

    def test_paid_outbox_is_consumed_without_automatic_allocation(self):
        boleto = self.boleto('outbox', paid=False)
        mark_paid(boleto, 10000, self.paid_at)
        self.assertEqual(process_outbox_batch(), 1)
        event = IntegrationOutbox.objects.get(event_type='boleto.paid')
        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationOutbox.Status.PROCESSED)
        self.assertFalse(ReceivableAllocation.objects.exists())
