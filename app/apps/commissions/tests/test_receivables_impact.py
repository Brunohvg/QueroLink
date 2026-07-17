from django.test import TransactionTestCase

from app.apps.commissions.models import CommissionAdjustment, SellerCommission
from app.apps.receivables.allocation_services import allocate_paid_boleto
from app.apps.receivables.commission_services import approve_and_apply_review
from app.apps.receivables.tests.test_commission_impact import CommissionImpactTests


class ReceivablesAdjustmentTests(TransactionTestCase):
    setUp = CommissionImpactTests.setUp
    make_tenant = CommissionImpactTests.make_tenant
    boleto = CommissionImpactTests.boleto
    commission = CommissionImpactTests.commission

    def test_approval_is_idempotent_and_creates_one_adjustment(self):
        self.commission(SellerCommission.Status.FECHADA)
        allocation, _ = allocate_paid_boleto(self.boleto('approve-impact'), self.user)
        review = allocation.commission_impact_reviews.get()
        applied, adjustment, created = approve_and_apply_review(review, self.user, 'approved')
        duplicate, duplicate_adjustment, duplicate_created = approve_and_apply_review(review, self.user, 'approved')
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertIsNone(duplicate_adjustment)
        self.assertEqual(applied.status, 'APPLIED')
        self.assertEqual(duplicate.status, 'APPLIED')
        self.assertEqual(CommissionAdjustment.objects.count(), 1)

    def test_paid_review_cannot_be_applied(self):
        self.commission(SellerCommission.Status.PAGA)
        allocation, _ = allocate_paid_boleto(self.boleto('paid-review'), self.user)
        with self.assertRaises(ValueError):
            approve_and_apply_review(
                allocation.commission_impact_reviews.get(), self.user, 'future'
            )
