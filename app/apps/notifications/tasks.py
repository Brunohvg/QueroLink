from celery import shared_task
from app.apps.notifications.models import Notification, MessageTemplate
from app.services.messaging.whatsapp import WhatsappClient

MAX_RETRIES = 3


@shared_task(bind=True, max_retries=MAX_RETRIES, default_retry_delay=60)
def send_whatsapp_notification(self, notification_id):
    try:
        notification = Notification.objects.select_related(
            "tenant", "seller", "commission_period"
        ).get(uuid=notification_id)
    except Notification.DoesNotExist:
        return

    if notification.status != Notification.Status.PENDING:
        return

    if notification.retry_count >= MAX_RETRIES:
        notification.status = Notification.Status.FAILED
        notification.error_log = f"Max retries ({MAX_RETRIES}) exceeded."
        notification.save(update_fields=["status", "error_log", "updated_at"])
        return

    try:
        client = WhatsappClient()
        client.send_message(notification.recipient, notification.message_body)

        notification.status = Notification.Status.SENT
        notification.save(update_fields=["status", "updated_at"])

    except Exception as e:
        notification.retry_count += 1
        notification.error_log = str(e)[:1000]
        notification.save(update_fields=["retry_count", "error_log", "updated_at"])

        if notification.retry_count < MAX_RETRIES:
            countdown = 60 * (2 ** (notification.retry_count - 1))
            raise self.retry(countdown=countdown)
        else:
            notification.status = Notification.Status.FAILED
            notification.save(update_fields=["status", "updated_at"])


def create_and_send_notification(*, tenant, event_type, channel, recipient, context, seller=None, order=None, commission_period=None):
    template = MessageTemplate.objects.filter(
        tenant=tenant, event_type=event_type, channel=channel, is_active=True
    ).first()

    if template:
        message_body = template.render_body(context)
    else:
        message_body = _fallback_body(event_type, context)

    notification = Notification.objects.create(
        tenant=tenant,
        order=order,
        seller=seller,
        commission_period=commission_period,
        event_type=event_type,
        channel=channel,
        recipient=recipient,
        message_body=message_body,
    )

    send_whatsapp_notification.delay(notification.uuid)
    return notification


def notify_seller_credentials(seller, password):
    create_and_send_notification(
        tenant=seller.tenant,
        event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
        channel=MessageTemplate.Channel.WHATSAPP,
        recipient=seller.phone,
        seller=seller,
        context={
            "vendedor": seller.name,
            "usuario": seller.user.username,
            "senha": password,
        },
    )


def notify_commission_paid(seller_commission):
    seller = seller_commission.seller
    period = seller_commission.period
    valor = f"R$ {seller_commission.commission_amount / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    create_and_send_notification(
        tenant=seller.tenant,
        event_type=MessageTemplate.EventType.COMMISSION_PAID,
        channel=MessageTemplate.Channel.WHATSAPP,
        recipient=seller.phone,
        seller=seller,
        commission_period=period,
        context={
            "vendedor": seller.name,
            "periodo": f"{period.month:02d}/{period.year}",
            "valor": valor,
        },
    )


def _fallback_body(event_type, context):
    if event_type == MessageTemplate.EventType.SELLER_CREDENTIALS:
        return (
            f"Ola {context.get('vendedor', '')}! "
            f"Seu acesso ao sistema de comissoes foi criado.\n"
            f"Usuario: {context.get('usuario', '')}\n"
            f"Senha temporaria: {context.get('senha', '')}\n"
            f"Acesse e troque sua senha no primeiro login."
        )
    if event_type == MessageTemplate.EventType.COMMISSION_PAID:
        return (
            f"Ola {context.get('vendedor', '')}! "
            f"Sua comissao de {context.get('periodo', '')} "
            f"no valor de {context.get('valor', '')} foi paga. "
            f"Confira os detalhes no app."
        )
    return ""
