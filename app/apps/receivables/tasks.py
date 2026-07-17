from celery import shared_task

from .services import (
    claim_outbox_events,
    complete_outbox_event,
    fail_outbox_event,
)


OUTBOX_HANDLERS = {}


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
