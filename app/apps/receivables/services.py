import logging
from datetime import datetime
from pathlib import Path

import requests
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from app.apps.audit.models import AuditLog

from .models import Boleto
from .providers import BoletoProviderError, get_provider

logger = logging.getLogger(__name__)


class BoletoServiceError(Exception):
    pass


def lookup_cnpj(cnpj):
    from app.apps.sellers.validators import validate_cnpj

    digits = validate_cnpj(cnpj)
    cache_key = f'boletos:cnpj:{digits}'
    cached = cache.get(cache_key)
    if cached:
        return cached
    try:
        response = requests.get(
            f'https://brasilapi.com.br/api/cnpj/v1/{digits}',
            headers={'User-Agent': 'Merito/1.0'},
            timeout=10,
        )
        response.raise_for_status()
        raw = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BoletoServiceError(
            'Nao foi possivel consultar o CNPJ. Preencha os dados manualmente.'
        ) from exc
    result = {
        'payer_name': raw.get('razao_social') or raw.get('nome_fantasia') or '',
        'payer_zip_code': ''.join(filter(str.isdigit, raw.get('cep') or '')),
        'payer_street': raw.get('logradouro') or '',
        'payer_number': raw.get('numero') or '',
        'payer_complement': raw.get('complemento') or '',
        'payer_neighborhood': raw.get('bairro') or '',
        'payer_city': raw.get('municipio') or '',
        'payer_state': raw.get('uf') or '',
    }
    cache.set(cache_key, result, 60 * 60 * 24 * 7)
    return result


def create_boleto(tenant, seller, created_by, data):
    boleto = Boleto(
        tenant=tenant,
        seller=seller,
        created_by=created_by,
        **data,
    )
    boleto.full_clean()
    provider = get_provider(tenant)
    try:
        with transaction.atomic():
            boleto.save()
            provider_data = {
                field.name: getattr(boleto, field.name)
                for field in Boleto._meta.fields
            }
            result = provider.create(tenant, provider_data)
            if not result.order_id or not result.charge_id:
                raise BoletoServiceError(
                    'O emissor nao retornou os identificadores do boleto.'
                )
            boleto.gateway = result.gateway
            boleto.gateway_order_id = result.order_id
            boleto.gateway_charge_id = result.charge_id
            boleto.barcode = result.barcode
            boleto.boleto_url = result.url
            boleto.boleto_pdf_password = result.pdf_password
            boleto.save(update_fields=[
                'gateway', 'gateway_order_id', 'gateway_charge_id',
                'barcode', 'boleto_url', 'boleto_pdf_password', 'updated_at',
            ])
            transaction.on_commit(
                lambda boleto_uuid=boleto.uuid: _enqueue_created_email(boleto_uuid),
                robust=True,
            )
    except ValidationError:
        raise
    except BoletoServiceError:
        raise
    except BoletoProviderError as exc:
        raise BoletoServiceError(str(exc)) from exc
    except Exception as exc:
        logger.exception('Falha ao emitir boleto para tenant=%s', tenant.uuid)
        raise BoletoServiceError(
            'Nao foi possivel emitir o boleto. Tente novamente.'
        ) from exc
    return boleto


def cancel_boleto(boleto, user):
    if boleto.status != Boleto.Status.PENDENTE:
        raise BoletoServiceError('Somente boletos pendentes podem ser cancelados.')
    provider = get_provider(boleto.tenant)
    try:
        provider.cancel(boleto.tenant, boleto.gateway_charge_id)
    except Exception as exc:
        raise BoletoServiceError(
            'Nao foi possivel cancelar o boleto no emissor.'
        ) from exc
    with transaction.atomic():
        locked = Boleto.objects.select_for_update().get(pk=boleto.pk)
        if locked.status != Boleto.Status.PENDENTE:
            raise BoletoServiceError('Boleto nao esta mais pendente.')
        locked.status = Boleto.Status.CANCELADO
        locked.save(update_fields=['status', 'updated_at'])
        AuditLog.objects.create(
            user=user,
            tenant=locked.tenant,
            action='boleto.canceled',
            model_name='Boleto',
            object_id=str(locked.uuid),
            changes={'status': Boleto.Status.CANCELADO},
        )
    return locked


def _validate_invoice_file(upload, kind):
    limits = {'pdf': 10 * 1024 * 1024, 'xml': 2 * 1024 * 1024}
    allowed_types = {
        'pdf': {'application/pdf', 'application/octet-stream'},
        'xml': {'application/xml', 'text/xml', 'application/octet-stream'},
    }
    extension = Path(upload.name or '').suffix.lower()
    if extension != f'.{kind}':
        raise BoletoServiceError(f'O arquivo {kind.upper()} possui extensao invalida.')
    if upload.size > limits[kind]:
        limit_mb = limits[kind] // (1024 * 1024)
        raise BoletoServiceError(
            f'O arquivo {kind.upper()} deve ter no maximo {limit_mb} MB.'
        )
    content_type = (getattr(upload, 'content_type', '') or '').lower()
    if content_type not in allowed_types[kind]:
        raise BoletoServiceError(f'O arquivo {kind.upper()} possui tipo invalido.')
    signature = upload.read(8)
    upload.seek(0)
    if kind == 'pdf' and not signature.startswith(b'%PDF-'):
        raise BoletoServiceError('O arquivo informado nao e um PDF valido.')
    if kind == 'xml' and not signature.lstrip().startswith(b'<'):
        raise BoletoServiceError('O arquivo informado nao e um XML valido.')


def save_invoice_files(boleto, user, pdf=None, xml=None):
    if not pdf and not xml:
        raise BoletoServiceError('Selecione ao menos um arquivo PDF ou XML.')
    if pdf:
        _validate_invoice_file(pdf, 'pdf')
    if xml:
        _validate_invoice_file(xml, 'xml')
    old_files = []
    with transaction.atomic():
        locked = Boleto.objects.select_for_update().get(pk=boleto.pk)
        if pdf:
            if locked.invoice_pdf:
                old_files.append(locked.invoice_pdf)
            locked.invoice_pdf = pdf
        if xml:
            if locked.invoice_xml:
                old_files.append(locked.invoice_xml)
            locked.invoice_xml = xml
        locked.invoice_uploaded_at = timezone.now()
        locked.invoice_uploaded_by = user
        locked.save(update_fields=[
            'invoice_pdf', 'invoice_xml', 'invoice_uploaded_at',
            'invoice_uploaded_by', 'updated_at',
        ])
        AuditLog.objects.create(
            user=user,
            tenant=locked.tenant,
            action='boleto.invoice_uploaded',
            model_name='Boleto',
            object_id=str(locked.uuid),
            changes={'pdf': bool(pdf), 'xml': bool(xml)},
        )
        for old_file in old_files:
            transaction.on_commit(lambda file=old_file: file.delete(save=False))
    return locked


def mark_paid(boleto, paid_amount_cents, paid_at):
    if isinstance(paid_at, str):
        paid_at = datetime.fromisoformat(paid_at.replace('Z', '+00:00'))
    paid_at = paid_at or timezone.now()
    with transaction.atomic():
        locked = Boleto.objects.select_for_update().get(pk=boleto.pk)
        if locked.status == Boleto.Status.PAGO:
            return locked, False
        locked.status = Boleto.Status.PAGO
        locked.paid_amount_cents = int(paid_amount_cents or locked.amount_cents)
        locked.paid_at = paid_at
        locked.save(update_fields=[
            'status', 'paid_amount_cents', 'paid_at', 'updated_at',
        ])
        transaction.on_commit(
            lambda boleto_uuid=locked.uuid: _enqueue_paid_notification(boleto_uuid),
            robust=True,
        )
    return locked, True


def mark_refunded(boleto):
    with transaction.atomic():
        locked = Boleto.objects.select_for_update().get(pk=boleto.pk)
        if locked.status == Boleto.Status.ESTORNADO:
            return locked, False
        locked.status = Boleto.Status.ESTORNADO
        locked.save(update_fields=['status', 'updated_at'])
        transaction.on_commit(
            lambda boleto_uuid=locked.uuid: _enqueue_refund_notification(boleto_uuid),
            robust=True,
        )
    return locked, True


def _enqueue_created_email(boleto_uuid):
    from .tasks import send_boleto_email
    send_boleto_email.delay(str(boleto_uuid), 'created')


def _enqueue_paid_notification(boleto_uuid):
    from .tasks import notify_boleto_paid
    notify_boleto_paid.delay(str(boleto_uuid))


def _enqueue_refund_notification(boleto_uuid):
    from .tasks import notify_boleto_refunded
    notify_boleto_refunded.delay(str(boleto_uuid))
