from django.db import transaction
from django.utils import timezone
from app.apps.sales.models import Sale, SaleChangeLog
from app.apps.commissions.services import (
    resolve_period_for_date,
    ensure_seller_commission,
)
from app.apps.commissions.models import SellerCommission


EDITABLE_FIELDS = {'amount', 'sale_date', 'notes'}


def _validate_period(sale, ref_date):
    period = resolve_period_for_date(sale.tenant, ref_date)
    if not period:
        return True, None
    sc = SellerCommission.objects.filter(
        period=period, seller=sale.seller,
    ).first()
    if not sc:
        return True, None
    if not sc.is_editable:
        return False, (
            f'A comissão de {sale.seller.name} já foi fechada '
            f'ou paga nesta competência.'
        )
    return True, None


def update_sale_as_manager(sale, user, data, reason):
    if sale.status == 'ESTORNADA':
        raise ValueError('Venda estornada não pode ser alterada.')

    if not reason or len(reason.strip()) < 5:
        raise ValueError('Motivo é obrigatório e deve ter no mínimo 5 caracteres.')

    can, err = _validate_period(sale, sale.sale_date)
    if not can:
        raise ValueError(err)

    new_date = data.get('sale_date', sale.sale_date)
    if new_date != sale.sale_date:
        can, err = _validate_period(sale, new_date)
        if not can:
            raise ValueError(err)

    field_changes = {}
    for field in EDITABLE_FIELDS:
        if field in data:
            new_value = data[field]
            old_value = getattr(sale, field)
            if new_value != old_value:
                if isinstance(old_value, (int, float)):
                    field_changes[field] = {
                        'old': old_value,
                        'new': new_value,
                    }
                elif field == 'sale_date':
                    field_changes[field] = {
                        'old': str(old_value),
                        'new': str(new_value),
                    }
                else:
                    field_changes[field] = {
                        'old': str(old_value) if old_value else '',
                        'new': str(new_value) if new_value else '',
                    }

    if not field_changes:
        return sale, False

    with transaction.atomic():
        for field in EDITABLE_FIELDS:
            if field in data:
                setattr(sale, field, data[field])
        sale.updated_by = user
        sale.save()

        SaleChangeLog.objects.create(
            sale=sale,
            tenant=sale.tenant,
            action=SaleChangeLog.Action.UPDATE,
            changed_by=user,
            field_changes=field_changes,
            reason=reason.strip(),
        )

        if 'sale_date' in field_changes or 'amount' in field_changes:
            ensure_seller_commission(sale.seller, sale.sale_date)

    return sale, True
