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

        # Pagar.me v5 geralmente envia o webhook "order.paid"
        if event_type == 'order.paid':
            # Como views.py salva o ID do payment_link em gateway_transaction_id, precisamos
            # tentar extrair o ID do Payment Link caso a Pagar.me não retorne o ID direto.
            # Alternativamente, a Pagar.me também manda o ID da Order no 'data.id'.
            gateway_id = data.get('payment_link_id') or data.get('id')
            
            if not gateway_id:
                raise ValueError("gateway_transaction_id não encontrado no payload")

            try:
                # Localizar o Payment correspondente via gateway_transaction_id
                payment = Payment.objects.get(gateway_transaction_id=gateway_id)
            except Payment.DoesNotExist:
                raise ValueError(f"Payment não encontrado para o gateway_transaction_id {gateway_id}")

            status = data.get('status')
            
            # Se o evento indica pagamento confirmado (status paid no payload)
            if status == 'paid':
                payment.status = Payment.Status.PAID
                payment.raw_callback_payload = payload
                payment.save()

                order = payment.order
                order.status = Order.Status.COMPLETED
                order.save()

                # Criar um Sale com origin=Sale.Origin.LINK
                Sale.objects.get_or_create(
                    order=order,
                    defaults={
                        'tenant': order.tenant,
                        'seller': order.seller,
                        'origin': Sale.Origin.LINK,
                        'amount': order.total_amount,
                        'sale_date': timezone.now().date(),
                    }
                )

        event.processed = True
        event.save()

    except Exception as e:
        event.processing_error = str(e)
        event.save()
