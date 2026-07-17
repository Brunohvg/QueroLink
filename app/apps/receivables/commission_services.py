from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.commissions.services import create_commission_adjustment, get_commission_rate

from .models import CommissionImpactReview


def apply_commission_impact(allocation, impact_type, delta_sale_cents, reason=''):
    period = CommissionPeriod.objects.filter(
        tenant=allocation.tenant,
        start_date__lte=allocation.sale_date,
        end_date__gte=allocation.sale_date,
    ).exclude(status=CommissionPeriod.Status.CANCELADA).first()
    if not period:
        return None
    commission = SellerCommission.objects.filter(period=period, seller=allocation.boleto.seller).first()
    if not commission:
        return None
    if commission.status in (SellerCommission.Status.ABERTA, SellerCommission.Status.REABERTA):
        commission.recalculate()
        return None
    rate = get_commission_rate(allocation.boleto.seller)
    estimated = int((Decimal(delta_sale_cents) * Decimal(str(rate))).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    review, _ = CommissionImpactReview.objects.get_or_create(
        tenant=allocation.tenant, allocation=allocation, impact_type=impact_type,
        defaults={
            'seller': allocation.boleto.seller,
            'source_period': period,
            'seller_commission': commission,
            'delta_sale_cents': delta_sale_cents,
            'estimated_commission_delta_cents': estimated,
            'reason': str(reason or '')[:255],
        },
    )
    return review


def approve_and_apply_review(review, user, reason):
    with transaction.atomic():
        locked = CommissionImpactReview.objects.select_for_update().select_related('seller_commission').get(pk=review.pk)
        if locked.status == CommissionImpactReview.Status.APPLIED:
            return locked, None, False
        if locked.seller_commission.status == SellerCommission.Status.PAGA:
            raise ValueError('Comissao paga exige compensacao futura.')
        if locked.seller_commission.status not in (SellerCommission.Status.FECHADA, SellerCommission.Status.AJUSTADA):
            raise ValueError('Comissao nao permite ajuste.')
        locked.status = CommissionImpactReview.Status.APPROVED
        locked.reviewed_by = user
        locked.reviewed_at = timezone.now()
        locked.reason = str(reason or locked.reason)[:255]
        locked.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'reason', 'updated_at'])
        previous = locked.seller_commission.frozen_commission_amount or locked.seller_commission.commission_amount
        adjustment = create_commission_adjustment(
            locked.seller_commission,
            max(0, previous + locked.estimated_commission_delta_cents),
            locked.reason,
            user,
        )
        locked.status = CommissionImpactReview.Status.APPLIED
        locked.save(update_fields=['status', 'updated_at'])
        return locked, adjustment, True
