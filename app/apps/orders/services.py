from django.db import transaction
from django.conf import settings
from app.apps.orders.models import Order, PaymentLink
from app.apps.payments.models import Payment
from app.services.gateway.pagar_me import PagarMeGateway


def create_payment_link(tenant, seller, customer_name, amount_cents, installments=1):
    with transaction.atomic():
        order = Order.objects.create(
            tenant=tenant,
            seller=seller,
            customer_name=customer_name,
            total_amount=amount_cents,
            status=Order.Status.PENDING,
        )

        success_url = f"https://{settings.SERVICE_FQDN_WEB}/pago/{order.uuid}/"
        gateway = PagarMeGateway(api_key=tenant.pagarme_api_key)
        response = gateway.create_payment_link(
            total_amount=amount_cents,
            max_installments=installments,
            name=customer_name,
            free_installments=installments,
            order_code=str(order.uuid),
            success_url=success_url,
        )

        link_url = response.get("url", "")
        gateway_id = response.get("id", "")

        if not link_url:
            raise Exception("Falha ao gerar o link de pagamento no Pagar.me.")

        payment = Payment.objects.create(
            order=order,
            gateway_name='pagarme',
            gateway_transaction_id=gateway_id,
            status=Payment.Status.PENDING,
            installments=installments,
        )

        payment_link = PaymentLink.objects.create(
            order=order,
            gateway_url=link_url,
            gateway_link_id=gateway_id,
        )

    return order, link_url
