"""Servicos de dominio para justificativas de dia sem lancamento.

Toda mutacao de SellerDayJustification passa por aqui. Os servicos:
- validam tenant/seller;
- usam transaction.atomic;
- definem created_by/updated_by;
- registram auditoria (AuditLog generico via log_action);
- NUNCA criam/alteram Sale, CommissionPeriod ou SellerCommission.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction

from app.apps.audit.utils import log_action
from app.apps.sellers.models import SellerDayJustification


class JustificationError(Exception):
    """Erro de dominio ao manipular justificativas."""


def _flatten(exc):
    try:
        parts = []
        for field, msgs in exc.message_dict.items():
            parts.append(f'{field}: {"; ".join(msgs)}')
        return ' | '.join(parts) if parts else str(exc)
    except AttributeError:
        return '; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc)


_DUPLICATE_MSG = (
    'Ja existe uma justificativa para este vendedor nesta data.'
)


def create_day_justification(*, tenant, seller, date, reason, notes='', user):
    """Cria uma justificativa. Nao cria nem altera Sale."""
    if seller.tenant_id != tenant.pk:
        raise JustificationError('O vendedor nao pertence ao tenant.')

    obj = SellerDayJustification(
        tenant=tenant, seller=seller, date=date, reason=reason,
        notes=notes or '', created_by=user, updated_by=user,
    )
    try:
        obj.full_clean(
            exclude=['created_by', 'updated_by'], validate_constraints=False,
        )
    except DjangoValidationError as exc:
        raise JustificationError(_flatten(exc))

    if SellerDayJustification.objects.filter(
        tenant=tenant, seller=seller, date=obj.date,
    ).exists():
        raise JustificationError(_DUPLICATE_MSG)

    try:
        with transaction.atomic():
            obj.save()
            log_action(
                user, 'day_justification.created', instance=obj,
                changes={
                    'seller': str(seller.uuid),
                    'date': obj.date.isoformat(),
                    'reason': obj.reason,
                    'notes': obj.notes,
                },
            )
    except IntegrityError:
        raise JustificationError(_DUPLICATE_MSG)
    return obj


def update_day_justification(*, justification, user, reason=None, notes=None,
                             date=None):
    """Atualiza reason/notes/date. Registra apenas campos realmente mudados."""
    changes = {}
    if reason is not None and reason != justification.reason:
        changes['reason'] = {'old': justification.reason, 'new': reason}
        justification.reason = reason
    if notes is not None and notes != justification.notes:
        changes['notes'] = {'old': justification.notes, 'new': notes}
        justification.notes = notes
    if date is not None and date != justification.date:
        changes['date'] = {
            'old': justification.date.isoformat(),
            'new': date.isoformat(),
        }
        justification.date = date

    if not changes:
        return justification

    justification.updated_by = user
    try:
        justification.full_clean(
            exclude=['created_by', 'updated_by'], validate_constraints=False,
        )
    except DjangoValidationError as exc:
        raise JustificationError(_flatten(exc))

    if 'date' in changes and SellerDayJustification.objects.filter(
        tenant=justification.tenant_id, seller=justification.seller_id,
        date=justification.date,
    ).exclude(pk=justification.pk).exists():
        raise JustificationError(_DUPLICATE_MSG)

    try:
        with transaction.atomic():
            justification.save()
            log_action(
                user, 'day_justification.updated', instance=justification,
                changes=changes,
            )
    except IntegrityError:
        raise JustificationError(_DUPLICATE_MSG)
    return justification


def delete_day_justification(*, justification, user):
    """Remove a justificativa. Nao toca em Sale/comissao."""
    snapshot = {
        'seller': str(justification.seller.uuid),
        'date': justification.date.isoformat(),
        'reason': justification.reason,
        'notes': justification.notes,
    }
    with transaction.atomic():
        log_action(
            user, 'day_justification.deleted', instance=justification,
            changes=snapshot,
        )
        justification.delete()
