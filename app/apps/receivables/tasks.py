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


def _notify_boleto_event(event, event_slug):
    try:
        boleto = Boleto.objects.get(
            pk=(event.payload or {}).get('boleto_uuid'),
            tenant=event.tenant,
        )
        from .notification_services import (
            notify_boleto_paid,
            notify_boleto_canceled,
            notify_boleto_refunded,
            notify_boleto_chargeback,
            notify_boleto_awaiting_allocation,
        )
        switcher = {
            'boleto.paid': notify_boleto_paid,
            'boleto.canceled': notify_boleto_canceled,
            'boleto.refunded': notify_boleto_refunded,
            'boleto.chargeback': notify_boleto_chargeback,
        }
        notifier = switcher.get(event_slug)
        if notifier:
            notifier(boleto)
        if event_slug == 'boleto.paid' and not boleto.allocations.exists():
            notify_boleto_awaiting_allocation(boleto)
    except Boleto.DoesNotExist:
        logger.warning('Boleto nao encontrado para notificacao event=%s', event.uuid)
    except Exception:
        logger.exception(
            'Falha ao notificar evento %s event=%s', event_slug, event.uuid
        )


def _mark_boleto_awaiting_allocation(event):
    boleto_uuid = (event.payload or {}).get('boleto_uuid')
    awaiting_allocation = Boleto.objects.filter(
        pk=boleto_uuid,
        tenant=event.tenant,
        status=Boleto.Status.PAGO,
        allocations__isnull=True,
    ).exists()
    _project_customer_event(event)
    _notify_boleto_event(event, 'boleto.paid')
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
    _notify_boleto_event(
        event,
        'boleto.chargeback' if 'chargeback' in event.event_type else 'boleto.refunded',
    )
    return reversed_allocation


def _project_customer_event(event):
    from app.apps.customers.services import project_outbox_event

    return project_outbox_event(event)


def _project_canceled_boleto(event):
    result = _project_customer_event(event)
    _notify_boleto_event(event, 'boleto.canceled')
    return result


OUTBOX_HANDLERS['boleto.paid'] = _mark_boleto_awaiting_allocation
OUTBOX_HANDLERS['boleto.refunded'] = _reverse_allocated_boleto
OUTBOX_HANDLERS['boleto.chargeback'] = _reverse_allocated_boleto
OUTBOX_HANDLERS['boleto.canceled'] = _project_canceled_boleto


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
    from .models import IntegrationOutbox

    event = IntegrationOutbox.objects.get(pk=event_uuid)
    handler = OUTBOX_HANDLERS.get(event.event_type)
    if handler is None:
        return False
    try:
        handler(event)
    except Exception as exc:
        fail_outbox_event(event.uuid, exc)
        raise
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
            upcoming = Boleto.objects.filter(
                tenant=tenant,
                status=Boleto.Status.PENDENTE,
                due_date__in=[tomorrow, three_days],
            ).select_related('seller')

            overdue = Boleto.objects.filter(
                tenant=tenant,
                status=Boleto.Status.PENDENTE,
                due_date__lt=today,
            ).select_related('seller')

            from .notification_services import notify_boleto_due_reminder

            for boleto in upcoming:
                notify_boleto_due_reminder(boleto)
                processed += 1
            for boleto in overdue:
                notify_boleto_due_reminder(boleto)
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
