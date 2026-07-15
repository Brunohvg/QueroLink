import logging
import time

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.core.mail import send_mail, EmailMessage
from django.db.models import Sum as DSum, Q
from app.apps.notifications.models import Notification, MessageTemplate, LifecycleEmail
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
        if notification.secondary_body:
            # Se a segunda mensagem falhar, o retry reenvia as duas mensagens.
            time.sleep(1)
            client.send_message(notification.recipient, notification.secondary_body)

        notification.status = Notification.Status.SENT
        notification.save(update_fields=["status", "updated_at"])
        logger.info(
            "WhatsApp notification %s sent (%d mensagens)",
            notification_id, 2 if notification.secondary_body else 1,
        )

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


def create_and_send_notification(*, tenant, event_type, channel, recipient, context, seller=None, order=None, commission_period=None, secondary_body=None):
    if channel == MessageTemplate.Channel.WHATSAPP and not recipient:
        logger.warning(
            "WhatsApp notification skipped: %s no phone for tenant=%s seller=%s",
            event_type, tenant.id, seller.id if seller else '?',
        )
        return None

    template = MessageTemplate.objects.filter(
        tenant=tenant, event_type=event_type, channel=channel, is_active=True
    ).first()

    from app.apps.notifications.models import EVENT_VARIABLES
    valid_vars = EVENT_VARIABLES.get(event_type, [])
    for v in valid_vars:
        if v not in context:
            context[v] = ''

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
        secondary_body=secondary_body,
    )

    if channel == MessageTemplate.Channel.WHATSAPP:
        send_whatsapp_notification.delay(notification.uuid)

    if seller and seller.user:
        from app.services.messaging.push import send_push_notification
        send_push_notification(
            user=seller.user,
            title=_push_title(event_type),
            body=message_body[:200],
            url='/dashboard/mobile/',
            icon='/static/icons/icon-192.png',
        )

    return notification


def notify_seller_credentials(seller, password):
    from app.apps.accounts.models import mark_onboarding_step
    mark_onboarding_step(seller.tenant, 'step_first_invite')

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
        secondary_body=password,
    )


def notify_seller_link_status(seller, order, event_type, motivo=''):
    if not seller or not seller.phone:
        logger.warning("Seller %s has no phone, skipping link notification", seller.id if seller else '?')
        return None
    valor = format_brl_cents(order.total_amount)
    payment = order.payments.order_by('created_at').first()
    installments = payment.installments if payment else 1
    parcelas = f"até {installments}x"
    return create_and_send_notification(
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
            "parcelas": parcelas,
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
        from app.apps.accounts.models import tenant_operational, is_working_day
        if not tenant_operational(tenant):
            continue
        if not is_working_day(tenant, current_date):
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


def _push_title(event_type):
    titles = {
        MessageTemplate.EventType.DAILY_REMINDER: 'Lancar vendas',
        MessageTemplate.EventType.COMMISSION_PAID: 'Comissao paga!',
        MessageTemplate.EventType.PAYMENT_PAID: 'Link pago!',
        MessageTemplate.EventType.SELLER_CREDENTIALS: 'Acesso criado',
        MessageTemplate.EventType.COMMISSION_ADJUSTED: 'Comissao ajustada',
    }
    return titles.get(event_type, 'Merito')


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
        parcelas = context.get('parcelas', '')
        return (
            "Link de pagamento criado\n"
            f"Cliente: {cli}\n"
            f"Valor: {val}\n"
            f"Pagamento: {parcelas}\n"
            "Link:\n"
            f"{link}\n"
            "Encaminhe este link ao cliente."
        )
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


@shared_task(soft_time_limit=300, time_limit=360)
def send_lifecycle_emails():
    from datetime import timedelta
    from app.apps.accounts.models import Tenant
    from app.apps.sellers.models import Seller
    from app.apps.sales.models import Sale

    hoje = timezone.localdate()
    tenants = Tenant.objects.filter(is_active=True)

    for tenant in tenants:
        try:
            email = getattr(tenant, 'billing_email', None)
            if not email:
                from app.apps.accounts.models import User as U
                email = U.objects.filter(tenant=tenant, role__in=['ADMIN', 'MANAGER']).values_list('email', flat=True).first()
            if not email:
                continue

            sub = None
            try:
                from app.apps.billing.models import Subscription
                sub = Subscription.objects.get(tenant=tenant)
            except Exception:
                pass

            is_pagante = sub and sub.status == 'ACTIVE'
            trial_end = tenant.trial_ends_at

            triggers_handled = set(
                LifecycleEmail.objects.filter(tenant=tenant).values_list('trigger', flat=True)
            )

            if trial_end and not is_pagante:
                days_left = (timezone.localtime(trial_end).date() - hoje).days
                if 6 <= days_left <= 7 and 'trial_d7' not in triggers_handled:
                    vendors = Seller.objects.filter(tenant=tenant, is_active=True).count()
                    sales_count = Sale.objects.filter(tenant=tenant, status='ATIVA').count()
                    body = f"Ola! Seu periodo de teste do Merito termina em 7 dias.\n\n"
                    body += f"Voce ja cadastrou {vendors} vendedores e registrou {sales_count} vendas. "
                    body += f"Para continuar usando todas as funcionalidades, assine um plano.\n\n"
                    body += f"Acesse: https://{settings.SERVICE_FQDN_WEB}/dashboard/assinatura/"
                    send_mail('Seu trial termina em 7 dias', body, settings.DEFAULT_FROM_EMAIL, [email])
                    LifecycleEmail.objects.create(tenant=tenant, trigger='trial_d7')

                elif 2 <= days_left <= 3 and 'trial_d3' not in triggers_handled:
                    body = f"ATENCAO: Seu trial do Merito expira em {days_left} dias.\n\n"
                    body += f"Sem assinatura, voce perdera acesso aos relatorios, exportacao e mais.\n"
                    body += f"Assine agora: https://{settings.SERVICE_FQDN_WEB}/dashboard/assinatura/"
                    send_mail('Ultimos dias de trial', body, settings.DEFAULT_FROM_EMAIL, [email])
                    LifecycleEmail.objects.create(tenant=tenant, trigger='trial_d3')

                elif -1 <= days_left <= 0 and 'trial_d0' not in triggers_handled:
                    body = f"Seu trial do Merito expirou.\n\n"
                    body += f"Para reativar sua conta e continuar usando o sistema, escolha um plano:\n"
                    body += f"https://{settings.SERVICE_FQDN_WEB}/dashboard/assinatura/"
                    send_mail('Seu trial expirou', body, settings.DEFAULT_FROM_EMAIL, [email])
                    LifecycleEmail.objects.create(tenant=tenant, trigger='trial_d0')

            if is_pagante:
                last_sale = Sale.objects.filter(tenant=tenant, status='ATIVA').order_by('-sale_date').first()
                if last_sale and last_sale.sale_date < hoje - timedelta(days=7) and 'inactive_7d' not in triggers_handled:
                    body = f"Sentimos sua falta! Sua equipe nao registra vendas ha mais de 7 dias.\n\n"
                    body += f"O Merito esta pronto para ajudar. Acesse: https://{settings.SERVICE_FQDN_WEB}/dashboard/gestor/"
                    send_mail('Sentimos sua falta', body, settings.DEFAULT_FROM_EMAIL, [email])
                    LifecycleEmail.objects.create(tenant=tenant, trigger='inactive_7d')
        except Exception:
            logger.exception('send_lifecycle_emails: falha no tenant %s, continuando', tenant.pk)
            continue


@shared_task(bind=True, max_retries=3, default_retry_delay=120, soft_time_limit=120, time_limit=180)
def send_accounting_package_email(self, tenant_uuid, month, year, requested_by_user_id=None):
    from app.apps.accounts.models import Tenant, tenant_operational

    try:
        tenant = Tenant.objects.get(uuid=tenant_uuid)
    except Tenant.DoesNotExist:
        logger.warning("send_accounting_package_email: tenant %s not found", tenant_uuid)
        return

    if not getattr(settings, 'PLAN_FEATURES', {}).get(tenant.plan, {}).get('export_contabil'):
        return

    if not tenant.accountant_email:
        logger.info("send_accounting_package_email: tenant %s has no accountant email", tenant_uuid)
        return

    month_int = int(month)
    year_int = int(year)

    if not requested_by_user_id:
        from datetime import timedelta
        from app.apps.audit.models import AuditLog
        uma_semana = timezone.now() - timedelta(days=7)
        try:
            existing = AuditLog.objects.filter(
                tenant=tenant,
                action='accounting_email_sent',
                created_at__gte=uma_semana,
                changes__month=month_int,
                changes__year=year_int,
            ).exists()
        except Exception:
            existing = False
        if existing:
            logger.info(
                "send_accounting_package_email: auto-send ja realizado para %s %s/%s",
                tenant_uuid, month, year,
            )
            return

    from app.apps.commissions.exports import build_accounting_zip, _fmt_br
    from app.apps.commissions.services import get_period_by_legacy_label, legacy_month_range
    from app.apps.sellers.models import Seller as SellerM
    from app.apps.sales.models import Sale as SModel

    period = get_period_by_legacy_label(tenant, month_int, year_int)
    if period:
        start = period.start_date
        end = period.end_date
        competencia = period.display_label
    else:
        start, end = legacy_month_range(month_int, year_int)
        competencia = f'{month_int:02d}/{year_int}'
    zip_bytes = build_accounting_zip(tenant, month_int, year_int)

    total_sold_all = SModel.objects.filter(
        tenant=tenant, status='ATIVA',
        sale_date__gte=start, sale_date__lte=end,
    ).aggregate(t=DSum('amount'))['t'] or 0

    senders = SellerM.objects.filter(tenant=tenant, is_active=True).count()
    msg = EmailMessage(
        subject=f'[{tenant.company_name}] Fechamento de comissoes — {competencia}',
        body=f'Competencia: {competencia}\n'
             f'Periodo: {start.strftime("%d/%m/%Y")} a {end.strftime("%d/%m/%Y")}\n'
             f'Vendedores ativos: {senders}\n'
             f'Total vendido: {_fmt_br(total_sold_all)}\n\n'
             f'Segue em anexo o pacote contabil com vendas, comissoes e resumo.\n'
             f'Gerado automaticamente pelo Merito by Vidalys.',
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[tenant.accountant_email],
    )
    msg.attach(f'contabilidade_{year_int}_{month_int:02d}.zip', zip_bytes, 'application/zip')
    msg.send()

    if period and not period.sent_to_accounting_at:
        period.sent_to_accounting_at = timezone.now()
        if requested_by_user_id:
            period.sent_to_accounting_by_id = requested_by_user_id
        period.save(update_fields=['sent_to_accounting_at', 'sent_to_accounting_by', 'updated_at'])

    try:
        from app.apps.audit.models import AuditLog
        AuditLog.objects.create(
            user=None,
            tenant=tenant,
            action='accounting_email_sent',
            changes={
                'month': month_int, 'year': year_int,
                'auto': not bool(requested_by_user_id),
                'recipient': tenant.accountant_email,
            },
        )
    except Exception:
        logger.exception('Falha ao registrar audit do envio contabil')

    logger.info(
        "send_accounting_package_email: sent to %s for %s/%s",
        tenant.accountant_email, month_int, year_int,
    )
