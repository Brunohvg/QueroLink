import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from app.apps.notifications.models import Notification, MessageTemplate
from app.services.messaging.whatsapp import WhatsappClient, InvalidNumberError

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


@shared_task(bind=True, max_retries=MAX_RETRIES, default_retry_delay=60, soft_time_limit=60, time_limit=90)
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

    if notification.channel != MessageTemplate.Channel.WHATSAPP:
        logger.warning("Notification %s channel is not WhatsApp (%s), skipping", notification_id, notification.channel)
        return

    if not notification.recipient:
        logger.warning("Notification %s has no recipient, skipping", notification_id)
        notification.status = Notification.Status.FAILED
        notification.error_log = "Destinatario vazio (sem telefone)"
        notification.save(update_fields=["status", "error_log", "updated_at"])
        return

    try:
        tenant = notification.tenant
        instance = tenant.whatsapp_instance_id
        api_key = tenant.whatsapp_token

        if not instance:
            if getattr(settings, 'WHATSAPP_ALLOW_SHARED_INSTANCE', False):
                instance = settings.WHATSAPP_INSTANCE
                api_key = api_key or settings.WHATSAPP_API_KEY
                logger.info(
                    "Notification %s usando instancia compartilhada (tenant %s sem instancia propria)",
                    notification_id, tenant.slug,
                )
            else:
                notification.status = Notification.Status.FAILED
                notification.error_log = 'Tenant sem instancia WhatsApp configurada. Conecte o WhatsApp em Configuracoes.'
                notification.save(update_fields=['status', 'error_log', 'updated_at'])
                return

        client = WhatsappClient(
            instance=instance,
            api_key=api_key,
        )
        client.send_message(notification.recipient, notification.message_body)

        notification.status = Notification.Status.SENT
        notification.save(update_fields=["status", "updated_at"])

    except InvalidNumberError as e:
        notification.status = Notification.Status.FAILED
        notification.error_log = str(e)[:1000]
        notification.save(update_fields=['status', 'error_log', 'updated_at'])
        logger.warning("WhatsApp notification %s failed: invalid number — %s", notification_id, e)

    except Exception as e:
        celery_retries = self.request.retries if hasattr(self, 'request') and self.request is not None else 0
        base_retries = max(celery_retries, notification.retry_count)
        attempt = base_retries + 1

        notification.retry_count = attempt
        notification.error_log = str(e)[:1000]

        if attempt >= MAX_RETRIES:
            notification.status = Notification.Status.FAILED
            notification.error_log = f"Max retries ({MAX_RETRIES}). Ultimo erro: {str(e)[:800]}"
            notification.save(update_fields=['retry_count', 'status', 'error_log', 'updated_at'])
            logger.warning(
                "WhatsApp notification %s failed (attempt %d/%d): %s",
                notification_id, attempt, MAX_RETRIES, e,
            )
            return

        notification.save(update_fields=['retry_count', 'error_log', 'updated_at'])
        logger.warning(
            "WhatsApp notification %s failed (attempt %d/%d): %s",
            notification_id, attempt, MAX_RETRIES, e,
        )
        if hasattr(self, 'request') and self.request is not None:
            raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


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
        try:
            message_body = template.render_body(context)
        except Exception:
            logger.exception(
                "Template render failed for tenant=%s event=%s, using fallback",
                tenant.pk, event_type,
            )
            message_body = _fallback_body(event_type, context)
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
    valor = format_brl_cents(order.total_amount)
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
    amount_value = seller_commission.paid_amount if seller_commission.paid_amount is not None else seller_commission.amount_due
    valor = format_brl_cents(amount_value)

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


@shared_task(soft_time_limit=300, time_limit=360)
def send_daily_entry_reminders():
    from datetime import time, timedelta
    from django.db.models import Q
    from app.apps.accounts.models import Tenant
    from app.apps.sales.models import Sale
    from app.apps.sellers.models import Seller

    now = timezone.localtime(timezone.now())
    current_time = now.time()
    current_date = now.date()

    if current_date.weekday() == 6:
        logger.info("Daily reminder: domingo, pulando")
        return

    window_start = (timezone.localtime(timezone.now()) - timedelta(minutes=15)).time()

    tenants = Tenant.objects.filter(
        is_active=True,
        daily_reminder_enabled=True,
        daily_reminder_time__gte=window_start,
        daily_reminder_time__lte=current_time,
    )

    sent_count = 0
    skipped_no_phone = 0
    for tenant in tenants:
        from app.apps.accounts.models import tenant_operational
        if not tenant_operational(tenant):
            continue

        sellers_with_sale_today = Sale.objects.filter(
            tenant=tenant,
            origin=Sale.Origin.MANUAL,
            status='ATIVA',
            sale_date=current_date,
        ).values_list('seller_id', flat=True).distinct()

        sellers_to_remind = Seller.objects.filter(
            tenant=tenant, is_active=True,
        ).exclude(uuid__in=sellers_with_sale_today).select_related('user')

        for seller in sellers_to_remind:
            if not seller.phone:
                skipped_no_phone += 1
                continue

            already_sent = Notification.objects.filter(
                seller=seller,
                event_type=MessageTemplate.EventType.DAILY_REMINDER,
                created_at__date=current_date,
            ).exists()
            if already_sent:
                continue

            create_and_send_notification(
                tenant=tenant,
                event_type=MessageTemplate.EventType.DAILY_REMINDER,
                channel=MessageTemplate.Channel.WHATSAPP,
                recipient=seller.phone,
                seller=seller,
                context={'vendedor': seller.name},
            )
            sent_count += 1

    logger.info(
        "Daily reminder: %d enviados, %d sem telefone",
        sent_count, skipped_no_phone,
    )


@shared_task(soft_time_limit=300, time_limit=360)
def requeue_stuck_notifications():
    from datetime import timedelta

    now = timezone.now()
    window_start = now - timedelta(hours=48)
    window_end = now - timedelta(minutes=15)

    stuck = Notification.objects.filter(
        status=Notification.Status.PENDING,
        retry_count=0,
        created_at__gte=window_start,
        created_at__lte=window_end,
    )[:100]

    count = 0
    for n in stuck:
        send_whatsapp_notification.delay(n.uuid)
        count += 1

    if count:
        logger.info("Requeued %d stuck notifications (PENDING, never attempted)", count)


def format_brl_cents(amount_cents):
    from decimal import Decimal
    amount = Decimal(str(amount_cents)) / Decimal('100')
    return f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


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
    if event_type == MessageTemplate.EventType.DAILY_REMINDER:
        return (
            f"Ola {v}! "
            f"Voce ainda nao lancou suas vendas de hoje. "
            f"Lance agora pelo app para manter sua comissao em dia."
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
