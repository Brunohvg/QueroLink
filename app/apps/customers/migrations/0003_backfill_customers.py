import hashlib

from django.db import migrations


def _hash(value):
    if not value:
        return ""
    return hashlib.sha256(str(value).strip().lower().encode("utf-8")).hexdigest()


def _digits(value):
    return "".join(filter(str.isdigit, value or ""))


def _customer(
    Customer,
    tenant_id,
    name,
    email="",
    phone="",
    document="",
    document_type="",
    address=None,
):
    email = (email or "").strip().lower()
    phone = _digits(phone)
    document = _digits(document)
    hashes = {
        "document_hash": _hash(document),
        "email_hash": _hash(email),
        "phone_hash": _hash(phone),
        "name_hash": _hash(name),
    }
    customer = None
    identity_fields = [
        field
        for field in ("document_hash", "email_hash", "phone_hash")
        if hashes[field]
    ] or ["name_hash"]
    for field in identity_fields:
        if hashes[field]:
            customer = Customer.objects.filter(
                tenant_id=tenant_id,
                **{field: hashes[field]},
            ).first()
            if customer:
                break
    if customer is None:
        customer = Customer(tenant_id=tenant_id)
    customer.name = name or ""
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
    for field, value in (address or {}).items():
        if value:
            setattr(customer, field, value)
    customer.save()
    return customer


def forwards(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")
    Activity = apps.get_model("customers", "CustomerActivity")
    Boleto = apps.get_model("receivables", "Boleto")
    Order = apps.get_model("orders", "Order")
    for boleto in Boleto.objects.filter(status="PAGO").select_related("seller").iterator():
        customer = _customer(
            Customer,
            boleto.tenant_id,
            boleto.payer_name,
            boleto.payer_email,
            boleto.payer_phone,
            boleto.payer_document,
            boleto.payer_document_type,
            {
                "zip_code": boleto.payer_zip_code,
                "street": boleto.payer_street,
                "number": boleto.payer_number,
                "complement": boleto.payer_complement,
                "neighborhood": boleto.payer_neighborhood,
                "city": boleto.payer_city,
                "state": boleto.payer_state,
            },
        )
        Activity.objects.create(
            customer=customer,
            source="BOLETO",
            source_uuid=boleto.uuid,
            seller_name=boleto.seller.name,
            amount_cents=boleto.amount_cents,
            status=boleto.status,
            occurred_at=boleto.created_at,
        )
    for order in Order.objects.filter(status="COMPLETED").select_related("seller").iterator():
        customer = _customer(
            Customer,
            order.tenant_id,
            order.customer_name,
            phone=order.customer_phone or "",
        )
        Activity.objects.create(
            customer=customer,
            source="PAYMENT_LINK",
            source_uuid=order.uuid,
            seller_name=order.seller.name if order.seller else "",
            amount_cents=order.total_amount,
            status=order.status,
            occurred_at=order.created_at,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0002_customer_name_hash"),
        ("orders", "0003_alter_paymentlink_gateway_link_id"),
        ("receivables", "0002_boleto_invoice_pdf_boleto_invoice_uploaded_at_and_more"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
