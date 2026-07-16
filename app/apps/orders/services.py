import logging
import hashlib
import unicodedata

from django.db import transaction
from django.conf import settings

from app.apps.orders.models import Order, PaymentLink
from app.apps.payments.models import Payment
from app.services.gateway.pagar_me import PagarMeGateway, PagarMeError

logger = logging.getLogger(__name__)


def create_payment_link(tenant, seller, customer_name, amount_cents, installments=1):
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
        Payment.objects.create(
            order=order,
            gateway_name='pagarme',
            status=Payment.Status.PENDING,
            installments=installments,
        )

    success_url = f"https://{settings.SERVICE_FQDN_WEB}/pago/{order.uuid}/"
    gateway = PagarMeGateway(api_key=tenant.pagarme_api_key)
    expires_in = getattr(tenant, 'link_expires_in', None) or 1200
    pix_enabled = getattr(tenant, 'pix_enabled', True)

    try:
        response = gateway.create_payment_link(
            total_amount=amount_cents,
            max_installments=installments,
            name=safe_name,
            free_installments=installments,
            success_url=success_url,
            order_code=str(order.uuid),
            expires_in=expires_in,
            pix_enabled=pix_enabled,
        )
    except PagarMeError:
        order.status = Order.Status.CANCELED
        order.save(update_fields=['status', 'updated_at'])
        raise

    link_url = response.get("url", "")
    gateway_link_id = response.get("id", "")

    if not link_url:
        order.status = Order.Status.CANCELED
        order.save(update_fields=['status', 'updated_at'])
        raise PagarMeError("Falha ao gerar o link de pagamento no Pagar.me.")

    try:
        with transaction.atomic():
            PaymentLink.objects.create(
                order=order,
                gateway_url=link_url,
                gateway_link_id=gateway_link_id,
                short_code=hashlib.sha256(str(order.uuid).encode()).hexdigest()[:8].upper(),
            )
            from app.apps.customers.services import sync_order_customer
            sync_order_customer(order)
    except Exception:
        try:
            gateway.cancel_payment_link(gateway_link_id)
        except PagarMeError:
            logger.warning(
                "Nao foi possivel cancelar link orfao no Pagar.me: %s",
                gateway_link_id,
            )
        raise

    return order, link_url
