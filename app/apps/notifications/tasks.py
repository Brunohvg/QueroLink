import logging

from celery import shared_task
from django.conf import settings
from app.apps.notifications.models import Notification, MessageTemplate
from app.services.messaging.whatsapp import WhatsappClient

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


@shared_task(bind=True, max_retries=MAX_RETRIES, default_retry_delay=60)
def send_whatsapp_notification(self, notification_id):
    try:
        notification = Notification.objects.select_related(
            "tenant", "seller", "commission_period"
        ).get(uuid=notification_id)
    except Notification.DoesNotExist:
        logger.warning("Notification %s not found", notification_id)
        return

    if notification.status != Notification.Status.PENDING:
        logger.info("Notification %s already processed, skipping", notification_id)
        return

    if not notification.recipient:
        logger.warning("Notification %s has no recipient, skipping", notification_id)
        notification.status = Notification.Status.FAILED
        notification.error_log = "Destinatario vazio (sem telefone)"
        notification.save(update_fields=["status", "error_log", "updated_at"])
        return

    try:
        tenant = notification.tenant
        client = WhatsappClient(
            instance=tenant.whatsapp_instance_id or getattr(settings, 'WHATSAPP_INSTANCE', ''),
            api_key=tenant.whatsapp_token or getattr(settings, 'WHATSAPP_API_KEY', ''),
        )
        client.send_message(notification.recipient, notification.message_body)

        notification.status = Notification.Status.SENT
        notification.save(update_fields=["status", "updated_at"])

    except Exception as e:
        notification.retry_count += 1
        notification.error_log = str(e)[:1000]
        notification.save(update_fields=["retry_count", "error_log", "updated_at"])

        retry_count = notification.retry_count
        logger.warning(
            "WhatsApp notification %s failed (attempt %d/%d): %s",
            notification_id, retry_count, MAX_RETRIES, e,
        )

        if retry_count < MAX_RETRIES:
            countdown = 60 * (2 ** (retry_count - 1))
            try:
                raise self.retry(countdown=countdown)
            except Exception:
                raise
        else:
            notification.status = Notification.Status.FAILED
            notification.error_log = f"Max retries ({MAX_RETRIES}) exceeded. Last error: {str(e)[:800]}"
            notification.save(update_fields=["status", "error_log", "updated_at"])


def create_and_send_notification(*, tenant, event_type, channel, recipient, context, seller=None, order=None, commission_period=None):
    if channel == MessageTemplate.Channel.WHATSAPP and not recipient:
        logger.warning(
            "WhatsApp notification skipped: %s no phone for tenant=%s seller=%s",
            event_type, tenant.id, seller.id if seller else '?',
        )
        return None

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
    from decimal import Decimal
    amount = Decimal(str(seller_commission.commission_amount)) / Decimal('100')
    valor = f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

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
            f"Acesse e altere sua senha imediatamente."
        )
    if event_type == MessageTemplate.EventType.COMMISSION_PAID:
        return (
            f"Ola {context.get('vendedor', '')}! "
            f"Sua comissao de {context.get('periodo', '')} "
            f"no valor de {context.get('valor', '')} foi paga. "
            f"Confira os detalhes no app."
        )
    return ""
