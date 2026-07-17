from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Boleto
from .providers import (
    ProviderDefinitiveError,
    ProviderInconclusiveError,
    ProviderTransientError,
    get_provider,
)


class BoletoServiceError(Exception):
    def __init__(self, message, *, boleto=None):
        super().__init__(message)
        self.boleto = boleto


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
