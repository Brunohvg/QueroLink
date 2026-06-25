import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from app.apps.webhooks.models import WebhookEvent
from app.apps.payments.models import Payment
from app.apps.orders.models import Order
from app.apps.sales.models import Sale

logger = logging.getLogger(__name__)


@shared_task(
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=30,
)
def process_pagarme_webhook(event_id):
    try:
        event = WebhookEvent.objects.get(id=event_id)
    except WebhookEvent.DoesNotExist:
        logger.warning("Webhook event %s not found", event_id)
        return

    if event.processed:
        logger.info("Webhook event %s already processed, skipping", event_id)
        return

    try:
        payload = event.payload
        if not isinstance(payload, dict):
            raise ValueError("Payload is not a dictionary")

        event_type = payload.get('type')
        data = payload.get('data', {})
        logger.info("Processing webhook event %s type=%s", event_id, event_type)

        if event_type in ('order.paid', 'charge.paid'):
            order_uuid = data.get('code')
            charge_data = data if event_type == 'charge.paid' else None

            if event_type == 'charge.paid' and not order_uuid:
                order_obj = data.get('order', {})
                order_uuid = order_obj.get('code')

            if not order_uuid:
                raise ValueError(
                    "order_code nao encontrado no payload (campo 'code')",
                )

            try:
                order = Order.objects.get(uuid=order_uuid)
            except Order.DoesNotExist:
                raise ValueError(
                    f"Order nao encontrada para o code {order_uuid}",
                )

            with transaction.atomic():
                payment = order.payments.order_by('created_at').first()
                if not payment:
                    raise ValueError(
                        f"Payment nao encontrado para a Order {order_uuid}",
                    )

                gateway_order_id = data.get('id')
                if gateway_order_id:
                    payment.gateway_order_id = gateway_order_id

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

        event.processed = True
        event.save()

    except Exception as e:
        logger.error("Webhook processing error for event %s: %s", event_id, e,
                      exc_info=True)
        try:
            event.processing_error = str(e)
            event.save(update_fields=['processing_error'])
        except Exception as save_error:
            logger.critical(
                "Failed to save webhook error for event %s: %s",
                event_id, save_error,
            )
        raise
