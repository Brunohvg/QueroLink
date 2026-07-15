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
    """Erro de dominio ao manipular justificativas (400)."""


class JustificationConflictError(JustificationError):
    """Conflito venda x justificativa no mesmo dia (409)."""


class JustificationLockedError(JustificationError):
    """Competencia fechada/paga/cancelada bloqueia a mutacao (409)."""


_CONFLICT_MSG = (
    'Este dia ja possui venda registrada e nao pode ser justificado.'
)
_LOCKED_MSG = (
    'A competencia deste dia esta fechada/paga e nao permite alterar '
    'justificativas.'
)


def _has_active_manual_sale(tenant, seller, date):
    from app.apps.sales.models import Sale
    return Sale.objects.filter(
        tenant=tenant, seller=seller, sale_date=date,
        origin__in=Sale.COMMISSION_ORIGINS, status='ATIVA',
    ).exists()


def has_active_justification(tenant, seller, date):
    """Usado pelo fluxo de venda para detectar dia justificado."""
    return SellerDayJustification.objects.filter(
        tenant=tenant, seller=seller, date=date,
    ).exists()


def _assert_period_unlocked(seller, date, user=None):
    """Bloqueia mutacao se a competencia que cobre a data estiver travada.

    Regra central por STATUS do periodo (LOTE 3): FECHADA/PAGA/CANCELADA ou
    SellerCommission travada bloqueiam. Considera periodos CANCELADOS (que
    resolve_period_for_date ignora) para nao permitir contornar fechamento.
    Sem competencia cobrindo a data -> permitido.
    """
    from app.apps.commissions.models import CommissionPeriod
    from app.apps.commissions.services import is_period_editable_for_seller

    periods = list(
        CommissionPeriod.objects.filter(
            tenant=seller.tenant,
            start_date__lte=date,
            end_date__gte=date,
        )
    )
    if not periods:
        return
    non_cancelled = [
        p for p in periods
        if p.status != CommissionPeriod.Status.CANCELADA
    ]
    target = non_cancelled[0] if non_cancelled else periods[0]
    if not is_period_editable_for_seller(seller, target):
        raise JustificationLockedError(_LOCKED_MSG)


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
    """Cria uma justificativa. Nao cria nem altera Sale.

    LOTE 2/3: bloqueia se ja houver venda manual ativa no dia (409) ou se a
    competencia estiver fechada/paga/cancelada (409).
    """
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

    _assert_period_unlocked(seller, obj.date, user)

    if _has_active_manual_sale(tenant, seller, obj.date):
        raise JustificationConflictError(_CONFLICT_MSG)

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

    _assert_period_unlocked(
        justification.seller, justification.date, user,
    )
    justification.updated_by = user
    try:
        justification.full_clean(
            exclude=['created_by', 'updated_by'], validate_constraints=False,
        )
    except DjangoValidationError as exc:
        raise JustificationError(_flatten(exc))

    if 'date' in changes:
        _assert_period_unlocked(justification.seller, justification.date, user)
        if _has_active_manual_sale(
            justification.tenant, justification.seller, justification.date,
        ):
            raise JustificationConflictError(_CONFLICT_MSG)
        if SellerDayJustification.objects.filter(
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
    """Remove a justificativa. Nao toca em Sale/comissao.

    LOTE 3: bloqueada se a competencia do dia estiver fechada/paga/cancelada.
    """
    _assert_period_unlocked(justification.seller, justification.date, user)
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


def replace_justification_with_sale(*, justification, amount, user, notes=''):
    """Fluxo de gestor: remove a justificativa e cria a venda, atomicamente.

    LOTE 2: acao explicita para substituir justificativa por venda no mesmo dia.
    Rollback completo se qualquer etapa falhar. Registra auditoria das duas
    acoes. Nao cria venda ficticia: `amount` deve ser > 0.
    """
    from app.apps.sales.models import Sale
    from app.apps.commissions.services import (
        validate_sale_can_be_changed, ensure_seller_commission,
    )

    tenant = justification.tenant
    seller = justification.seller
    date = justification.date

    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise JustificationError('Valor invalido.')
    if amount <= 0:
        raise JustificationError('O valor da venda deve ser maior que zero.')
    if amount > 10_000_000:
        raise JustificationError('Valor maximo e R$ 100.000,00.')

    can_change, error_msg = validate_sale_can_be_changed(seller, date, user)
    if not can_change:
        raise JustificationLockedError(error_msg or _LOCKED_MSG)
    _assert_period_unlocked(seller, date, user)

    if _has_active_manual_sale(tenant, seller, date):
        raise JustificationConflictError(
            'Este dia ja possui venda registrada.'
        )

    justification_snapshot = {
        'uuid': str(justification.uuid),
        'date': date.isoformat(),
        'reason': justification.reason,
        'notes': justification.notes,
    }

    with transaction.atomic():
        log_action(
            user, 'day_justification.replaced_by_sale',
            instance=justification,
            changes={
                'justification_before': justification_snapshot,
                'sale_amount': amount,
                'sale_date': date.isoformat(),
                'seller': str(seller.uuid),
            },
        )
        justification.delete()
        sale = Sale.objects.create(
            tenant=tenant, seller=seller, origin=Sale.Origin.MANUAL,
            status='ATIVA', amount=amount, sale_date=date,
            notes=(notes or '').strip() or None, created_by=user,
        )
        log_action(user, 'sale.created', instance=sale)
        ensure_seller_commission(seller, date)
    return sale
