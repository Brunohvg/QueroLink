from django.db import IntegrityError, transaction

from app.apps.accounts.fields import compute_hash

from .models import Customer, CustomerActivity, CustomerIdentityConflict


def _digits(value):
    return ''.join(filter(str.isdigit, value or ''))


def _normalized_identity(*, name, email, phone, document):
    normalized = {
        'name': (name or '').strip(),
        'email': (email or '').strip().lower(),
        'phone': _digits(phone),
        'document': _digits(document),
    }
    hashes = {
        f'{field}_hash': compute_hash(value) or ''
        for field, value in normalized.items()
    }
    return normalized, hashes


def _unique_match(tenant, field, value):
    if not value:
        return None, False
    matches = list(Customer.objects.filter(
        tenant=tenant, **{field: value}
    ).order_by('uuid')[:2])
    return (matches[0], False) if len(matches) == 1 else (None, len(matches) > 1)


def _record_conflicts(*, tenant, selected, candidates, hashes, source, source_uuid):
    for identifier_type, candidate in candidates.items():
        if candidate is None or candidate.pk == selected.pk:
            continue
        CustomerIdentityConflict.objects.get_or_create(
            tenant=tenant,
            selected_customer=selected,
            conflicting_customer=candidate,
            identifier_type=identifier_type,
            identifier_hash=hashes[f'{identifier_type.lower()}_hash'],
            source=source,
            source_uuid=source_uuid,
        )


def resolve_customer(
    tenant,
    *,
    name,
    email='',
    phone='',
    document='',
    document_type='',
    source,
    source_uuid,
):
    normalized, hashes = _normalized_identity(
        name=name, email=email, phone=phone, document=document
    )
    candidates = {}
    ambiguous = set()
    for identifier_type, field in (
        ('DOCUMENT', 'document_hash'),
        ('EMAIL', 'email_hash'),
        ('PHONE', 'phone_hash'),
    ):
        candidate, is_ambiguous = _unique_match(tenant, field, hashes[field])
        candidates[identifier_type] = candidate
        if is_ambiguous:
            ambiguous.add(identifier_type)

    selected = candidates['DOCUMENT']
    if selected is None and not normalized['document']:
        weak_candidates = {
            candidate.pk: candidate
            for key, candidate in candidates.items()
            if key in ('EMAIL', 'PHONE') and candidate is not None
        }
        if len(weak_candidates) == 1 and not ambiguous:
            candidate = next(iter(weak_candidates.values()))
            if not candidate.document_hash:
                selected = candidate

    if selected is None:
        customer = Customer(
            tenant=tenant,
            name=normalized['name'],
            email=normalized['email'],
            phone=normalized['phone'],
            document=normalized['document'],
            document_type=document_type or '',
            **hashes,
        )
        if hashes['document_hash']:
            try:
                with transaction.atomic():
                    customer.save(force_insert=True)
            except IntegrityError:
                customer = Customer.objects.get(
                    tenant=tenant, document_hash=hashes['document_hash']
                )
        else:
            customer.save(force_insert=True)
        selected = customer
    else:
        selected.name = normalized['name'] or selected.name
        selected.name_hash = hashes['name_hash'] or selected.name_hash
        if normalized['email'] and (
            candidates['EMAIL'] is None or candidates['EMAIL'].pk == selected.pk
        ):
            selected.email = normalized['email']
            selected.email_hash = hashes['email_hash']
        if normalized['phone'] and (
            candidates['PHONE'] is None or candidates['PHONE'].pk == selected.pk
        ):
            selected.phone = normalized['phone']
            selected.phone_hash = hashes['phone_hash']
        if normalized['document'] and not selected.document_hash:
            selected.document = normalized['document']
            selected.document_hash = hashes['document_hash']
            selected.document_type = document_type or ''
        selected.save()

    _record_conflicts(
        tenant=tenant,
        selected=selected,
        candidates=candidates,
        hashes=hashes,
        source=source,
        source_uuid=source_uuid,
    )
    return selected


@transaction.atomic
def project_boleto(boleto):
    customer = resolve_customer(
        boleto.tenant,
        name=boleto.payer_name,
        email=boleto.payer_email,
        phone=boleto.payer_phone,
        document=boleto.payer_document,
        document_type=boleto.payer_document_type,
        source=CustomerActivity.Source.BOLETO,
        source_uuid=boleto.uuid,
    )
    activity, _ = CustomerActivity.objects.update_or_create(
        tenant=boleto.tenant,
        source=CustomerActivity.Source.BOLETO,
        source_uuid=boleto.uuid,
        defaults={
            'customer': customer,
            'seller_name': boleto.seller.name,
            'amount_cents': boleto.paid_amount_cents or boleto.amount_cents,
            'status': boleto.status,
            'occurred_at': boleto.paid_at or boleto.updated_at,
        },
    )
    return activity


def project_outbox_event(event):
    from app.apps.receivables.models import Boleto

    boleto_uuid = (event.payload or {}).get('boleto_uuid')
    if not boleto_uuid:
        boleto_uuid = event.aggregate_uuid
    boleto = Boleto.objects.select_related('tenant', 'seller').get(
        pk=boleto_uuid, tenant=event.tenant
    )
    return project_boleto(boleto)
