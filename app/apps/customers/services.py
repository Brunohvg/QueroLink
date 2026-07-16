from django.db import transaction

from app.apps.accounts.fields import compute_hash

from .models import Customer, CustomerActivity


def _digits(value):
    return "".join(filter(str.isdigit, value or ""))


def sync_customer(
    tenant, *, name, email="", phone="", document="", document_type="", address=None
):
    email = (email or "").strip().lower()
    phone = _digits(phone)
    document = _digits(document)
    hashes = {
        "name_hash": compute_hash((name or "").strip()) or "",
        "document_hash": compute_hash(document) or "",
        "email_hash": compute_hash(email) or "",
        "phone_hash": compute_hash(phone) or "",
    }
    customer = None
    identity_fields = [
        field
        for field in ("document_hash", "email_hash", "phone_hash")
        if hashes[field]
    ] or ["name_hash"]
    for field in identity_fields:
        value = hashes[field]
        if value:
            customer = Customer.objects.filter(tenant=tenant, **{field: value}).first()
            if customer:
                break
    if customer is None:
        customer = Customer(tenant=tenant)
    customer.name = (name or "").strip()
    customer.name_hash = hashes["name_hash"]
    if email:
        customer.email = email
        customer.email_hash = hashes["email_hash"]
    if phone:
        customer.phone = phone
        customer.phone_hash = hashes["phone_hash"]
    if document:
        customer.document = document
        customer.document_hash = hashes["document_hash"]
        customer.document_type = document_type
    address = address or {}
    for target, source in (
        ("zip_code", "zip_code"),
        ("street", "street"),
        ("number", "number"),
        ("complement", "complement"),
        ("neighborhood", "neighborhood"),
        ("city", "city"),
        ("state", "state"),
    ):
        value = address.get(source)
        if value:
            setattr(customer, target, value)
    customer.save()
    return customer


def record_activity(
    customer, *, source, source_uuid, seller_name, amount_cents, status, occurred_at
):
    activity = CustomerActivity.objects.filter(
        customer__tenant=customer.tenant,
        source=source,
        source_uuid=source_uuid,
    ).first()
    if activity is None:
        activity = CustomerActivity(
            customer=customer, source=source, source_uuid=source_uuid
        )
    activity.customer = customer
    activity.seller_name = seller_name or ""
    activity.amount_cents = int(amount_cents or 0)
    activity.status = status or ""
    activity.occurred_at = occurred_at
    activity.save()
    return activity


@transaction.atomic
def sync_boleto_customer(boleto):
    customer = sync_customer(
        boleto.tenant,
        name=boleto.payer_name,
        email=boleto.payer_email,
        phone=boleto.payer_phone,
        document=boleto.payer_document,
        document_type=boleto.payer_document_type,
        address={
            "zip_code": boleto.payer_zip_code,
            "street": boleto.payer_street,
            "number": boleto.payer_number,
            "complement": boleto.payer_complement,
            "neighborhood": boleto.payer_neighborhood,
            "city": boleto.payer_city,
            "state": boleto.payer_state,
        },
    )
    record_activity(
        customer,
        source=CustomerActivity.Source.BOLETO,
        source_uuid=boleto.uuid,
        seller_name=boleto.seller.name,
        amount_cents=boleto.amount_cents,
        status=boleto.status,
        occurred_at=boleto.created_at,
    )
    return customer


@transaction.atomic
def sync_order_customer(order):
    customer = sync_customer(
        order.tenant,
        name=order.customer_name,
        phone=order.customer_phone or "",
    )
    record_activity(
        customer,
        source=CustomerActivity.Source.PAYMENT_LINK,
        source_uuid=order.uuid,
        seller_name=order.seller.name if order.seller else "",
        amount_cents=order.total_amount,
        status=order.status,
        occurred_at=order.created_at,
    )
    return customer
