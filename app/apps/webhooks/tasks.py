import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from app.apps.webhooks.models import WebhookEvent
from app.apps.payments.models import Payment
from app.apps.orders.models import Order, PaymentLink
from app.apps.sales.models import Sale

logger = logging.getLogger(__name__)


@shared_task(
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=30,
)
def process_pagarme_webhook(event_id):
    with transaction.atomic():
        try:
            event = WebhookEvent.objects.select_for_update(nowait=True).get(
                id=event_id, processed=False
            )
        except WebhookEvent.DoesNotExist:
            logger.info("Webhook %s already processed or not found", event_id)
            return

        payload = event.payload
        if not isinstance(payload, dict):
            raise ValueError("Payload is not a dictionary")

        event_type = payload.get('type')
        data = payload.get('data', {})
        logger.info("Processing webhook event %s type=%s", event_id, event_type)

        if event_type in ('order.paid', 'charge.paid'):
            order = None

            if event_type == 'order.paid':
                link_id = data.get('id')
                try:
                    payment_link = PaymentLink.objects.select_related('order').get(
                        gateway_link_id=link_id
                    )
                    order = payment_link.order
                except PaymentLink.DoesNotExist:
                    raise ValueError(
                        f"PaymentLink nao encontrado para gateway_link_id={link_id}"
                    )

            elif event_type == 'charge.paid':
                order_data = data.get('order', {})
                link_id = order_data.get('payment_link', {}).get('id') or data.get('payment_link_id')
                if link_id:
                    try:
                        payment_link = PaymentLink.objects.select_related('order').get(
                            gateway_link_id=link_id
                        )
                        order = payment_link.order
                    except PaymentLink.DoesNotExist:
                        pass

                if not order:
                    gateway_txn_id = data.get('id')
                    try:
                        payment = Payment.objects.select_related('order').get(
                            gateway_transaction_id=gateway_txn_id
                        )
                        order = payment.order
                    except Payment.DoesNotExist:
                        raise ValueError(
                            f"Nao foi possivel identificar Order para charge.paid "
                            f"(gateway_transaction_id={gateway_txn_id})"
                        )

            if not order:
                raise ValueError("Order nao encontrada para o webhook")

            payment = order.payments.order_by('created_at').first()
            if not payment:
                raise ValueError(
                    f"Payment nao encontrado para a Order {order.uuid}",
                )

            if event_type == 'order.paid':
                payment.gateway_order_id = data.get('id')
                charges = data.get('charges', [])
                if charges and isinstance(charges, list):
                    payment.gateway_transaction_id = charges[0].get('id')
            elif event_type == 'charge.paid':
                payment.gateway_transaction_id = data.get('id')
                payment.gateway_order_id = data.get('order', {}).get('id')

            if payment.status == Payment.Status.PAID:
                logger.info(
                    "Payment %s already PAID, skipping duplicate webhook",
                    payment.id,
                )
            else:
                payment.status = Payment.Status.PAID
                payment.raw_callback_payload = payload
                payment.save()

                order.status = Order.Status.COMPLETED
                order.save()

                Sale.objects.get_or_create(
                    order=order,
                    defaults={
                        'tenant': order.tenant,
                        'seller': order.seller,
                        'origin': Sale.Origin.LINK,
                        'amount': order.total_amount,
                        'sale_date': timezone.localdate(),
                    },
                )

                if order.seller:
                    from app.apps.notifications.tasks import notify_seller_link_status
                    notify_seller_link_status(order.seller, order, 'payment_paid')

        elif event_type in ('charge.payment_failed', 'order.payment_failed'):
            order = None
            gateway_txn_id = data.get('id')
            if gateway_txn_id:
                try:
                    payment = Payment.objects.select_related('order').get(
                        gateway_transaction_id=gateway_txn_id
                    )
                    order = payment.order
                except Payment.DoesNotExist:
                    pass
            if not order:
                link_id = data.get('order', {}).get('payment_link', {}).get('id')
                if link_id:
                    try:
                        payment_link = PaymentLink.objects.select_related('order').get(
                            gateway_link_id=link_id
                        )
                        order = payment_link.order
                    except PaymentLink.DoesNotExist:
                        pass
            if not order:
                raise ValueError("Order nao encontrada para payment_failed")
            payment = order.payments.order_by('created_at').first()
            if not payment:
                raise ValueError(f"Payment nao encontrado para Order {order.uuid}")
            payment.gateway_transaction_id = data.get('id')
            payment.status = Payment.Status.FAILED
            payment.raw_callback_payload = payload
            payment.save()
            if order.seller:
                last_txn = data.get('last_transaction') or {}
                motivo = (
                    last_txn.get('acquirer_message')
                    or last_txn.get('refusal_reason')
                    or ''
                )
                from app.apps.notifications.tasks import notify_seller_link_status
                notify_seller_link_status(
                    order.seller, order, 'payment_failed', motivo=motivo,
                )

        elif event_type == 'charge.refunded':
            gateway_txn_id = data.get('id')
            order = None
            if gateway_txn_id:
                try:
                    payment = Payment.objects.select_related('order').get(
                        gateway_transaction_id=gateway_txn_id
                    )
                    order = payment.order
                except Payment.DoesNotExist:
                    raise ValueError(
                        f"Payment nao encontrado para charge.refunded "
                        f"(gateway_transaction_id={gateway_txn_id})"
                    )
            if not order:
                raise ValueError("Order nao encontrada para charge.refunded")
            payment.status = Payment.Status.REFUNDED
            payment.raw_callback_payload = payload
            payment.save()
            if order.seller:
                from app.apps.notifications.tasks import notify_seller_link_status
                notify_seller_link_status(order.seller, order, 'payment_refunded')

        elif event_type in ('payment-link.expired', 'payment-link.cancelled'):
            link_id = data.get('id')
            if link_id:
                try:
                    payment_link = PaymentLink.objects.select_related('order__seller').get(
                        gateway_link_id=link_id
                    )
                    order = payment_link.order
                except PaymentLink.DoesNotExist:
                    logger.warning(
                        "PaymentLink nao encontrado para %s gateway_link_id=%s",
                        event_type, link_id,
                    )
                    event.processed = True
                    event.save(update_fields=['processed'])
                    return
            else:
                raise ValueError(f"{event_type}: gateway_link_id ausente no payload")

            if event_type == 'payment-link.expired':
                if order.status == Order.Status.EXPIRED:
                    logger.info("Order %s already EXPIRED, skipping", order.uuid)
                else:
                    order.status = Order.Status.EXPIRED
                    order.save(update_fields=['status'])
                    if order.seller:
                        from app.apps.notifications.tasks import notify_seller_link_status
                        notify_seller_link_status(order.seller, order, 'payment_expired')

            elif event_type == 'payment-link.cancelled':
                if order.status == Order.Status.CANCELED:
                    logger.info("Order %s already CANCELED, skipping", order.uuid)
                else:
                    order.status = Order.Status.CANCELED
                    order.save(update_fields=['status'])
                    if order.seller:
                        from app.apps.notifications.tasks import notify_seller_link_status
                        notify_seller_link_status(order.seller, order, 'link_canceled')

        elif event_type == 'charge.chargedback':
            gateway_txn_id = data.get('id')
            order = None
            if gateway_txn_id:
                try:
                    payment = Payment.objects.select_related('order__seller').get(
                        gateway_transaction_id=gateway_txn_id
                    )
                    order = payment.order
                except Payment.DoesNotExist:
                    logger.warning(
                        "Payment nao encontrado para charge.chargedback "
                        "gateway_transaction_id=%s", gateway_txn_id,
                    )
                    event.processed = True
                    event.save(update_fields=['processed'])
                    return
            if not order:
                raise ValueError("Order nao encontrada para charge.chargedback")
            if payment.status == Payment.Status.CHARGEBACK:
                logger.info("Payment %s already CHARGEBACK, skipping", payment.id)
            else:
                payment.status = Payment.Status.CHARGEBACK
                payment.raw_callback_payload = payload
                payment.save(update_fields=['status', 'raw_callback_payload'])
                if order.seller:
                    from app.apps.notifications.tasks import notify_seller_link_status
                    notify_seller_link_status(order.seller, order, 'payment_refunded')

        event.processed = True
        event.save(update_fields=['processed'])
