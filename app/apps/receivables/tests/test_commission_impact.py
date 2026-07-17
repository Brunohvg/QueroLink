from django.test import TransactionTestCase
from django.utils import timezone

from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.receivables.allocation_services import allocate_paid_boleto, reverse_allocation
from app.apps.receivables.models import CommissionImpactReview
from app.apps.receivables.tests.test_allocation import ReceivableAllocationTests


class CommissionImpactTests(TransactionTestCase):
    setUp = ReceivableAllocationTests.setUp
    make_tenant = ReceivableAllocationTests.make_tenant
    boleto = ReceivableAllocationTests.boleto

    def commission(self, status):
        sale_date = timezone.localtime(self.paid_at).date()
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=sale_date.month, year=sale_date.year,
            start_date=sale_date, end_date=sale_date,
        )
        commission = SellerCommission.objects.create(
            period=period, seller=self.seller, status=status,
            frozen_commission_amount=100 if status != SellerCommission.Status.ABERTA else None,
        )
        return commission

    def test_open_commission_recalculates_without_review(self):
        commission = self.commission(SellerCommission.Status.ABERTA)
        allocate_paid_boleto(self.boleto('open-impact'), self.user)
        commission.refresh_from_db()
        self.assertEqual(commission.total_sold_amount, 10000)
        self.assertFalse(CommissionImpactReview.objects.exists())

    def test_closed_allocation_creates_review_without_changing_frozen(self):
        commission = self.commission(SellerCommission.Status.FECHADA)
        allocate_paid_boleto(self.boleto('closed-impact'), self.user)
        commission.refresh_from_db()
        self.assertEqual(commission.frozen_commission_amount, 100)
        self.assertEqual(CommissionImpactReview.objects.get().status, 'PENDING')

    def test_closed_refund_creates_distinct_review(self):
        commission = self.commission(SellerCommission.Status.ABERTA)
        allocation, _ = allocate_paid_boleto(self.boleto('refund-impact'), self.user)
        commission.freeze(self.user)
        reverse_allocation(allocation, 'refund')
        self.assertTrue(CommissionImpactReview.objects.filter(
            impact_type=CommissionImpactReview.ImpactType.REFUND,
        ).exists())

    def test_paid_commission_is_immutable_and_creates_future_review(self):
        commission = self.commission(SellerCommission.Status.PAGA)
        commission.paid_amount = 100
        commission.save(update_fields=['paid_amount'])
        allocate_paid_boleto(self.boleto('paid-impact'), self.user)
        commission.refresh_from_db()
        self.assertEqual(commission.status, SellerCommission.Status.PAGA)
        self.assertEqual(commission.paid_amount, 100)
        self.assertEqual(commission.frozen_commission_amount, 100)
        self.assertTrue(CommissionImpactReview.objects.exists())

    def test_missing_period_keeps_sale_without_creating_period(self):
        allocation, _ = allocate_paid_boleto(self.boleto('no-period'), self.user)
        self.assertEqual(allocation.sale.amount, 10000)
        self.assertFalse(CommissionPeriod.objects.exists())
        self.assertFalse(CommissionImpactReview.objects.exists())
