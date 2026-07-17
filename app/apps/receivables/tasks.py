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
