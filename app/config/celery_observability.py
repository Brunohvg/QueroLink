import logging
import time

from celery.signals import task_failure, task_postrun, task_prerun

logger = logging.getLogger('app.observability.celery')

OBSERVED_TASKS = {
    'app.apps.accounts.tasks.daily_backup',
    'app.apps.notifications.tasks.requeue_stuck_notifications',
    'app.apps.notifications.tasks.send_accounting_package_email',
    'app.apps.notifications.tasks.send_daily_entry_reminders',
    'app.apps.notifications.tasks.send_lifecycle_emails',
    'app.apps.notifications.tasks.send_whatsapp_notification',
    'app.apps.webhooks.tasks.cleanup_old_webhook_events',
    'app.apps.webhooks.tasks.process_billing_webhook',
    'app.apps.webhooks.tasks.process_pagarme_webhook',
    'app.apps.webhooks.tasks.reconcile_pending_orders',
    'app.apps.receivables.tasks.reconcile_pending_boletos',
    'app.apps.receivables.tasks.process_outbox_batch',
    'app.apps.receivables.tasks.process_outbox_event',
    'app.apps.receivables.tasks.send_boleto_due_reminders',
    'app.apps.receivables.tasks.reprocess_stuck_outbox_events',
    'app.apps.receivables.tasks.report_stuck_outbox_metrics',
}

_TASK_STARTS = {}


def _is_observed(task_name):
    return task_name in OBSERVED_TASKS


def _duration_ms(started_at):
    if started_at is None:
        return None
    return int((time.monotonic() - started_at) * 1000)


def _log(event, task_name, task_id, **extra):
    payload = {
        'event': event,
        'task_name': task_name,
        'task_id': task_id,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    logger.info('celery_task_event', extra={'structured': payload})


@task_prerun.connect
def log_task_start(sender=None, task_id=None, task=None, **kwargs):
    task_name = getattr(sender, 'name', None) or getattr(task, 'name', None)
    if not _is_observed(task_name):
        return

    _TASK_STARTS[(task_name, task_id)] = time.monotonic()
    _log('start', task_name, task_id)


@task_postrun.connect
def log_task_success(sender=None, task_id=None, task=None, state=None, retval=None, **kwargs):
    task_name = getattr(sender, 'name', None) or getattr(task, 'name', None)
    if not _is_observed(task_name) or state != 'SUCCESS':
        return

    started_at = _TASK_STARTS.pop((task_name, task_id), None)
    _log('success', task_name, task_id, duration_ms=_duration_ms(started_at))


@task_failure.connect
def log_task_failure(sender=None, task_id=None, exception=None, einfo=None, traceback=None, **kwargs):
    task_name = getattr(sender, 'name', None)
    if not _is_observed(task_name):
        return

    started_at = _TASK_STARTS.pop((task_name, task_id), None)
    payload = {
        'structured': {
            'event': 'failure',
            'task_name': task_name,
            'task_id': task_id,
            'duration_ms': _duration_ms(started_at),
            'exception_class': exception.__class__.__name__ if exception else None,
        }
    }
    if einfo is not None:
        payload['structured']['traceback'] = str(einfo.traceback)
    logger.error('celery_task_event', extra=payload)
