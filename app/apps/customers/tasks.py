from celery import shared_task


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def project_customer_outbox_event(event_uuid):
    from app.apps.receivables.models import IntegrationOutbox

    from .services import project_outbox_event

    event = IntegrationOutbox.objects.select_related('tenant').get(pk=event_uuid)
    project_outbox_event(event)
    return True
