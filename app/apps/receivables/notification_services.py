import hashlib
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from app.apps.accounts.models import User
from app.apps.notifications.models import MessageTemplate
from app.apps.notifications.tasks import create_and_send_notification


logger = logging.getLogger(__name__)

_BRL_CENTS_TEMPLATE = '{},{}'


def _format_brl(cents):
    if cents is None:
        return 'R$ 0,00'
    reais = cents // 100
    centavos = cents % 100
    return f'R$ {reais},{centavos:02d}'


def _notification_key(boleto_uuid, event_slug, channel, recipient):
    raw = f'{boleto_uuid}:{event_slug}:{channel}:{recipient}'
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _send_email_notification(boleto, subject, template_name, context):
    html_body = render_to_string(
        f'boletos/email/{template_name}.html', context
    )
    text_body = render_to_string(
        f'boletos/email/{template_name}.txt', context
    )
    try:
        send_mail(
            subject=subject,
            message=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[boleto.payer_email],
            html_message=html_body,
            fail_silently=False,
        )
    except Exception:
        logger.warning(
            'Falha ao enviar email boleto=%s tenant=%s',
            boleto.uuid, boleto.tenant_id,
        )


def _notify_seller_whatsapp(boleto, event_type_slug, context):
    event_type_map = {
        'boleto_created': 'payment_paid',
        'boleto_paid': 'payment_paid',
        'boleto_canceled': 'link_canceled',
        'boleto_refunded': 'payment_refunded',
        'boleto_chargeback': 'payment_chargeback',
        'boleto_due_reminder': 'daily_reminder',
    }
    mapped = event_type_map.get(event_type_slug, 'payment_paid')
    try:
        create_and_send_notification(
            tenant=boleto.tenant,
            event_type=mapped,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient=boleto.seller.phone,
            seller=boleto.seller,
            context=context,
        )
    except Exception:
        logger.warning(
            'Falha ao enviar whatsapp boleto=%s tenant=%s',
            boleto.uuid, boleto.tenant_id,
        )


def _notify_gestor_email(boleto, event_type_slug):
    from app.apps.accounts.models import User as UserModel

    if event_type_slug == 'boleto_created':
        subject = 'Novo boleto emitido'
        template = 'boleto_created'
    elif event_type_slug == 'boleto_paid':
        subject = 'Boleto pago'
        template = 'boleto_paid'
    elif event_type_slug == 'boleto_canceled':
        subject = 'Boleto cancelado'
        template = 'boleto_canceled'
    elif event_type_slug == 'boleto_awaiting_allocation':
        subject = 'Boleto aguardando alocacao'
        template = 'boleto_paid'
    else:
        return

    context = {
        'boleto_uuid': str(boleto.uuid),
        'seller_name': boleto.seller.name,
        'amount': _format_brl(boleto.amount_cents),
        'paid_amount': _format_brl(boleto.paid_amount_cents),
        'status': boleto.get_status_display(),
        'due_date': boleto.due_date.isoformat(),
    }

    gestores = UserModel.objects.filter(
        tenant=boleto.tenant,
        role__in=(UserModel.Role.ADMIN, UserModel.Role.MANAGER),
        email__isnull=False,
    ).exclude(email='').values_list('email', flat=True)

    if not gestores:
        return

    html_body = render_to_string(
        f'boletos/email/{template}.html', context
    )
    text_body = render_to_string(
        f'boletos/email/{template}.txt', context
    )

    try:
        send_mail(
            subject=f'[Merito] {subject}',
            message=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=list(gestores),
            html_message=html_body,
            fail_silently=False,
        )
    except Exception:
        logger.warning(
            'Falha ao enviar email gestor boleto=%s tenant=%s',
            boleto.uuid, boleto.tenant_id,
        )


def notify_boleto_created(boleto):
    try:
        context = {
            'vendedor': boleto.seller.name,
            'cliente': boleto.payer_name,
            'valor': _format_brl(boleto.amount_cents),
            'link': '',
        }
        _notify_seller_whatsapp(boleto, 'boleto_created', context)
        _notify_gestor_email(boleto, 'boleto_created')
    except Exception:
        logger.warning(
            'Falha ao notificar criacao boleto=%s', boleto.uuid,
        )


def notify_boleto_paid(boleto):
    try:
        context = {
            'vendedor': boleto.seller.name,
            'cliente': boleto.payer_name,
            'valor': _format_brl(boleto.paid_amount_cents),
            'link': '',
        }
        _notify_seller_whatsapp(boleto, 'boleto_paid', context)
        _notify_gestor_email(boleto, 'boleto_paid')
        _send_email_notification(
            boleto,
            subject='Seu boleto foi pago',
            template_name='boleto_paid',
            context={
                'boleto_uuid': str(boleto.uuid),
                'seller_name': boleto.seller.name,
                'amount': _format_brl(boleto.amount_cents),
                'paid_amount': _format_brl(boleto.paid_amount_cents),
            },
        )
    except Exception:
        logger.warning(
            'Falha ao notificar pagamento boleto=%s', boleto.uuid,
        )


def notify_boleto_canceled(boleto):
    try:
        context = {
            'vendedor': boleto.seller.name,
            'cliente': boleto.payer_name,
            'valor': _format_brl(boleto.amount_cents),
            'link': '',
        }
        _notify_seller_whatsapp(boleto, 'boleto_canceled', context)
        _notify_gestor_email(boleto, 'boleto_canceled')
    except Exception:
        logger.warning(
            'Falha ao notificar cancelamento boleto=%s', boleto.uuid,
        )


def notify_boleto_refunded(boleto):
    try:
        context = {
            'vendedor': boleto.seller.name,
            'cliente': boleto.payer_name,
            'valor': _format_brl(boleto.amount_cents),
            'link': '',
        }
        _notify_seller_whatsapp(boleto, 'boleto_refunded', context)
    except Exception:
        logger.warning(
            'Falha ao notificar estorno boleto=%s', boleto.uuid,
        )


def notify_boleto_chargeback(boleto):
    try:
        context = {
            'vendedor': boleto.seller.name,
            'cliente': boleto.payer_name,
            'valor': _format_brl(boleto.amount_cents),
            'link': '',
        }
        _notify_seller_whatsapp(boleto, 'boleto_chargeback', context)
    except Exception:
        logger.warning(
            'Falha ao notificar chargeback boleto=%s', boleto.uuid,
        )


def notify_boleto_due_reminder(boleto):
    try:
        context = {
            'vendedor': boleto.seller.name,
            'cliente': boleto.payer_name,
            'valor': _format_brl(boleto.amount_cents),
        }
        _notify_seller_whatsapp(boleto, 'boleto_due_reminder', context)
    except Exception:
        logger.warning(
            'Falha ao notificar lembrete boleto=%s', boleto.uuid,
        )


def notify_boleto_awaiting_allocation(boleto):
    try:
        _notify_gestor_email(boleto, 'boleto_awaiting_allocation')
    except Exception:
        logger.warning(
            'Falha ao notificar alocacao pendente boleto=%s', boleto.uuid,
        )


def notify_commission_impact_pending(review):
    from app.apps.accounts.models import User as UserModel

    gestores = UserModel.objects.filter(
        tenant=review.tenant,
        role__in=(UserModel.Role.ADMIN, UserModel.Role.MANAGER),
        email__isnull=False,
    ).exclude(email='').values_list('email', flat=True)

    if not gestores:
        return

    context = {
        'seller_name': review.seller.name,
        'amount': _format_brl(review.delta_sale_cents),
        'impact_type': review.get_impact_type_display(),
        'status': review.get_status_display(),
    }

    html_body = render_to_string(
        'boletos/email/commission_impact.html', context
    )
    text_body = render_to_string(
        'boletos/email/commission_impact.txt', context
    )

    try:
        send_mail(
            subject='[Merito] Impacto em comissao aguardando revisao',
            message=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=list(gestores),
            html_message=html_body,
            fail_silently=False,
        )
    except Exception:
        logger.warning(
            'Falha ao enviar email impacto comissao tenant=%s',
            review.tenant_id,
        )
