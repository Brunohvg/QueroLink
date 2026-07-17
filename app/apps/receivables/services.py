from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Boleto, IntegrationOutbox
from .providers import (
    ProviderDefinitiveError,
    ProviderInconclusiveError,
    ProviderStatus,
    ProviderTransientError,
    get_provider,
)


class BoletoServiceError(Exception):
    def __init__(self, message, *, boleto=None):
        super().__init__(message)
        self.boleto = boleto


def _outbox_event(boleto, event_type, payload):
    event, _ = IntegrationOutbox.objects.get_or_create(
        tenant=boleto.tenant,
        event_key=f'{event_type}:{boleto.uuid}',
        defaults={
            'aggregate_type': 'boleto',
            'aggregate_uuid': boleto.uuid,
            'event_type': event_type,
            'payload': payload,
        },
    )
    return event


def mark_paid(boleto, paid_amount_cents, paid_at):
    try:
        amount = int(paid_amount_cents)
    except (TypeError, ValueError) as exc:
        raise BoletoServiceError(
            'Dados de pagamento invalidos.', boleto=boleto
        ) from exc
    if amount <= 0 or paid_at is None:
        raise BoletoServiceError('Dados de pagamento invalidos.', boleto=boleto)

    with transaction.atomic():
        locked = Boleto.objects.select_for_update().get(pk=boleto.pk)
        if locked.status in (Boleto.Status.PAGO, Boleto.Status.ESTORNADO):
            return locked, False
        try:
            locked.transition_to(Boleto.Status.PAGO)
        except ValidationError as exc:
            raise BoletoServiceError(
                'Transicao de pagamento invalida.', boleto=locked
            ) from exc
        locked.paid_amount_cents = amount
        locked.paid_at = paid_at
        locked.operation_error_code = ''
        locked.operation_error_message = ''
        locked.save(update_fields=[
            'status', 'paid_amount_cents', 'paid_at', 'operation_error_code',
            'operation_error_message', 'updated_at',
        ])
        _outbox_event(locked, 'boleto.paid', {'boleto_uuid': str(locked.uuid)})
        return locked, True


def cancel_boleto(boleto):
    with transaction.atomic():
        locked = Boleto.objects.select_for_update().get(pk=boleto.pk)
        if locked.status == Boleto.Status.CANCELADO:
            return locked, False
        if locked.status != Boleto.Status.PENDENTE:
            raise BoletoServiceError('Boleto nao pode ser cancelado.', boleto=locked)
        locked.transition_to(Boleto.Status.CANCEL_PEND)
        locked.save(update_fields=['status', 'updated_at'])

    provider = get_provider(locked.tenant)
    try:
        result = provider.request_cancel(locked.tenant, locked.provider_charge_id)
    except ProviderDefinitiveError as exc:
        current = _finish_cancel_error(locked.pk, exc, revert=True)
        raise BoletoServiceError(
            'Cancelamento recusado pelo provider.', boleto=current
        ) from exc
    except (ProviderInconclusiveError, ProviderTransientError) as exc:
        current = _finish_cancel_error(locked.pk, exc, revert=False)
        raise BoletoServiceError(
            'Cancelamento aguardando reconciliacao.', boleto=current
        ) from exc

    if result.status == ProviderStatus.PAID:
        status = provider.retrieve_status(
            locked.tenant,
            order_id=locked.provider_order_id,
            charge_id=locked.provider_charge_id,
        )
        if status.status == ProviderStatus.PAID:
            return mark_paid(locked, locked.amount_cents, timezone.now())
        return Boleto.objects.get(pk=locked.pk), False

    if result.status != ProviderStatus.CANCELED:
        error = ProviderInconclusiveError('Status de cancelamento inconclusivo.')
        current = _finish_cancel_error(locked.pk, error, revert=False)
        raise BoletoServiceError(
            'Cancelamento aguardando reconciliacao.', boleto=current
        )

    with transaction.atomic():
        current = Boleto.objects.select_for_update().get(pk=locked.pk)
        if current.status == Boleto.Status.PAGO:
            return current, False
        if current.status == Boleto.Status.CANCEL_PEND:
            current.transition_to(Boleto.Status.CANCELADO)
            current.operation_error_code = ''
            current.operation_error_message = ''
            current.save(update_fields=[
                'status', 'operation_error_code', 'operation_error_message',
                'updated_at',
            ])
            _outbox_event(
                current, 'boleto.canceled', {'boleto_uuid': str(current.uuid)}
            )
            return current, True
        return current, False


def _finish_cancel_error(boleto_pk, error, *, revert):
    with transaction.atomic():
        locked = Boleto.objects.select_for_update().get(pk=boleto_pk)
        if locked.status == Boleto.Status.CANCEL_PEND:
            if revert:
                locked.transition_to(Boleto.Status.PENDENTE)
            locked.set_operation_error(error.code, str(error))
            locked.save(update_fields=[
                'status', 'operation_error_code', 'operation_error_message',
                'updated_at',
            ])
        return locked


def mark_refunded(
    boleto, *, refunded_at=None, refunded_amount_cents=None, reason='', chargeback=False
):
    with transaction.atomic():
        locked = Boleto.objects.select_for_update().get(pk=boleto.pk)
        if locked.status == Boleto.Status.ESTORNADO:
            return locked, False
        try:
            locked.transition_to(Boleto.Status.ESTORNADO)
        except ValidationError as exc:
            raise BoletoServiceError(
                'Boleto nao pode ser estornado.', boleto=locked
            ) from exc
        try:
            amount = (
                int(refunded_amount_cents)
                if refunded_amount_cents is not None
                else locked.paid_amount_cents
            )
        except (TypeError, ValueError) as exc:
            raise BoletoServiceError(
                'Valor de estorno invalido.', boleto=locked
            ) from exc
        if amount is not None and amount <= 0:
            raise BoletoServiceError('Valor de estorno invalido.', boleto=locked)
        locked.refunded_at = refunded_at or timezone.now()
        locked.refunded_amount_cents = amount
        locked.refund_reason = str(reason or '')[:255]
        locked.save(update_fields=[
            'status', 'refunded_at', 'refunded_amount_cents', 'refund_reason',
            'updated_at',
        ])
        event_type = 'boleto.chargeback' if chargeback else 'boleto.refunded'
        _outbox_event(locked, event_type, {'boleto_uuid': str(locked.uuid)})
        return locked, True


def claim_outbox_events(limit=100, *, event_types=None):
    safe_limit = max(1, min(int(limit), 1000))
    now = timezone.now()
    with transaction.atomic():
        queryset = IntegrationOutbox.objects.select_for_update(skip_locked=True).filter(
            status__in=[IntegrationOutbox.Status.PENDING, IntegrationOutbox.Status.FAILED],
            available_at__lte=now,
        )
        if event_types is not None:
            queryset = queryset.filter(event_type__in=event_types)
        events = list(queryset.order_by('available_at', 'created_at')[:safe_limit])
        for event in events:
            event.status = IntegrationOutbox.Status.PROCESSING
            event.attempt_count += 1
            event.processing_started_at = now
            event.last_error = ''
            event.save(update_fields=[
                'status', 'attempt_count', 'processing_started_at', 'last_error',
                'updated_at',
            ])
    return events


def complete_outbox_event(event_uuid):
    with transaction.atomic():
        event = IntegrationOutbox.objects.select_for_update().get(pk=event_uuid)
        event.status = IntegrationOutbox.Status.PROCESSED
        event.processed_at = timezone.now()
        event.last_error = ''
        event.save(update_fields=['status', 'processed_at', 'last_error', 'updated_at'])
        return event


def fail_outbox_event(event_uuid, error, retry_delay_seconds=60):
    delay = max(0, min(int(retry_delay_seconds), 86400))
    with transaction.atomic():
        event = IntegrationOutbox.objects.select_for_update().get(pk=event_uuid)
        event.status = IntegrationOutbox.Status.FAILED
        event.available_at = timezone.now() + timedelta(seconds=delay)
        event.last_error = IntegrationOutbox.sanitize_error(error)
        event.save(update_fields=['status', 'available_at', 'last_error', 'updated_at'])
        return event


def create_boleto(tenant, seller, created_by, data, idempotency_key):
    key = str(idempotency_key or '').strip()
    if not key:
        raise ValidationError({'idempotency_key': 'Chave de idempotencia obrigatoria.'})

    defaults = dict(data)
    defaults.update({
        'seller': seller,
        'created_by': created_by,
        'status': Boleto.Status.CRIANDO,
    })
    with transaction.atomic():
        boleto, created = Boleto.objects.get_or_create(
            tenant=tenant,
            idempotency_key=key,
            defaults=defaults,
        )
        if created:
            boleto.full_clean()
            boleto.save()

    if not created:
        return boleto

    provider = get_provider(tenant)
    provider_data = {
        field.name: getattr(boleto, field.name)
        for field in Boleto._meta.fields
    }
    try:
        result = provider.create(tenant, provider_data, key)
    except ProviderDefinitiveError as exc:
        failed = _record_failure(boleto.pk, exc, mark_failed=True)
        raise BoletoServiceError(
            'Nao foi possivel emitir o boleto.', boleto=failed
        ) from exc
    except (ProviderInconclusiveError, ProviderTransientError) as exc:
        inconclusive = _record_failure(boleto.pk, exc, mark_failed=False)
        raise BoletoServiceError(
            'Emissao enviada e aguardando reconciliacao.', boleto=inconclusive
        ) from exc

    if not result.order_id or not result.charge_id:
        error = ProviderDefinitiveError(
            'Provider nao retornou os identificadores da cobranca.'
        )
        failed = _record_failure(boleto.pk, error, mark_failed=True)
        raise BoletoServiceError(
            'Nao foi possivel emitir o boleto.', boleto=failed
        ) from error

    with transaction.atomic():
        locked = Boleto.objects.select_for_update().get(pk=boleto.pk)
        if locked.status == Boleto.Status.CRIANDO:
            locked.provider = result.provider
            locked.provider_order_id = result.order_id
            locked.provider_charge_id = result.charge_id
            locked.last_provider_status = result.status.value
            locked.last_synced_at = timezone.now()
            locked.operation_error_code = ''
            locked.operation_error_message = ''
            locked.transition_to(Boleto.Status.PENDENTE)
            locked.save(update_fields=[
                'provider',
                'provider_order_id',
                'provider_charge_id',
                'last_provider_status',
                'last_synced_at',
                'operation_error_code',
                'operation_error_message',
                'status',
                'updated_at',
            ])
    return locked


def _record_failure(boleto_pk, error, *, mark_failed):
    with transaction.atomic():
        locked = Boleto.objects.select_for_update().get(pk=boleto_pk)
        if locked.status == Boleto.Status.CRIANDO:
            if mark_failed:
                locked.transition_to(Boleto.Status.FALHOU)
            locked.set_operation_error(error.code, str(error))
            locked.save(update_fields=[
                'status',
                'operation_error_code',
                'operation_error_message',
                'updated_at',
            ])
    return locked
