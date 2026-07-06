import logging

from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from app.apps.webhooks.models import WebhookEvent

logger = logging.getLogger(__name__)


def _skip_foreign_event(event, reason):
    logger.warning("Webhook %s ignorado: %s", event.id, reason)
    event.processed = True
    event.skip_reason = reason
    event.save(update_fields=['processed', 'skip_reason'])


VALID_PAYMENT_METHODS = {'credit_card', 'pix', 'boleto', 'unknown'}


def _normalize_payment_method(raw_method: str) -> str:
    method = (raw_method or '').strip().lower()
    if method in VALID_PAYMENT_METHODS:
        return method
    method_map = {
        'debit_card': 'unknown',
        'voucher': 'unknown',
        'cash': 'unknown',
    }
    return method_map.get(method, 'unknown')


@shared_task(
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=120,
    time_limit=180,
)
def process_pagarme_webhook(event_id):
    with transaction.atomic():
        try:
            event = WebhookEvent.objects.select_for_update(nowait=True).get(
                id=event_id, processed=False,
            )
        except WebhookEvent.DoesNotExist:
            logger.info("Webhook %s already processed or not found", event_id)
            return
        event.processed = True
        event.save(update_fields=['processed'])


def _safe_gateway_skip_reason(error):
    operation = getattr(error, 'operation', '') or 'mercadopago'
    status_code = getattr(error, 'status_code', None)
    if status_code is not None:
        return f'{operation} returned definitive status {status_code}'
    return f'{operation} definitive error'


def _handle_gateway_lookup_error(event, error):
    if isinstance(error, BaseException) and getattr(error, 'retryable', False):
        logger.warning(
            'Billing webhook %s retryable Mercado Pago error in %s status=%s',
            event.id,
            getattr(error, 'operation', 'mercadopago'),
            getattr(error, 'status_code', None),
        )
        raise error

    if hasattr(error, 'retryable') and not error.retryable:
        event.processed = True
        event.skip_reason = _safe_gateway_skip_reason(error)
        event.save(update_fields=['processed', 'skip_reason'])
        return

    logger.exception('Billing webhook %s unexpected Mercado Pago lookup error', event.id)
    raise error


@shared_task(
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=30,
)
def process_billing_webhook(event_id):
    from datetime import timedelta
    from django.db import transaction as db_transaction
    from app.apps.billing.models import Subscription
    from app.apps.accounts.models import Tenant
    from app.services.gateway.mercadopago import MercadoPagoGateway, MercadoPagoError

    try:
        event = WebhookEvent.objects.get(id=event_id)
    except WebhookEvent.DoesNotExist:
        return

    if event.processed:
        return

    payload = event.payload
    mp_type = payload.get('type', '')
    data = payload.get('data', {})

    tenant = None
    sub = None
    new_sub_status = None
    new_period_end = None
    sync_plan = False
    external_ref = ''
    preapproval_id = ''

    if mp_type == 'payment' and data.get('id'):
        payment_id = data['id']
        try:
            gateway = MercadoPagoGateway()
            payment = gateway.get_payment(payment_id)
        except MercadoPagoError as e:
            result = _handle_gateway_lookup_error(event, e)
            if result is None:
                return
        except Exception as e:
            logger.exception('Erro inesperado ao buscar payment %s', payment_id)
            raise

        external_ref = payment.get('external_reference', '')
        if external_ref:
            try:
                tenant = Tenant.objects.get(uuid=external_ref)
            except Tenant.DoesNotExist:
                pass

        if not tenant:
            metadata = payment.get('metadata') or {}
            preapproval_id = metadata.get('preapproval_id') or metadata.get('subscription_id', '')
            if preapproval_id:
                try:
                    sub = Subscription.objects.get(
                        gateway_subscription_id=preapproval_id,
                    )
                    tenant = sub.tenant
                except Subscription.DoesNotExist:
                    pass

        if not tenant:
            logger.warning(
                "Billing webhook: payment %s sem tenant via external_reference ou preapproval_id",
                payment_id,
            )
            event.processed = True
            event.skip_reason = 'tenant not found'
            event.save(update_fields=['processed', 'skip_reason'])
            return

        if not sub:
            try:
                sub = Subscription.objects.get(tenant=tenant)
            except Subscription.DoesNotExist:
                logger.warning(
                    "Billing webhook: subscription para tenant %s nao encontrada",
                    external_ref,
                )
                event.processed = True
                event.skip_reason = 'subscription not found'
                event.save(update_fields=['processed', 'skip_reason'])
                return

        payment_status = payment.get('status', '')
        if payment_status == 'approved':
            new_sub_status = Subscription.Status.ACTIVE
            period_days = 365 if sub.billing_cycle == 'YEARLY' else 30
            new_period_end = timezone.now() + timedelta(days=period_days)
            sync_plan = (tenant.plan != sub.plan)
        elif payment_status == 'rejected':
            new_sub_status = Subscription.Status.PAST_DUE

    elif mp_type == 'subscription_preapproval' and data.get('id'):
        preapproval_id = data['id']
        try:
            gateway = MercadoPagoGateway()
            preapproval = gateway.get_preapproval(preapproval_id)
        except MercadoPagoError as e:
            result = _handle_gateway_lookup_error(event, e)
            if result is None:
                return
        except Exception:
            logger.exception('Erro inesperado ao buscar preapproval %s', preapproval_id)
            raise

        external_ref = preapproval.get('external_reference', '')
        if not external_ref:
            logger.warning("Billing webhook: preapproval %s sem external_reference", preapproval_id)
            event.processed = True
            event.skip_reason = 'no external_reference'
            event.save(update_fields=['processed', 'skip_reason'])
            return

        try:
            tenant = Tenant.objects.get(uuid=external_ref)
        except Tenant.DoesNotExist:
            event.processed = True
            event.skip_reason = 'tenant not found'
            event.save(update_fields=['processed', 'skip_reason'])
            return

        try:
            sub = Subscription.objects.get(tenant=tenant)
        except Subscription.DoesNotExist:
            event.processed = True
            event.skip_reason = 'subscription not found'
            event.save(update_fields=['processed', 'skip_reason'])
            return

        mp_status = preapproval.get('status', '')
        if mp_status == 'authorized':
            new_sub_status = Subscription.Status.ACTIVE
            sync_plan = (tenant.plan != sub.plan)
        elif mp_status == 'cancelled':
            new_sub_status = Subscription.Status.CANCELED
        elif mp_status == 'past_due':
            new_sub_status = Subscription.Status.PAST_DUE

    if new_sub_status is None and not sync_plan:
        with db_transaction.atomic():
            try:
                event = WebhookEvent.objects.select_for_update().get(
                    id=event_id, processed=False,
                )
            except WebhookEvent.DoesNotExist:
                return
            event.processed = True
            event.save(update_fields=['processed'])
        return

    with db_transaction.atomic():
        try:
            locked_event = WebhookEvent.objects.select_for_update().get(
                id=event_id, processed=False,
            )
        except WebhookEvent.DoesNotExist:
            return

        locked_sub = Subscription.objects.select_for_update().get(pk=sub.pk)
        locked_tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)

        if locked_event.tenant is None and tenant:
            locked_event.tenant = tenant

        if new_sub_status is not None:
            locked_sub.status = new_sub_status
        if new_period_end is not None:
            locked_sub.current_period_end = new_period_end

        update_fields = ['status', 'updated_at']
        if new_period_end is not None:
            update_fields.append('current_period_end')

        locked_sub.save(update_fields=update_fields)

        if sync_plan:
            locked_tenant.plan = locked_sub.plan
            locked_tenant.save(update_fields=['plan', 'updated_at'])

        locked_event.processed = True
        locked_event.save(update_fields=['processed', 'tenant'])

        db_transaction.on_commit(
            lambda: cache.delete(f'tenant_operational:{tenant.uuid}')
        )

        if new_sub_status == Subscription.Status.ACTIVE:
            logger.info("Billing: tenant %s ACTIVE", external_ref)
        elif new_sub_status == Subscription.Status.PAST_DUE:
            logger.warning("Billing: tenant %s PAST_DUE", external_ref)
        elif new_sub_status == Subscription.Status.CANCELED:
            logger.info("Billing: subscription %s cancelada", preapproval_id)


@shared_task(soft_time_limit=300, time_limit=360)
def reconcile_pending_orders():
    logger.info('Reconcile pending orders skipped in this worker revision')


@shared_task(soft_time_limit=300, time_limit=360)
def cleanup_old_webhook_events():
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(days=90)
    total = 0
    while True:
        ids = list(WebhookEvent.objects.filter(
            processed=True, received_at__lt=cutoff,
        ).values_list('pk', flat=True)[:1000])
        if not ids:
            break
        deleted, _ = WebhookEvent.objects.filter(pk__in=ids).delete()
        total += deleted
    if total:
        logger.info("Cleaned up %d old webhook events", total)
