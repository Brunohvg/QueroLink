import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from app.apps.orders.models import Order, PaymentLink
from app.apps.payments.models import Payment
from app.apps.webhooks.models import WebhookEvent

logger = logging.getLogger(__name__)


VALID_PAYMENT_METHODS = {'credit_card', 'pix', 'boleto', 'unknown'}
PAID_EVENT_TYPES = ('order.paid', 'charge.paid', 'payment-link.finished')


@dataclass
class PagarmePaymentResult:
    status: str
    event_type: str = ''
    order_uuid: str = ''
    payment_uuid: str = ''
    sale_created: bool = False
    financial_changed: bool = False
    notify_event_type: str = ''
    message: str = ''


def normalize_payment_method(raw_method: str) -> str:
    method = (raw_method or '').strip().lower()
    if method in VALID_PAYMENT_METHODS:
        return method
    method_map = {
        'debit_card': 'unknown',
        'voucher': 'unknown',
        'cash': 'unknown',
    }
    return method_map.get(method, 'unknown')


def populate_payment_from_webhook(payment, data, event_type):
    """Extrai dados do payload do webhook antes do PII scrub no save()."""
    charge = data
    if event_type == 'order.paid':
        charges = data.get('charges', [])
        charge = charges[0] if charges else {}

    txn = charge.get('last_transaction') or {}
    card = txn.get('card') or {}

    raw_method = charge.get('payment_method', '')
    payment.payment_method = normalize_payment_method(raw_method) or payment.payment_method
    payment.installments = txn.get('installments') or payment.installments

    paid_at = charge.get('paid_at')
    if paid_at:
        payment.paid_at = paid_at

    brand = card.get('brand', '')
    if brand:
        payment.card_brand = brand

    last4 = card.get('last_four_digits', '')
    if last4:
        payment.card_last4 = str(last4)


def mark_event_skipped(event, reason):
    logger.warning("Webhook %s ignorado: %s", event.id, reason)
    event.processed = True
    event.status = WebhookEvent.Status.SKIPPED
    event.skip_reason = reason
    event.processed_at = timezone.now()
    event.save(update_fields=['processed', 'status', 'skip_reason', 'processed_at'])


def mark_event_failed(event, error):
    event.status = WebhookEvent.Status.FAILED
    event.processing_error = str(error)[:4000]
    event.save(update_fields=['status', 'processing_error'])


def _resolve_order_for_paid_event(event_type, data):
    order = None

    if event_type == 'order.paid':
        code = data.get('code', '')
        if code:
            try:
                from uuid import UUID
                UUID(code)
                order = Order.objects.select_for_update(of=('self',)).select_related(
                    'tenant', 'seller',
                ).get(uuid=code)
                logger.info("order.paid resolvido via code (uuid)")
            except (ValueError, Order.DoesNotExist):
                order = None

        if not order:
            charges = data.get('charges', [])
            if charges:
                link_id = charges[0].get('payment_link_id', '')
                if link_id:
                    try:
                        payment_link = PaymentLink.objects.select_related(
                            'order__tenant', 'order__seller',
                        ).select_for_update(of=('self',)).get(gateway_link_id=link_id)
                        order = payment_link.order
                        logger.info("order.paid resolvido via charges[0].payment_link_id")
                    except PaymentLink.DoesNotExist:
                        pass

        if not order:
            gateway_order_id = data.get('id', '')
            if gateway_order_id:
                try:
                    payment = Payment.objects.select_related(
                        'order__tenant', 'order__seller',
                    ).select_for_update(of=('self',)).get(gateway_order_id=gateway_order_id)
                    order = payment.order
                    logger.info("order.paid resolvido via Payment.gateway_order_id")
                except Payment.DoesNotExist:
                    pass

    elif event_type == 'payment-link.finished':
        link_id = data.get('id')
        try:
            payment_link = PaymentLink.objects.select_related(
                'order__tenant', 'order__seller',
            ).select_for_update(of=('self',)).get(gateway_link_id=link_id)
            order = payment_link.order
        except PaymentLink.DoesNotExist:
            pass

    elif event_type == 'charge.paid':
        order_data = data.get('order', {})
        order_code = order_data.get('code') or data.get('code', '')
        if order_code:
            try:
                from uuid import UUID
                UUID(order_code)
                order = Order.objects.select_for_update(of=('self',)).select_related(
                    'tenant', 'seller',
                ).get(uuid=order_code)
                logger.info("charge.paid resolvido via order.code")
            except (ValueError, Order.DoesNotExist):
                order = None

        if not order:
            link_id = (
                order_data.get('payment_link', {}).get('id')
                or data.get('payment_link_id')
                or (data.get('metadata') or {}).get('payment_link_id')
                or (order_data.get('metadata') or {}).get('payment_link_id')
            )
            if link_id:
                try:
                    payment_link = PaymentLink.objects.select_related(
                        'order__tenant', 'order__seller',
                    ).select_for_update(of=('self',)).get(gateway_link_id=link_id)
                    order = payment_link.order
                except PaymentLink.DoesNotExist:
                    pass

        if not order:
            gateway_txn_id = data.get('id')
            if gateway_txn_id:
                try:
                    payment = Payment.objects.select_related(
                        'order__tenant', 'order__seller',
                    ).select_for_update(of=('self',)).get(gateway_transaction_id=gateway_txn_id)
                    order = payment.order
                except Payment.DoesNotExist:
                    pass

    return order


def _apply_gateway_ids(payment, data, event_type):
    if event_type == 'order.paid':
        payment.gateway_order_id = data.get('id')
        charges = data.get('charges', [])
        if charges and isinstance(charges, list):
            payment.gateway_transaction_id = charges[0].get('id')
    elif event_type == 'charge.paid':
        payment.gateway_transaction_id = data.get('id')
        payment.gateway_order_id = data.get('order', {}).get('id')


def process_paid_pagarme_event(event_id, *, dry_run=False):
    if dry_run:
        with transaction.atomic():
            event = WebhookEvent.objects.get(id=event_id, gateway='pagarme')
            return _process_paid_locked_event(event, dry_run=True)

    try:
        with transaction.atomic():
            event = WebhookEvent.objects.select_for_update().get(id=event_id, gateway='pagarme')
            if event.processed or event.status in (
                WebhookEvent.Status.PROCESSED,
                WebhookEvent.Status.SKIPPED,
            ):
                return PagarmePaymentResult(
                    status='duplicate',
                    message='Evento ja processado.',
                )

            event.status = WebhookEvent.Status.PROCESSING
            event.processing_started_at = timezone.now()
            event.last_attempt_at = timezone.now()
            event.attempt_count += 1
            event.processing_error = ''
            event.save(update_fields=[
                'status', 'processing_started_at', 'last_attempt_at',
                'attempt_count', 'processing_error',
            ])

            return _process_paid_locked_event(event, dry_run=False)
    except Exception as exc:
        WebhookEvent.objects.filter(id=event_id, gateway='pagarme').update(
            status=WebhookEvent.Status.FAILED,
            processing_error=str(exc)[:4000],
        )
        raise


def _process_paid_locked_event(event, *, dry_run):
    payload = event.payload
    if not isinstance(payload, dict):
        raise ValueError("Payload is not a dictionary")

    event_type = payload.get('type')
    data = payload.get('data', {})
    if event_type not in PAID_EVENT_TYPES:
        raise ValueError(f"Evento {event_type} nao e evento de pagamento confirmado")

    order = _resolve_order_for_paid_event(event_type, data)
    if not order:
        if not dry_run:
            mark_event_skipped(event, "Order nao encontrada para pagamento confirmado")
        return PagarmePaymentResult(
            status='skipped',
            event_type=event_type,
            message='Order nao encontrada.',
        )

    if event.tenant_id and order.tenant_id != event.tenant_id:
        reason = f"Tenant divergente para Order {order.uuid}"
        if not dry_run:
            mark_event_skipped(event, reason)
        return PagarmePaymentResult(
            status='skipped',
            event_type=event_type,
            order_uuid=str(order.uuid),
            message=reason,
        )

    order = Order.objects.select_for_update(of=('self',)).select_related(
        'tenant', 'seller',
    ).get(uuid=order.uuid)

    payment = Payment.objects.select_for_update().filter(order=order).order_by('created_at').first()
    if not payment:
        reason = f"Payment ausente na Order {order.uuid}"
        if not dry_run:
            mark_event_skipped(event, reason)
        return PagarmePaymentResult(
            status='skipped',
            event_type=event_type,
            order_uuid=str(order.uuid),
            message=reason,
        )

    financial_changed = payment.status != Payment.Status.PAID or order.status != Order.Status.COMPLETED

    if dry_run:
        return PagarmePaymentResult(
            status='dry_run',
            event_type=event_type,
            order_uuid=str(order.uuid),
            payment_uuid=str(payment.uuid),
            sale_created=False,
            financial_changed=financial_changed,
            notify_event_type='payment_paid' if financial_changed and order.seller_id else '',
            message='Dry-run sem alteracao.',
        )

    _apply_gateway_ids(payment, data, event_type)
    if event_type == 'payment-link.finished':
        if not payment.paid_at:
            payment.paid_at = timezone.now()
    else:
        populate_payment_from_webhook(payment, data, event_type)

    payment.status = Payment.Status.PAID
    payment.raw_callback_payload = payload
    payment.save()

    order.status = Order.Status.COMPLETED
    order.save(update_fields=['status', 'updated_at'])

    event.processed = True
    event.status = WebhookEvent.Status.PROCESSED
    event.processed_at = timezone.now()
    event.processing_error = ''
    event.save(update_fields=['processed', 'status', 'processed_at', 'processing_error'])

    return PagarmePaymentResult(
        status='processed',
        event_type=event_type,
        order_uuid=str(order.uuid),
        payment_uuid=str(payment.uuid),
        sale_created=False,
        financial_changed=financial_changed,
        notify_event_type='payment_paid' if financial_changed and order.seller_id else '',
        message='Pagamento confirmado.',
    )
