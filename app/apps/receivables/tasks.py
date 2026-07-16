import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives, send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from app.apps.accounts.models import Tenant, User, tenant_has_feature, tenant_operational
from app.apps.notifications.models import MessageTemplate, Notification
from app.apps.notifications.tasks import send_whatsapp_notification
from app.services.messaging.push import send_push_notification

from .models import Boleto

logger = logging.getLogger(__name__)


def _format_cents(value):
    value = int(value or 0)
    whole = f'{value // 100:,}'.replace(',', '.')
    return f'R$ {whole},{value % 100:02d}'


@shared_task(
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=60,
    time_limit=90,
)
def send_boleto_email(boleto_uuid, kind='created', force=False):
    boleto = Boleto.objects.select_related('tenant', 'seller').get(uuid=boleto_uuid)
    if not boleto.payer_email:
        logger.info('Boleto %s sem e-mail do comprador', boleto.uuid)
        return 'no_email'
    cache_key = f'boleto-email:{boleto.uuid}:{kind}'
    if not force and not cache.add(cache_key, '1', timeout=60 * 60 * 48):
        return 'duplicate'
    subjects = {
        'created': f'Boleto emitido - vencimento {boleto.due_date:%d/%m/%Y}',
        'due_tomorrow': f'Lembrete: boleto vence amanha ({boleto.due_date:%d/%m/%Y})',
        'due_today': 'Lembrete: seu boleto vence hoje',
        'invoice': 'Nota fiscal da sua cobranca',
    }
    context = {
        'boleto': boleto,
        'amount': _format_cents(boleto.amount_cents),
        'kind': kind,
        'brand_logo_url': (
            f'https://{settings.SERVICE_FQDN_WEB}/static/img/vidalys-merito-logo.png'
        ),
    }
    html = render_to_string('boletos/email/boleto.html', context)
    text = render_to_string('boletos/email/boleto.txt', context)
    message = EmailMultiAlternatives(
        subjects.get(kind, 'Boleto Merito'),
        text,
        settings.DEFAULT_FROM_EMAIL,
        [boleto.payer_email],
    )
    message.attach_alternative(html, 'text/html')
    if boleto.invoice_pdf:
        with boleto.invoice_pdf.open('rb') as invoice:
            message.attach('nota-fiscal.pdf', invoice.read(), 'application/pdf')
    if boleto.invoice_xml:
        with boleto.invoice_xml.open('rb') as invoice:
            message.attach('nota-fiscal.xml', invoice.read(), 'application/xml')
    try:
        message.send()
    except Exception:
        if not force:
            cache.delete(cache_key)
        raise
    return 'sent'


@shared_task(soft_time_limit=60, time_limit=90)
def notify_boleto_paid(boleto_uuid):
    boleto = Boleto.objects.select_related('tenant', 'seller__user').get(uuid=boleto_uuid)
    cache_key = f'boleto-paid-notification:{boleto.uuid}'
    if not cache.add(cache_key, '1', timeout=60 * 60 * 24 * 30):
        return 'duplicate'
    amount = _format_cents(boleto.paid_amount_cents or boleto.amount_cents)
    base_url = f'https://{settings.SERVICE_FQDN_WEB}'
    link = f'{base_url}/dashboard/mobile/lancar/?boleto={boleto.uuid}'
    body = (
        f'Boleto pago! {boleto.payer_name} pagou {amount} '
        f'(venc. {boleto.due_date:%d/%m/%Y}). '
        f'Toque para lancar a venda: {link}'
    )
    seller = boleto.seller
    if seller.phone:
        notification = Notification.objects.create(
            tenant=boleto.tenant,
            seller=seller,
            event_type='boleto_paid',
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient=seller.phone,
            message_body=body,
        )
        send_whatsapp_notification.delay(str(notification.uuid))
    if seller.user_id:
        send_push_notification(
            user=seller.user,
            title='Boleto pago!',
            body=body[:200],
            url=f'/dashboard/mobile/lancar/?boleto={boleto.uuid}',
            icon='/static/icons/icon-192.png',
        )
    return 'sent'


@shared_task(soft_time_limit=60, time_limit=90)
def notify_boleto_refunded(boleto_uuid):
    boleto = Boleto.objects.select_related('tenant').get(uuid=boleto_uuid)
    recipients = list(
        User.objects.filter(
            tenant=boleto.tenant,
            role__in=(User.Role.ADMIN, User.Role.MANAGER),
        ).exclude(email='').values_list('email', flat=True)
    )
    if boleto.tenant.billing_email:
        recipients.append(boleto.tenant.billing_email)
    if recipients:
        send_mail(
            f'Boleto estornado - {boleto.payer_name}',
            f'O boleto {boleto.uuid} foi estornado e exige revisao do gestor.',
            settings.DEFAULT_FROM_EMAIL,
            sorted(set(recipients)),
        )
    return len(set(recipients))


@shared_task(soft_time_limit=300, time_limit=360)
def process_boleto_daily_notifications():
    today = timezone.localdate()
    for tenant in Tenant.objects.filter(is_active=True).iterator():
        try:
            if not tenant_operational(tenant) or not tenant_has_feature(tenant, 'boletos'):
                continue
            pending = Boleto.objects.filter(tenant=tenant, status=Boleto.Status.PENDENTE)
            for boleto in pending.filter(due_date=today + timedelta(days=1)):
                send_boleto_email.delay(str(boleto.uuid), 'due_tomorrow')
            for boleto in pending.filter(due_date=today):
                send_boleto_email.delay(str(boleto.uuid), 'due_today')
            pending.filter(due_date__lt=today - timedelta(days=3)).update(
                status=Boleto.Status.VENCIDO,
                updated_at=timezone.now(),
            )
        except Exception:
            logger.exception('Falha no processamento diario de boletos tenant=%s', tenant.uuid)


@shared_task(soft_time_limit=300, time_limit=360)
def send_boleto_manager_digest():
    today = timezone.localdate()
    for tenant in Tenant.objects.filter(is_active=True).iterator():
        try:
            if not tenant_operational(tenant) or not tenant_has_feature(tenant, 'boletos'):
                continue
            queryset = Boleto.objects.filter(tenant=tenant)
            due_soon = queryset.filter(
                status=Boleto.Status.PENDENTE,
                due_date__gte=today,
                due_date__lte=today + timedelta(days=3),
            )
            overdue_yesterday = queryset.filter(
                status__in=(Boleto.Status.PENDENTE, Boleto.Status.VENCIDO),
                due_date=today - timedelta(days=1),
            )
            awaiting = queryset.filter(
                status=Boleto.Status.PAGO,
                launched_sale__isnull=True,
                paid_at__date__lte=today - timedelta(days=2),
            )
            if not (due_soon.exists() or overdue_yesterday.exists() or awaiting.exists()):
                continue
            recipients = list(
                User.objects.filter(
                    tenant=tenant,
                    role__in=(User.Role.ADMIN, User.Role.MANAGER),
                ).exclude(email='').values_list('email', flat=True)
            )
            if tenant.billing_email:
                recipients.append(tenant.billing_email)
            if not recipients:
                continue
            digest_key = f'boleto-manager-digest:{tenant.uuid}:{today.isoformat()}'
            if not cache.add(digest_key, '1', timeout=60 * 60 * 36):
                continue
            body = (
                f'Vencendo em 3 dias: {due_soon.count()}\n'
                f'Vencidos ontem: {overdue_yesterday.count()}\n'
                f'Pagos aguardando lancamento: {awaiting.count()}'
            )
            try:
                send_mail(
                    'Resumo diario de boletos', body,
                    settings.DEFAULT_FROM_EMAIL, sorted(set(recipients)),
                )
            except Exception:
                cache.delete(digest_key)
                raise
        except Exception:
            logger.exception('Falha no digest de boletos tenant=%s', tenant.uuid)
