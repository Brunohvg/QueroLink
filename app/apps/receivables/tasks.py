import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .services import (
    claim_outbox_events,
    complete_outbox_event,
    fail_outbox_event,
    apply_reconciliation_result,
)
from .models import Boleto
from .providers import get_provider


OUTBOX_HANDLERS = {}
logger = logging.getLogger(__name__)


def _handle_delivery(event):
    from .notification_services import deliver_outbox_event

    return deliver_outbox_event(event)


def _mark_boleto_awaiting_allocation(event):
    boleto_uuid = (event.payload or {}).get('boleto_uuid')
    awaiting_allocation = Boleto.objects.filter(
        pk=boleto_uuid,
        tenant=event.tenant,
        status=Boleto.Status.PAGO,
        allocations__isnull=True,
    ).exists()
    _project_customer_event(event)
    _handle_delivery(event)
    return awaiting_allocation


def _reverse_allocated_boleto(event):
    from .allocation_services import reverse_allocation
    from .models import ReceivableAllocation

    allocation = ReceivableAllocation.objects.filter(
        tenant=event.tenant,
        boleto_id=(event.payload or {}).get('boleto_uuid'),
        status=ReceivableAllocation.Status.ACTIVE,
    ).first()
    reversed_allocation = False
    if allocation:
        reverse_allocation(allocation, event.event_type)
        reversed_allocation = True
    _project_customer_event(event)
    return reversed_allocation


def _project_customer_event(event):
    from app.apps.customers.services import project_outbox_event

    return project_outbox_event(event)


def _project_canceled_boleto(event):
    result = _project_customer_event(event)
    _handle_delivery(event)
    return result


def _handle_boleto_created(event):
    from .notification_services import deliver_outbox_event

    return deliver_outbox_event(event)


OUTBOX_HANDLERS['boleto.paid'] = _mark_boleto_awaiting_allocation
OUTBOX_HANDLERS['boleto.refunded'] = _reverse_allocated_boleto
OUTBOX_HANDLERS['boleto.chargeback'] = _reverse_allocated_boleto
OUTBOX_HANDLERS['boleto.canceled'] = _project_canceled_boleto
OUTBOX_HANDLERS['boleto.created'] = _handle_boleto_created


@shared_task(soft_time_limit=300, time_limit=360)
def reconcile_pending_boletos(limit=None):
    from app.apps.accounts.models import Tenant

    try:
        requested_limit = int(limit or settings.RECEIVABLES_RECONCILE_BATCH_SIZE)
    except (TypeError, ValueError):
        requested_limit = settings.RECEIVABLES_RECONCILE_BATCH_SIZE
    batch_limit = max(1, min(requested_limit, 1000))
    now = timezone.now()
    creating_before = now - timedelta(
        minutes=settings.RECEIVABLES_RECONCILE_CREATING_MIN_AGE_MINUTES
    )
    stale_before = now - timedelta(
        minutes=settings.RECEIVABLES_RECONCILE_STALE_MINUTES
    )
    processed = 0
    changed = 0

    tenants = Tenant.objects.filter(receivables_enabled=True).order_by('uuid')
    for tenant in tenants.iterator():
        if processed >= batch_limit:
            break
        try:
            if not str(tenant.pagarme_api_key or '').strip():
                continue
            provider = get_provider(tenant)
            candidates = Boleto.objects.filter(tenant=tenant).filter(
                Q(status=Boleto.Status.CRIANDO, created_at__lte=creating_before)
                | Q(
                    status__in=[
                        Boleto.Status.PENDENTE,
                        Boleto.Status.CANCEL_PEND,
                        Boleto.Status.PAGO,
                    ]
                ) & (Q(last_synced_at__isnull=True) | Q(last_synced_at__lte=stale_before))
            ).order_by('created_at')[:batch_limit - processed]
            for boleto in candidates:
                try:
                    result = provider.retrieve_status(
                        tenant,
                        order_id=boleto.provider_order_id,
                        charge_id=boleto.provider_charge_id,
                        local_code=str(boleto.uuid),
                    )
                    _, did_change = apply_reconciliation_result(boleto, result)
                    changed += int(did_change)
                except Exception:
                    logger.warning(
                        'Falha ao reconciliar boleto tenant=%s boleto=%s',
                        tenant.uuid,
                        boleto.uuid,
                    )
                processed += 1
                if processed >= batch_limit:
                    break
        except Exception:
            logger.warning('Falha no lote de reconciliacao tenant=%s', tenant.uuid)
    logger.info('Reconciliacao de boletos processados=%s alterados=%s', processed, changed)
    return {'processed': processed, 'changed': changed}


@shared_task
def process_outbox_batch(limit=100):
    if not OUTBOX_HANDLERS:
        return 0
    events = claim_outbox_events(limit, event_types=list(OUTBOX_HANDLERS))
    for event in events:
        process_outbox_event.delay(str(event.uuid))
    return len(events)


@shared_task
def process_outbox_event(event_uuid):
    from .models import IntegrationOutbox, ReceivableNotificationDelivery

    event = IntegrationOutbox.objects.get(pk=event_uuid)
    handler = OUTBOX_HANDLERS.get(event.event_type)
    if handler is None:
        return False
    try:
        result = handler(event)
    except Exception as exc:
        fail_outbox_event(event.uuid, exc)
        raise

    if event.event_type == 'boleto.created':
        has_failures = ReceivableNotificationDelivery.objects.filter(
            outbox_event=event,
            status__in=(
                ReceivableNotificationDelivery.Status.FAILED,
                ReceivableNotificationDelivery.Status.DEAD,
            ),
        ).exists()
        has_pending = ReceivableNotificationDelivery.objects.filter(
            outbox_event=event,
            status=ReceivableNotificationDelivery.Status.PENDING,
        ).exists()
        if has_failures and not has_pending:
            fail_outbox_event(
                event.uuid,
                Exception('Notificacoes com falha permanente.'),
            )
            return False

    complete_outbox_event(event.uuid)
    return True


@shared_task(soft_time_limit=300, time_limit=360)
def send_boleto_due_reminders():
    from app.apps.accounts.models import Tenant, tenant_has_feature

    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)
    three_days = today + timedelta(days=3)

    tenants = Tenant.objects.filter(receivables_enabled=True).iterator()
    processed = 0
    for tenant in tenants:
        if not tenant_has_feature(tenant, 'boletos'):
            continue
        try:
            candidates = Boleto.objects.filter(
                tenant=tenant,
                status=Boleto.Status.PENDENTE,
            ).filter(
                Q(due_date__in=[tomorrow, three_days]) | Q(due_date__lt=today),
            ).select_related('seller')

            from .notification_services import (
                _delivery_key, _mark_failed, _mark_sent, _recipient_hash,
                _format_brl,
            )
            from app.services.messaging.whatsapp import WhatsappClient
            from .models import ReceivableNotificationDelivery

            for boleto in candidates:
                recipient = boleto.seller.phone or ''
                if not recipient:
                    continue
                today_dk = _delivery_key(
                    str(boleto.uuid), f'due_reminder_{today.isoformat()}',
                    'whatsapp', recipient,
                )
                delivery, _ = ReceivableNotificationDelivery.objects.get_or_create(
                    tenant=tenant,
                    delivery_key=today_dk,
                    defaults={
                        'channel': 'whatsapp',
                        'recipient_hash': _recipient_hash(recipient),
                        'status': ReceivableNotificationDelivery.Status.PENDING,
                    },
                )
                if delivery.status in (
                    ReceivableNotificationDelivery.Status.SENT,
                    ReceivableNotificationDelivery.Status.SKIPPED,
                    ReceivableNotificationDelivery.Status.DEAD,
                ):
                    continue
                if (
                    delivery.next_attempt_at
                    and delivery.next_attempt_at > timezone.now()
                ):
                    continue

                instance = tenant.whatsapp_instance_id
                api_key = tenant.whatsapp_token
                if not instance and getattr(
                    settings, 'WHATSAPP_ALLOW_SHARED_INSTANCE', False,
                ):
                    instance = settings.WHATSAPP_INSTANCE
                    api_key = api_key or settings.WHATSAPP_API_KEY
                if not instance:
                    delivery.status = ReceivableNotificationDelivery.Status.SKIPPED
                    delivery.skip_reason = 'Tenant sem instancia WhatsApp configurada'
                    delivery.save(update_fields=[
                        'status', 'skip_reason', 'updated_at',
                    ])
                    continue

                claimed = ReceivableNotificationDelivery.objects.filter(
                    pk=delivery.pk,
                    status__in=(
                        ReceivableNotificationDelivery.Status.PENDING,
                        ReceivableNotificationDelivery.Status.FAILED,
                    ),
                ).update(
                    status=ReceivableNotificationDelivery.Status.SENDING,
                    updated_at=timezone.now(),
                )
                if not claimed:
                    continue
                delivery.refresh_from_db()
                if boleto.due_date < today:
                    due_label = f'venceu em {boleto.due_date:%d/%m/%Y}'
                else:
                    due_label = f'vence em {boleto.due_date:%d/%m/%Y}'
                message = (
                    f'Lembrete: o boleto de {_format_brl(boleto.amount_cents)} '
                    f'{due_label}.'
                )
                try:
                    WhatsappClient(
                        instance=instance,
                        api_key=api_key,
                    ).send_message(recipient, message)
                except Exception as exc:
                    _mark_failed(delivery, exc)
                else:
                    _mark_sent(delivery)
                    processed += 1
        except Exception:
            logger.warning('Falha no lote de lembretes tenant=%s', tenant.uuid)
    logger.info('Lembretes de boleto enviados=%s', processed)
    return processed


@shared_task(soft_time_limit=60, time_limit=90)
def reprocess_stuck_outbox_events():
    from .models import IntegrationOutbox

    cutoff = timezone.now() - timedelta(hours=1)
    stuck = IntegrationOutbox.objects.filter(
        status__in=(
            IntegrationOutbox.Status.PROCESSING,
            IntegrationOutbox.Status.FAILED,
        ),
        updated_at__lt=cutoff,
    ).order_by('updated_at')[:50]

    count = 0
    for event in stuck:
        if event.status == IntegrationOutbox.Status.FAILED:
            event.status = IntegrationOutbox.Status.PENDING
            event.available_at = timezone.now()
            event.save(update_fields=['status', 'available_at', 'updated_at'])
            count += 1
        elif event.status == IntegrationOutbox.Status.PROCESSING:
            if event.processing_started_at and (
                timezone.now() - event.processing_started_at
            ) > timedelta(minutes=30):
                event.status = IntegrationOutbox.Status.PENDING
                event.processing_started_at = None
                event.available_at = timezone.now()
                event.save(
                    update_fields=[
                        'status', 'processing_started_at',
                        'available_at', 'updated_at',
                    ]
                )
                count += 1
    logger.info('Eventos da outbox reprocessados=%s', count)
    return count


@shared_task(soft_time_limit=60, time_limit=90)
def report_stuck_outbox_metrics():
    from .models import IntegrationOutbox

    failed = IntegrationOutbox.objects.filter(
        status=IntegrationOutbox.Status.FAILED,
    ).count()
    pending_old = IntegrationOutbox.objects.filter(
        status=IntegrationOutbox.Status.PENDING,
        available_at__lt=timezone.now() - timedelta(hours=6),
    ).count()
    logger.info(
        'Metricas outbox: failed=%s pending_old=%s', failed, pending_old,
    )
    return {'failed': failed, 'pending_old': pending_old}
