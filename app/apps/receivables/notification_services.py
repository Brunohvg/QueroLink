import hashlib
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from app.apps.accounts.models import User as UserModel

from .models import ReceivableNotificationDelivery, Boleto


logger = logging.getLogger(__name__)

DELIVERY_MAX_ATTEMPTS = 5
DEAD_LETTER_HOURS = 72


def _delivery_key(boleto_uuid, event_slug, channel, recipient):
    raw = f'{boleto_uuid}:{event_slug}:{channel}:{recipient}'
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _recipient_hash(value):
    return hashlib.sha256((value or '').encode()).hexdigest()[:40]


def _format_brl(cents):
    if cents is None:
        return 'R$ 0,00'
    return f'R$ {cents // 100},{cents % 100:02d}'


def _register_delivery(outbox_event, channel, recipient, delivery_key):
    delivery, created = ReceivableNotificationDelivery.objects.get_or_create(
        tenant=outbox_event.tenant,
        delivery_key=delivery_key,
        defaults={
            'outbox_event': outbox_event,
            'channel': channel,
            'recipient_hash': _recipient_hash(recipient),
            'max_attempts': DELIVERY_MAX_ATTEMPTS,
            'status': ReceivableNotificationDelivery.Status.PENDING,
        },
    )
    if created:
        return delivery, False
    if delivery.status == ReceivableNotificationDelivery.Status.SENT:
        return delivery, True
    return delivery, False


def _mark_sent(delivery):
    delivery.status = ReceivableNotificationDelivery.Status.SENT
    delivery.sent_at = timezone.now()
    delivery.attempt_count += 1
    delivery.save(update_fields=['status', 'sent_at', 'attempt_count', 'updated_at'])


def _mark_failed(delivery, error):
    delivery.attempt_count += 1
    delivery.last_error = ReceivableNotificationDelivery.sanitize_error(str(error))
    if delivery.attempt_count >= delivery.max_attempts:
        delivery.status = ReceivableNotificationDelivery.Status.DEAD
        delivery.next_attempt_at = timezone.now() + timezone.timedelta(
            hours=DEAD_LETTER_HOURS
        )
    else:
        delivery.status = ReceivableNotificationDelivery.Status.FAILED
        delay_minutes = 2 ** delivery.attempt_count
        delivery.next_attempt_at = timezone.now() + timezone.timedelta(
            minutes=min(delay_minutes, 60)
        )
    delivery.save(update_fields=[
        'status', 'attempt_count', 'last_error',
        'next_attempt_at', 'updated_at',
    ])


def _mark_skipped(delivery, reason):
    delivery.status = ReceivableNotificationDelivery.Status.SKIPPED
    delivery.skip_reason = reason[:255]
    delivery.save(update_fields=['status', 'skip_reason', 'updated_at'])


def _send_email(boleto, subject, template_name, context, recipient_list):
    html_body = render_to_string(f'boletos/email/{template_name}.html', context)
    text_body = render_to_string(f'boletos/email/{template_name}.txt', context)
    send_mail(
        subject=subject,
        message=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        html_message=html_body,
        fail_silently=False,
    )


def _deliver_boleto_created(outbox_event):
    boleto = Boleto.objects.get(pk=outbox_event.payload['boleto_uuid'])
    context = {
        'boleto_uuid': str(boleto.uuid),
        'seller_name': boleto.seller.name,
        'amount': _format_brl(boleto.amount_cents),
        'due_date': boleto.due_date.isoformat(),
        'digitable_line': boleto.provider_digitable_line,
        'barcode': boleto.provider_barcode,
        'provider_url': boleto.provider_url,
    }
    sent_count = 0

    # Email ao cliente
    if boleto.payer_email:
        dk = _delivery_key(boleto.uuid, 'created', 'email', boleto.payer_email)
        delivery, already_sent = _register_delivery(
            outbox_event, 'email', boleto.payer_email, dk,
        )
        if already_sent:
            sent_count += 1
        elif delivery.status == ReceivableNotificationDelivery.Status.PENDING:
            delivery.status = ReceivableNotificationDelivery.Status.SENDING
            delivery.save(update_fields=['status', 'updated_at'])
            try:
                _send_email(
                    boleto,
                    subject=f'Boleto {_format_brl(boleto.amount_cents)} - Merito',
                    template_name='boleto_created',
                    context=context,
                    recipient_list=[boleto.payer_email],
                )
                _mark_sent(delivery)
                sent_count += 1
            except Exception as e:
                _mark_failed(delivery, e)
    else:
        delivery = ReceivableNotificationDelivery.objects.create(
            tenant=boleto.tenant,
            outbox_event=outbox_event,
            channel='email',
            recipient_hash=_recipient_hash(''),
            delivery_key=_delivery_key(boleto.uuid, 'created', 'email', ''),
            status=ReceivableNotificationDelivery.Status.SKIPPED,
            skip_reason='Sem email do pagador',
        )

    # Notificacao ao gestor
    gestores = UserModel.objects.filter(
        tenant=boleto.tenant,
        role__in=(UserModel.Role.ADMIN, UserModel.Role.MANAGER),
        email__isnull=False,
    ).exclude(email='').values_list('email', flat=True)
    if gestores:
        gestor_key = _delivery_key(
            boleto.uuid, 'created', 'gestor', str(boleto.tenant_id)
        )
        g_delivery, g_sent = _register_delivery(
            outbox_event, 'gestor', str(boleto.tenant_id), gestor_key,
        )
        if not g_sent and g_delivery.status == ReceivableNotificationDelivery.Status.PENDING:
            g_delivery.status = ReceivableNotificationDelivery.Status.SENDING
            g_delivery.save(update_fields=['status', 'updated_at'])
            try:
                _send_email(
                    boleto,
                    subject=f'[Merito] Novo boleto emitido - {boleto.seller.name}',
                    template_name='boleto_created',
                    context=context,
                    recipient_list=list(gestores),
                )
                _mark_sent(g_delivery)
            except Exception as e:
                _mark_failed(g_delivery, e)

    return sent_count


def _deliver_boleto_paid(outbox_event):
    boleto = Boleto.objects.get(pk=outbox_event.payload['boleto_uuid'])
    context = {
        'boleto_uuid': str(boleto.uuid),
        'seller_name': boleto.seller.name,
        'amount': _format_brl(boleto.amount_cents),
        'paid_amount': _format_brl(boleto.paid_amount_cents),
        'due_date': boleto.due_date.isoformat(),
    }

    if boleto.payer_email:
        dk = _delivery_key(boleto.uuid, 'paid', 'email', boleto.payer_email)
        delivery, already_sent = _register_delivery(
            outbox_event, 'email', boleto.payer_email, dk,
        )
        if not already_sent and delivery.status == ReceivableNotificationDelivery.Status.PENDING:
            delivery.status = ReceivableNotificationDelivery.Status.SENDING
            delivery.save(update_fields=['status', 'updated_at'])
            try:
                _send_email(
                    boleto,
                    subject='Seu boleto foi pago - Merito',
                    template_name='boleto_paid',
                    context=context,
                    recipient_list=[boleto.payer_email],
                )
                _mark_sent(delivery)
            except Exception as e:
                _mark_failed(delivery, e)

    gestores = UserModel.objects.filter(
        tenant=boleto.tenant,
        role__in=(UserModel.Role.ADMIN, UserModel.Role.MANAGER),
        email__isnull=False,
    ).exclude(email='').values_list('email', flat=True)
    if gestores:
        gk = _delivery_key(boleto.uuid, 'paid', 'gestor', str(boleto.tenant_id))
        g_delivery, g_sent = _register_delivery(
            outbox_event, 'gestor', str(boleto.tenant_id), gk,
        )
        if not g_sent and g_delivery.status == ReceivableNotificationDelivery.Status.PENDING:
            g_delivery.status = ReceivableNotificationDelivery.Status.SENDING
            g_delivery.save(update_fields=['status', 'updated_at'])
            try:
                _send_email(
                    boleto,
                    subject=f'[Merito] Boleto pago - {boleto.seller.name}',
                    template_name='boleto_paid',
                    context=context,
                    recipient_list=list(gestores),
                )
                _mark_sent(g_delivery)
            except Exception as e:
                _mark_failed(g_delivery, e)


def _deliver_boleto_canceled(outbox_event):
    boleto = Boleto.objects.get(pk=outbox_event.payload['boleto_uuid'])
    context = {
        'boleto_uuid': str(boleto.uuid),
        'seller_name': boleto.seller.name,
        'amount': _format_brl(boleto.amount_cents),
    }

    gestores = UserModel.objects.filter(
        tenant=boleto.tenant,
        role__in=(UserModel.Role.ADMIN, UserModel.Role.MANAGER),
        email__isnull=False,
    ).exclude(email='').values_list('email', flat=True)
    if gestores:
        gk = _delivery_key(boleto.uuid, 'canceled', 'gestor', str(boleto.tenant_id))
        g_delivery, g_sent = _register_delivery(
            outbox_event, 'gestor', str(boleto.tenant_id), gk,
        )
        if not g_sent and g_delivery.status == ReceivableNotificationDelivery.Status.PENDING:
            g_delivery.status = ReceivableNotificationDelivery.Status.SENDING
            g_delivery.save(update_fields=['status', 'updated_at'])
            try:
                _send_email(
                    boleto,
                    subject=f'[Merito] Boleto cancelado - {boleto.seller.name}',
                    template_name='boleto_canceled',
                    context=context,
                    recipient_list=list(gestores),
                )
                _mark_sent(g_delivery)
            except Exception as e:
                _mark_failed(g_delivery, e)


def _has_failed_deliveries(outbox_event):
    return ReceivableNotificationDelivery.objects.filter(
        outbox_event=outbox_event,
        status__in=(
            ReceivableNotificationDelivery.Status.FAILED,
            ReceivableNotificationDelivery.Status.DEAD,
        ),
    ).exists()


def _has_pending_deliveries(outbox_event):
    return ReceivableNotificationDelivery.objects.filter(
        outbox_event=outbox_event,
        status=ReceivableNotificationDelivery.Status.PENDING,
    ).exists()


def deliver_outbox_event(outbox_event):
    event_type = outbox_event.event_type
    handlers = {
        'boleto.created': _deliver_boleto_created,
        'boleto.paid': _deliver_boleto_paid,
        'boleto.canceled': _deliver_boleto_canceled,
    }
    handler = handlers.get(event_type)
    if not handler:
        return True
    handler(outbox_event)
    if _has_failed_deliveries(outbox_event) and not _has_pending_deliveries(outbox_event):
        return False
    return True
