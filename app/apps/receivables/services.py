from datetime import timedelta

import requests
from django.core.cache import cache
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


def apply_reconciliation_result(boleto, result):
    now = timezone.now()
    if (
        result.status in (ProviderStatus.PAID, ProviderStatus.REFUNDED)
        and boleto.status == Boleto.Status.CRIANDO
    ):
        with transaction.atomic():
            recovered = Boleto.objects.select_for_update().get(pk=boleto.pk)
            if recovered.status == Boleto.Status.CRIANDO:
                recovered.provider_order_id = (
                    result.order_id or recovered.provider_order_id
                )
                recovered.provider_charge_id = (
                    result.charge_id or recovered.provider_charge_id
                )
                recovered.transition_to(Boleto.Status.PENDENTE)
                recovered.save(update_fields=[
                    'provider_order_id', 'provider_charge_id', 'status', 'updated_at',
                ])
            boleto = recovered
    if result.status == ProviderStatus.PAID:
        reconciled, changed = mark_paid(
            boleto,
            result.paid_amount_cents or boleto.amount_cents,
            result.paid_at or now,
        )
    elif result.status == ProviderStatus.REFUNDED:
        current = Boleto.objects.get(pk=boleto.pk)
        if current.status not in (Boleto.Status.PAGO, Boleto.Status.ESTORNADO):
            current, _ = mark_paid(
                current,
                result.paid_amount_cents or current.amount_cents,
                result.paid_at or now,
            )
        reconciled, changed = mark_refunded(
            current,
            refunded_amount_cents=result.paid_amount_cents,
        )
    else:
        with transaction.atomic():
            reconciled = Boleto.objects.select_for_update().get(pk=boleto.pk)
            changed = False
            if result.order_id and not reconciled.provider_order_id:
                reconciled.provider_order_id = result.order_id
                changed = True
            if result.charge_id and not reconciled.provider_charge_id:
                reconciled.provider_charge_id = result.charge_id
                changed = True
            if (
                result.status == ProviderStatus.PENDING
                and reconciled.status == Boleto.Status.CRIANDO
            ):
                reconciled.transition_to(Boleto.Status.PENDENTE)
                changed = True
            elif (
                result.status == ProviderStatus.OVERDUE
                and reconciled.status == Boleto.Status.PENDENTE
            ):
                reconciled.transition_to(Boleto.Status.VENCIDO)
                changed = True
            elif (
                result.status == ProviderStatus.CANCELED
                and reconciled.status == Boleto.Status.CANCEL_PEND
            ):
                reconciled.transition_to(Boleto.Status.CANCELADO)
                _outbox_event(
                    reconciled,
                    'boleto.canceled',
                    {'boleto_uuid': str(reconciled.uuid)},
                )
                changed = True
            elif (
                result.status == ProviderStatus.FAILED
                and reconciled.status in (Boleto.Status.CRIANDO, Boleto.Status.PENDENTE)
            ):
                reconciled.transition_to(Boleto.Status.FALHOU)
                changed = True
            reconciled.last_provider_status = result.status.value
            reconciled.last_synced_at = now
            reconciled.save(update_fields=[
                'provider_order_id', 'provider_charge_id', 'status',
                'last_provider_status', 'last_synced_at', 'updated_at',
            ])
            return reconciled, changed

    update_kwargs = {
        'provider_order_id': result.order_id or reconciled.provider_order_id,
        'provider_charge_id': result.charge_id or reconciled.provider_charge_id,
        'last_provider_status': result.status.value,
        'last_synced_at': now,
    }
    if result.barcode and not reconciled.provider_barcode:
        update_kwargs['provider_barcode'] = result.barcode[:255]
    if result.url and not reconciled.provider_url:
        update_kwargs['provider_url'] = result.url[:500]
    Boleto.objects.filter(pk=reconciled.pk).update(**update_kwargs)
    reconciled.refresh_from_db()
    return reconciled, changed


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


def lookup_cnpj(cnpj):
    from app.apps.sellers.validators import normalize_and_validate_cpf
    digits = cnpj.replace('.', '').replace('/', '').replace('-', '')
    if len(digits) != 14:
        raise ValueError('CNPJ invalido.')

    cache_key = f'boletos:cnpj:{digits}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        resp = requests.get(
            f'https://brasilapi.com.br/api/cnpj/v1/{digits}',
            headers={'User-Agent': 'Merito/1.0'},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        raise ValueError(
            'Nao foi possivel consultar o CNPJ. Preencha os dados manualmente.'
        ) from e

    result = {
        'payer_name': raw.get('razao_social') or raw.get('nome_fantasia') or '',
        'payer_zip_code': ''.join(filter(str.isdigit, raw.get('cep') or '')),
        'payer_street': raw.get('logradouro') or '',
        'payer_number': raw.get('numero') or '',
        'payer_complement': raw.get('complemento') or '',
        'payer_neighborhood': raw.get('bairro') or '',
        'payer_city': raw.get('municipio') or '',
        'payer_state': raw.get('uf') or '',
        'source': 'brasilapi_cnpj',
    }
    cache.set(cache_key, result, 3600 * 24)
    return result


def lookup_cep(cep):
    digits = ''.join(filter(str.isdigit, cep))
    if len(digits) != 8:
        raise ValueError('CEP deve ter 8 digitos.')

    cache_key = f'boletos:cep:{digits}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        resp = requests.get(
            f'https://brasilapi.com.br/api/cep/v1/{digits}',
            headers={'User-Agent': 'Merito/1.0'},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception:
        raise ValueError('Nao foi possivel consultar o CEP.')

    result = {
        'payer_zip_code': digits,
        'payer_street': raw.get('street') or '',
        'payer_neighborhood': raw.get('neighborhood') or '',
        'payer_city': raw.get('city') or '',
        'payer_state': raw.get('state') or '',
        'source': 'brasilapi_cep',
    }
    cache.set(cache_key, result, 3600 * 24)
    return result


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
        existing = Boleto.objects.filter(
            tenant=tenant, idempotency_key=key,
        ).select_for_update().first()
        if existing:
            compare_fields = {
                k for k in data.keys()
                if k not in ('status',)
            }
            for field in compare_fields:
                existing_val = getattr(existing, field, None)
                new_val = data.get(field)
                if str(existing_val) != str(new_val) or (
                    existing_val is None and new_val is not None
                ):
                    raise ValidationError(
                        {'idempotency_key': 'Chave de idempotencia ja utilizada com dados diferentes.'}
                    )
            return existing

        boleto = Boleto(
            tenant=tenant,
            idempotency_key=key,
            **defaults,
        )
        boleto.full_clean()
        boleto.save()

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
            locked.provider_barcode = (result.barcode or '')[:255]
            locked.provider_url = (result.url or '')[:500]
            locked.transition_to(Boleto.Status.PENDENTE)
            locked.save(update_fields=[
                'provider',
                'provider_order_id',
                'provider_charge_id',
                'last_provider_status',
                'last_synced_at',
                'operation_error_code',
                'operation_error_message',
                'provider_barcode',
                'provider_url',
                'status',
                'updated_at',
            ])
            if locked.provider_barcode or locked.provider_url:
                _outbox_event(
                    locked,
                    'boleto.created',
                    {'boleto_uuid': str(locked.uuid)},
                )
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
