import logging
from xml.etree import ElementTree

from django.db import transaction
from django.utils import timezone

from app.apps.audit.models import AuditLog

from .models import Boleto


logger = logging.getLogger(__name__)

PDF_MAX_BYTES = 10 * 1024 * 1024
XML_MAX_BYTES = 5 * 1024 * 1024


class InvalidInvoiceDocument(ValueError):
    pass


class InvoiceStorageError(Exception):
    pass


def _read_upload(uploaded_file, max_bytes):
    if uploaded_file.size > max_bytes:
        raise InvalidInvoiceDocument('Arquivo excede o tamanho maximo permitido.')
    content = uploaded_file.read(max_bytes + 1)
    uploaded_file.seek(0)
    if len(content) > max_bytes:
        raise InvalidInvoiceDocument('Arquivo excede o tamanho maximo permitido.')
    return content


def validate_invoice_document(document_type, uploaded_file):
    if document_type == 'pdf':
        content = _read_upload(uploaded_file, PDF_MAX_BYTES)
        if not content.startswith(b'%PDF-'):
            raise InvalidInvoiceDocument('PDF invalido.')
        return
    if document_type == 'xml':
        content = _read_upload(uploaded_file, XML_MAX_BYTES)
        lowered = content.lower()
        if b'<!doctype' in lowered or b'<!entity' in lowered:
            raise InvalidInvoiceDocument('XML inseguro.')
        try:
            ElementTree.fromstring(content)
        except (ElementTree.ParseError, ValueError) as exc:
            raise InvalidInvoiceDocument('XML invalido.') from exc
        return
    raise InvalidInvoiceDocument('Tipo de documento invalido.')


def _delete_old_file(storage, name):
    try:
        storage.delete(name)
    except Exception:
        logger.exception('Falha ao excluir documento fiscal substituido path=%s', name)


def upload_invoice_document(*, boleto, document_type, uploaded_file, user):
    validate_invoice_document(document_type, uploaded_file)
    field_name = f'invoice_{document_type}'
    uploaded_at_field = f'{field_name}_uploaded_at'
    uploaded_by_field = f'{field_name}_uploaded_by'
    new_name = None
    storage = Boleto._meta.get_field(field_name).storage

    try:
        with transaction.atomic():
            locked = Boleto.objects.select_for_update().get(
                pk=boleto.pk, tenant=boleto.tenant
            )
            field_file = getattr(locked, field_name)
            old_name = field_file.name
            field_file.save(uploaded_file.name, uploaded_file, save=False)
            new_name = field_file.name
            setattr(locked, uploaded_at_field, timezone.now())
            setattr(locked, uploaded_by_field, user)
            locked.save(update_fields=[
                field_name, uploaded_at_field, uploaded_by_field, 'updated_at'
            ])
            AuditLog.objects.create(
                user=user,
                tenant=locked.tenant,
                action='receivable.invoice_uploaded',
                model_name='Boleto',
                object_id=str(locked.uuid),
                changes={'document_type': document_type, 'replaced': bool(old_name)},
            )
            if old_name and old_name != new_name:
                transaction.on_commit(
                    lambda: _delete_old_file(storage, old_name),
                    robust=True,
                )
            return locked
    except InvalidInvoiceDocument:
        raise
    except Exception as exc:
        if new_name:
            _delete_old_file(storage, new_name)
        raise InvoiceStorageError('Falha ao armazenar documento fiscal.') from exc
