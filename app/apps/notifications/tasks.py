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


def notify_seller_link_status(seller, order, event_type, motivo=''):
    if not seller or not seller.phone:
        logger.warning("Seller %s has no phone, skipping link notification", seller.id if seller else '?')
        return None
    valor = f"R$ {order.total_amount // 100},{order.total_amount % 100:02d}"
    create_and_send_notification(
        tenant=seller.tenant,
        event_type=event_type,
        channel=MessageTemplate.Channel.WHATSAPP,
        recipient=seller.phone,
        seller=seller,
        order=order,
        context={
            "vendedor": seller.name,
            "cliente": order.customer_name or 'cliente',
            "valor": valor,
            "link": order.payment_link.gateway_url if hasattr(order, 'payment_link') and order.payment_link else '',
            "motivo": motivo,
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


def notify_commission_adjusted(seller_commission, adjustment):
    seller = seller_commission.seller
    period = seller_commission.period
    from decimal import Decimal
    diff = Decimal(str(adjustment.difference)) / Decimal('100')
    sinal = '-' if adjustment.difference < 0 else ''
    valor = f"{sinal}R$ {abs(diff):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    create_and_send_notification(
        tenant=seller.tenant,
        event_type=MessageTemplate.EventType.COMMISSION_ADJUSTED,
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
    v = context.get('vendedor', '')
    val = context.get('valor', '')
    cli = context.get('cliente', '')
    link = context.get('link', '')
    if event_type == MessageTemplate.EventType.SELLER_CREDENTIALS:
        return (
            f"Ola {v}! "
            f"Seu acesso ao sistema de comissoes foi criado.\n"
            f"Usuario: {context.get('usuario', '')}\n"
            f"Senha temporaria: {context.get('senha', '')}\n"
            f"Acesse e altere sua senha imediatamente."
        )
    if event_type == MessageTemplate.EventType.COMMISSION_PAID:
        return (
            f"Ola {v}! "
            f"Sua comissao de {context.get('periodo', '')} "
            f"no valor de {val} foi paga. "
            f"Confira os detalhes no app."
        )
    if event_type == MessageTemplate.EventType.COMMISSION_ADJUSTED:
        return (
            f"Sua comissao do periodo {context.get('periodo', '')} "
            f"recebeu um ajuste de {val}. "
            f"Acesse o sistema para conferir os detalhes."
        )
    if event_type in (MessageTemplate.EventType.LINK_CREATED,):
        return f"Ola {v}! Seu link de {val} para {cli} foi gerado com sucesso."
    if event_type in (MessageTemplate.EventType.PAYMENT_PAID,):
        return f"Ola {v}! O link de {val} do(a) {cli} foi pago!"
    if event_type in (MessageTemplate.EventType.LINK_CANCELED,):
        return f"Ola {v}! O link de {val} do(a) {cli} foi cancelado."
    if event_type in (MessageTemplate.EventType.PAYMENT_EXPIRED,):
        return f"Ola {v}! O link de {val} do(a) {cli} expirou."
    if event_type in (MessageTemplate.EventType.PAYMENT_FAILED,):
        return (
            f"Ola {v}! O pagamento de {val} do(a) {cli} falhou."
            + (f" Motivo: {context.get('motivo', '')}." if context.get('motivo') else '')
        )
    if event_type in (MessageTemplate.EventType.PAYMENT_REFUNDED,):
        return f"Ola {v}! O pagamento de {val} do(a) {cli} foi estornado."
    return ""
