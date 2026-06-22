from celery import shared_task
from django.utils import timezone
from app.apps.webhooks.models import WebhookEvent
from app.apps.payments.models import Payment
from app.apps.orders.models import Order
from app.apps.sales.models import Sale


@shared_task
def process_pagarme_webhook(event_id):
    try:
        event = WebhookEvent.objects.get(id=event_id)
    except WebhookEvent.DoesNotExist:
        return

    try:
        payload = event.payload
        if not isinstance(payload, dict):
            raise ValueError("Payload is not a dictionary")

        event_type = payload.get('type')
        data = payload.get('data', {})

        if event_type == 'order.paid':
            order_uuid = data.get('code')

            if not order_uuid:
                raise ValueError(
                    "order_code nao encontrado no payload (campo 'code')",
                )

            try:
                order = Order.objects.get(uuid=order_uuid)

                payment = order.payments.first()
                if not payment:
                    raise ValueError(
                        f"Payment nao encontrado para a Order {order_uuid}",
                    )

                gateway_order_id = data.get('id')
                if gateway_order_id:
                    payment.gateway_order_id = gateway_order_id
                    payment.save(update_fields=['gateway_order_id'])

            except Order.DoesNotExist:
                raise ValueError(
                    f"Order nao encontrada para o code {order_uuid}",
                )

            status = data.get('status')

            if status == 'paid':
                payment.status = Payment.Status.PAID
                payment.raw_callback_payload = payload
                payment.save()

                order = payment.order
                order.status = Order.Status.COMPLETED
                order.save()

                Sale.objects.get_or_create(
                    order=order,
                    defaults={
                        'tenant': order.tenant,
                        'seller': order.seller,
                        'origin': Sale.Origin.LINK,
                        'amount': order.total_amount,
                        'sale_date': timezone.now().date(),
                    },
                )

        event.processed = True
        event.save()

    except Exception as e:
        event.processing_error = str(e)
        event.save()
