from django.db import IntegrityError, transaction
from django.utils import timezone

from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller

from .models import Boleto, ReceivableAllocation


class AllocationDomainError(Exception):
    pass


def _get_or_create_manual_sale(boleto, sale_date, allocated_by):
    sale = Sale.objects.select_for_update().filter(
        tenant=boleto.tenant,
        seller=boleto.seller,
        sale_date=sale_date,
        origin=Sale.Origin.MANUAL,
    ).first()
    if sale:
        return sale
    try:
        with transaction.atomic():
            return Sale.objects.create(
                tenant=boleto.tenant,
                seller=boleto.seller,
                origin=Sale.Origin.MANUAL,
                status='ATIVA',
                amount=0,
                sale_date=sale_date,
                created_by=allocated_by,
            )
    except IntegrityError:
        return Sale.objects.select_for_update().get(
            seller=boleto.seller,
            sale_date=sale_date,
            origin=Sale.Origin.MANUAL,
        )


def allocate_paid_boleto(boleto, allocated_by, **_client_values):
    with transaction.atomic():
        locked = Boleto.objects.select_for_update().select_related(
            'tenant', 'seller'
        ).get(pk=boleto.pk)
        existing = ReceivableAllocation.objects.select_for_update().filter(
            tenant=locked.tenant, boleto=locked
        ).first()
        if existing:
            return existing, False
        if locked.status != Boleto.Status.PAGO:
            raise AllocationDomainError('Somente boleto pago pode ser alocado.')
        if not locked.paid_at or not locked.paid_amount_cents:
            raise AllocationDomainError('Boleto pago sem valor ou data confirmados.')
        if allocated_by.tenant_id != locked.tenant_id:
            raise AllocationDomainError('Usuario nao pertence ao tenant do boleto.')

        sale_date = timezone.localtime(locked.paid_at).date()
        Seller.objects.select_for_update().get(pk=locked.seller_id)
        sale = _get_or_create_manual_sale(locked, sale_date, allocated_by)
        sale.amount += locked.paid_amount_cents
        sale.status = 'ATIVA'
        sale.updated_by = allocated_by
        sale.save(update_fields=['amount', 'status', 'updated_by', 'updated_at'])
        allocation = ReceivableAllocation.objects.create(
            tenant=locked.tenant,
            boleto=locked,
            sale=sale,
            amount_cents=locked.paid_amount_cents,
            sale_date=sale_date,
            allocated_by=allocated_by,
        )
        from .commission_services import apply_commission_impact
        from .models import CommissionImpactReview
        apply_commission_impact(
            allocation, CommissionImpactReview.ImpactType.ALLOCATION,
            allocation.amount_cents, 'Alocacao de boleto pago',
        )
        return allocation, True


def reverse_allocation(allocation, reason=''):
    with transaction.atomic():
        locked = ReceivableAllocation.objects.select_for_update().select_related(
            'boleto__tenant', 'boleto__seller'
        ).get(pk=allocation.pk)
        if locked.status == ReceivableAllocation.Status.REVERSED:
            return locked, False
        sale = Sale.objects.select_for_update().get(pk=locked.sale_id)
        if sale.amount < locked.amount_cents:
            raise AllocationDomainError('Saldo da venda menor que a alocacao.')
        new_amount = sale.amount - locked.amount_cents
        sale.amount = new_amount
        if new_amount == 0:
            sale.status = 'ESTORNADA'
        sale.save(update_fields=['amount', 'status', 'updated_at'])
        locked.status = ReceivableAllocation.Status.REVERSED
        locked.reversed_at = timezone.now()
        locked.reversal_reason = str(reason or '')[:255]
        locked.save(update_fields=[
            'status', 'reversed_at', 'reversal_reason', 'updated_at',
        ])
        from .commission_services import apply_commission_impact
        from .models import CommissionImpactReview
        impact_type = (
            CommissionImpactReview.ImpactType.CHARGEBACK
            if 'chargeback' in str(reason or '').lower()
            else CommissionImpactReview.ImpactType.REFUND
        )
        apply_commission_impact(locked, impact_type, -locked.amount_cents, reason)
        return locked, True
