import logging

from celery import shared_task
from django.db import transaction, IntegrityError
from django.utils import timezone

from app.apps.webhooks.models import WebhookEvent
from app.apps.payments.models import Payment
from app.apps.orders.models import Order, PaymentLink
from app.apps.sales.models import Sale

logger = logging.getLogger(__name__)


def _skip_foreign_event(event, reason):
    """Marca evento como processado sem erro — evita retry de webhooks de outras plataformas."""
    logger.warning("Webhook %s ignorado: %s", event.id, reason)
    event.processed = True
    event.save(update_fields=['processed'])


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


def _populate_payment_from_webhook(payment, data, event_type):
    """Extrai dados do payload do webhook antes do PII scrub no save()."""
    charge = data
    if event_type == 'order.paid':
        charges = data.get('charges', [])
        charge = charges[0] if charges else {}

    txn = charge.get('last_transaction') or {}
    card = txn.get('card') or {}

    raw_method = charge.get('payment_method', '')
    payment.payment_method = _normalize_payment_method(raw_method) or payment.payment_method
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
                id=event_id, processed=False
            )
        except WebhookEvent.DoesNotExist:
            logger.info("Webhook %s already processed or not found", event_id)
            return

        if event.gateway_event_id:
            already_processed = WebhookEvent.objects.filter(
                gateway_event_id=event.gateway_event_id,
                processed=True,
            ).exclude(id=event.id).exists()
            if already_processed:
                logger.info(
                    "Webhook %s ja processado via outro evento (gateway_event_id=%s), ignorando",
                    event_id, event.gateway_event_id,
                )
                event.processed = True
                event.save(update_fields=['processed'])
                return

        payload = event.payload
        if not isinstance(payload, dict):
            raise ValueError("Payload is not a dictionary")

        event_type = payload.get('type')
        data = payload.get('data', {})
        logger.info("Processing webhook event %s type=%s", event_id, event_type)

        if event_type in ('order.paid', 'charge.paid', 'payment-link.finished'):
            order = None

            if event_type == 'order.paid':
                code = data.get('code', '')
                if code:
                    try:
                        from uuid import UUID
                        UUID(code)
                        try:
                            order = Order.objects.select_related('tenant', 'seller').get(uuid=code)
                            logger.info("order.paid resolvido via code (uuid)")
                        except Order.DoesNotExist:
                            pass
                    except ValueError:
                        pass

                if not order:
                    charges = data.get('charges', [])
                    if charges:
                        link_id = charges[0].get('payment_link_id', '')
                        if link_id:
                            try:
                                payment_link = PaymentLink.objects.select_related('order').get(
                                    gateway_link_id=link_id
                                )
                                order = payment_link.order
                                logger.info("order.paid resolvido via charges[0].payment_link_id")
                            except PaymentLink.DoesNotExist:
                                pass

                if not order:
                    gateway_order_id = data.get('id', '')
                    if gateway_order_id:
                        try:
                            payment = Payment.objects.select_related('order').get(
                                gateway_order_id=gateway_order_id
                            )
                            order = payment.order
                            logger.info("order.paid resolvido via Payment.gateway_order_id")
                        except Payment.DoesNotExist:
                            pass

                if not order:
                    _skip_foreign_event(
                        event,
                        f"Order nao encontrada para order.paid "
                        f"(code={data.get('code')}, id={data.get('id')}, plataforma externa)",
                    )
                    return

            elif event_type == 'payment-link.finished':
                link_id = data.get('id')
                try:
                    payment_link = PaymentLink.objects.select_related('order').get(
                        gateway_link_id=link_id
                    )
                    order = payment_link.order
                except PaymentLink.DoesNotExist:
                    _skip_foreign_event(
                        event,
                        f"PaymentLink nao encontrado (gateway_link_id={link_id}, plataforma externa)",
                    )
                    return

            elif event_type == 'charge.paid':
                order_data = data.get('order', {})
                order_code = order_data.get('code', '')
                if order_code:
                    try:
                        from uuid import UUID
                        UUID(order_code)
                        try:
                            order = Order.objects.select_related('tenant', 'seller').get(uuid=order_code)
                            logger.info("charge.paid resolvido via order.code")
                        except Order.DoesNotExist:
                            pass
                    except ValueError:
                        pass

                if not order:
                    link_id = order_data.get('payment_link', {}).get('id') or data.get('payment_link_id')
                    if link_id:
                        try:
                            payment_link = PaymentLink.objects.select_related('order').get(
                                gateway_link_id=link_id
                            )
                            order = payment_link.order
                        except PaymentLink.DoesNotExist:
                            pass

                if not order:
                    gateway_txn_id = data.get('id')
                    try:
                        payment = Payment.objects.select_related('order').get(
                            gateway_transaction_id=gateway_txn_id
                        )
                        order = payment.order
                    except Payment.DoesNotExist:
                        _skip_foreign_event(
                            event,
                            f"Payment nao encontrado para charge.paid "
                            f"(gateway_transaction_id={gateway_txn_id}, plataforma externa)",
                        )
                        return

            if not order:
                _skip_foreign_event(event, "Order nao encontrada (plataforma externa)")
                return

            payment = order.payments.order_by('created_at').first()
            if not payment:
                _skip_foreign_event(event, f"Payment ausente na Order {order.uuid}")
                return

            if event_type == 'order.paid':
                payment.gateway_order_id = data.get('id')
                charges = data.get('charges', [])
                if charges and isinstance(charges, list):
                    payment.gateway_transaction_id = charges[0].get('id')
            elif event_type == 'charge.paid':
                payment.gateway_transaction_id = data.get('id')
                payment.gateway_order_id = data.get('order', {}).get('id')

            if payment.status == Payment.Status.PAID:
                logger.info(
                    "Payment %s already PAID, skipping duplicate webhook",
                    payment.uuid,
                )
            else:
                if event_type == 'payment-link.finished':
                    if not payment.paid_at:
                        payment.paid_at = timezone.now()
                else:
                    _populate_payment_from_webhook(payment, data, event_type)
                payment.status = Payment.Status.PAID
                payment.raw_callback_payload = payload
                payment.save()

                order.status = Order.Status.COMPLETED
                order.save()

                try:
                    Sale.objects.get_or_create(
                        order=order,
                        defaults={
                            'tenant': order.tenant,
                            'seller': order.seller,
                            'origin': Sale.Origin.LINK,
                            'amount': order.total_amount,
                            'sale_date': timezone.localdate(),
                        },
                    )
                except IntegrityError:
                    logger.info(
                        "Sale for order %s already exists (concurrent webhook), skipping",
                        order.uuid,
                    )

                if order.seller:
                    from app.apps.notifications.tasks import notify_seller_link_status
                    notify_seller_link_status(order.seller, order, 'payment_paid')

        elif event_type in ('charge.payment_failed', 'order.payment_failed'):
            order = None
            gateway_txn_id = data.get('id')
            if gateway_txn_id:
                try:
                    payment = Payment.objects.select_related('order').get(
                        gateway_transaction_id=gateway_txn_id
                    )
                    order = payment.order
                except Payment.DoesNotExist:
                    pass
            if not order:
                link_id = data.get('order', {}).get('payment_link', {}).get('id')
                if link_id:
                    try:
                        payment_link = PaymentLink.objects.select_related('order').get(
                            gateway_link_id=link_id
                        )
                        order = payment_link.order
                    except PaymentLink.DoesNotExist:
                        pass
            if not order:
                _skip_foreign_event(event, "Order nao encontrada para payment_failed (plataforma externa)")
                return
            payment = order.payments.order_by('created_at').first()
            if not payment:
                _skip_foreign_event(event, f"Payment ausente na Order {order.uuid}")
                return
            payment.gateway_transaction_id = data.get('id')

            charge = data
            if event_type == 'order.payment_failed':
                charges = data.get('charges', [])
                charge = charges[0] if charges else {}
            txn = charge.get('last_transaction') or {}
            raw_method = charge.get('payment_method', '')
            payment.payment_method = _normalize_payment_method(raw_method) or payment.payment_method
            payment.installments = txn.get('installments') or payment.installments

            payment.status = Payment.Status.FAILED
            payment.raw_callback_payload = payload
            payment.save()
            if order.seller:
                last_txn = data.get('last_transaction') or {}
                motivo = (
                    last_txn.get('acquirer_message')
                    or last_txn.get('refusal_reason')
                    or ''
                )
                from app.apps.notifications.tasks import notify_seller_link_status
                notify_seller_link_status(
                    order.seller, order, 'payment_failed', motivo=motivo,
                )

        elif event_type == 'charge.refunded':
            gateway_txn_id = data.get('id')
            order = None
            if gateway_txn_id:
                try:
                    payment = Payment.objects.select_related('order').get(
                        gateway_transaction_id=gateway_txn_id
                    )
                    order = payment.order
                except Payment.DoesNotExist:
                    _skip_foreign_event(
                        event,
                        f"Payment nao encontrado para charge.refunded "
                        f"(gateway_transaction_id={gateway_txn_id}, plataforma externa)",
                    )
                    return
            if not order:
                _skip_foreign_event(event, "Order nao encontrada para charge.refunded")
                return
            payment.status = Payment.Status.REFUNDED
            payment.raw_callback_payload = payload
            payment.save()
            Sale.objects.filter(order=order).update(status='ESTORNADA')
            if order.seller:
                from app.apps.notifications.tasks import notify_seller_link_status
                notify_seller_link_status(order.seller, order, 'payment_refunded')

        elif event_type in ('payment-link.expired', 'payment-link.cancelled'):
            link_id = data.get('id')
            if link_id:
                try:
                    payment_link = PaymentLink.objects.select_related('order__seller').get(
                        gateway_link_id=link_id
                    )
                    order = payment_link.order
                except PaymentLink.DoesNotExist:
                    logger.warning(
                        "PaymentLink nao encontrado para %s gateway_link_id=%s",
                        event_type, link_id,
                    )
                    event.processed = True
                    event.save(update_fields=['processed'])
                    return
            else:
                _skip_foreign_event(event, f"{event_type}: gateway_link_id ausente no payload")
                return

            if event_type == 'payment-link.expired':
                if order.status == Order.Status.EXPIRED:
                    logger.info("Order %s already EXPIRED, skipping", order.uuid)
                else:
                    order.status = Order.Status.EXPIRED
                    order.save(update_fields=['status', 'updated_at'])
                    if order.seller:
                        from app.apps.notifications.tasks import notify_seller_link_status
                        notify_seller_link_status(order.seller, order, 'payment_expired')

            elif event_type == 'payment-link.cancelled':
                if order.status == Order.Status.CANCELED:
                    logger.info("Order %s already CANCELED, skipping", order.uuid)
                else:
                    order.status = Order.Status.CANCELED
                    order.save(update_fields=['status', 'updated_at'])
                    if order.seller:
                        from app.apps.notifications.tasks import notify_seller_link_status
                        notify_seller_link_status(order.seller, order, 'link_canceled')

        elif event_type == 'charge.chargedback':
            gateway_txn_id = data.get('id')
            order = None
            if gateway_txn_id:
                try:
                    payment = Payment.objects.select_related('order__seller').get(
                        gateway_transaction_id=gateway_txn_id
                    )
                    order = payment.order
                except Payment.DoesNotExist:
                    logger.warning(
                        "Payment nao encontrado para charge.chargedback "
                        "gateway_transaction_id=%s", gateway_txn_id,
                    )
                    event.processed = True
                    event.save(update_fields=['processed'])
                    return
            if not order:
                _skip_foreign_event(event, "Order nao encontrada para charge.chargedback")
                return
            if payment.status == Payment.Status.CHARGEBACK:
                logger.info("Payment %s already CHARGEBACK, skipping", payment.uuid)
            else:
                payment.status = Payment.Status.CHARGEBACK
                payment.raw_callback_payload = payload
                payment.save(update_fields=['status', 'raw_callback_payload', 'updated_at'])
                Sale.objects.filter(order=order).update(status='ESTORNADA')
                if order.seller:
                    from app.apps.notifications.tasks import notify_seller_link_status
                    notify_seller_link_status(order.seller, order, 'payment_chargeback')

        elif event_type in ('charge.antifraud_approved', 'charge.antifraud_reproved',
                            'charge.antifraud_manual', 'charge.antifraud_pending'):
            gateway_txn_id = data.get('id')
            antifraud = (data.get('last_transaction') or {}).get('antifraud_response') or {}
            antifraud_status = antifraud.get('status', '')
            antifraud_score = antifraud.get('score', '')
            logger.info(
                "Antifraud webhook: type=%s txn=%s status=%s score=%s",
                event_type, gateway_txn_id, antifraud_status, antifraud_score,
            )

            if event_type == 'charge.antifraud_reproved':
                order = None
                if gateway_txn_id:
                    try:
                        payment = Payment.objects.select_related('order__seller').get(
                            gateway_transaction_id=gateway_txn_id
                        )
                        order = payment.order
                    except Payment.DoesNotExist:
                        pass
                if not order:
                    _skip_foreign_event(event, f"Order nao encontrada para {event_type}")
                    return
                payment.status = Payment.Status.FAILED
                payment.raw_callback_payload = payload
                payment.save(update_fields=['status', 'raw_callback_payload', 'updated_at'])
                if order.seller:
                    motivo = f"Antifraude: {antifraud_status} (score: {antifraud_score})"
                    from app.apps.notifications.tasks import notify_seller_link_status
                    notify_seller_link_status(
                        order.seller, order, 'payment_failed', motivo=motivo,
                    )

        event.processed = True
        event.save(update_fields=['processed'])


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
