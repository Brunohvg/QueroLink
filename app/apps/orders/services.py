from django.db import transaction
from django.conf import settings
from app.apps.orders.models import Order, PaymentLink
from app.apps.payments.models import Payment
from app.services.gateway.pagar_me import PagarMeGateway


def create_payment_link(tenant, seller, customer_name, amount_cents, installments=1):
    # Remove acentos e caracteres especiais para evitar erro na API do Pagar.me
    import unicodedata
    safe_name = ''.join(
        c for c in unicodedata.normalize('NFD', customer_name)
        if not unicodedata.combining(c)
    )
    safe_name = safe_name.encode('ascii', errors='ignore').decode('ascii')

    with transaction.atomic():
        order = Order.objects.create(
            tenant=tenant,
            seller=seller,
            customer_name=safe_name,
            total_amount=amount_cents,
            status=Order.Status.PENDING,
        )

        success_url = f"https://{settings.SERVICE_FQDN_WEB}/pago/{order.uuid}/"
        gateway = PagarMeGateway(api_key=tenant.pagarme_api_key)
        response = gateway.create_payment_link(
            total_amount=amount_cents,
            max_installments=installments,
            name=safe_name,
            free_installments=installments,
            order_code=str(order.uuid),
            success_url=success_url,
        )

        link_url = response.get("url", "")
        gateway_link_id = response.get("id", "")

        if not link_url:
            raise Exception("Falha ao gerar o link de pagamento no Pagar.me.")

        payment = Payment.objects.create(
            order=order,
            gateway_name='pagarme',
            status=Payment.Status.PENDING,
            installments=installments,
        )

        payment_link = PaymentLink.objects.create(
            order=order,
            gateway_url=link_url,
            gateway_link_id=gateway_link_id,
        )

    return order, link_url
