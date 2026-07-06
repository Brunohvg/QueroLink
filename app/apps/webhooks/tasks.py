import logging

from celery import shared_task
from django.core.cache import cache
from django.db import transaction, IntegrityError
from django.utils import timezone

from app.apps.webhooks.models import WebhookEvent
from app.apps.payments.models import Payment
from app.apps.orders.models import Order, PaymentLink
from app.apps.sales.models import Sale

logger = logging.getLogger(__name__)


def _skip_foreign_event(event, reason):
    """Marca evento como processado sem erro - evita retry de webhooks de outras plataformas."""
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


def _safe_gateway_skip_reason(error):
    operation = getattr(error, 'operation', '') or 'mercadopago'
    status_code = getattr(error, 'status_code', None)
    if status_code is not None:
        return f'{operation} returned definitive status {status_code}'
    return f'{operation} definitive error'


def _handle_gateway_lookup_error(event, error):
    if getattr(error, 'retryable', False):
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
        except Exception:
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
    from datetime import timedelta
    from app.services.gateway.pagar_me import PagarMeGateway, PagarMeError

    cutoff_start = timezone.now() - timedelta(days=7)
    cutoff_min_age = timezone.now() - timedelta(hours=2)

    orders = Order.objects.filter(
        status=Order.Status.PENDING,
        created_at__gte=cutoff_start,
        created_at__lte=cutoff_min_age,
        payment_link__gateway_link_id__isnull=False,
    ).select_related('tenant', 'payment_link').order_by('created_at')[:200]

    counted = 0
    paid_count = 0
    failed_count = 0
    expired_count = 0

    tenant_gateways = {}

    for order in orders:
        counted += 1
        try:
            tenant = order.tenant

            if tenant.uuid not in tenant_gateways:
                if not tenant.pagarme_api_key:
                    logger.info(
                        "Reconcile: tenant %s sem chave Pagar.me, pulando order %s",
                        tenant.slug, order.uuid,
                    )
                    continue
                try:
                    tenant_gateways[tenant.uuid] = PagarMeGateway(
                        api_key=tenant.pagarme_api_key,
                    )
                except PagarMeError:
                    logger.warning(
                        "Reconcile: erro ao criar gateway para tenant %s",
                        tenant.slug,
                    )
                    continue

            gateway = tenant_gateways[tenant.uuid]

            try:
                remote_order = gateway.find_order_by_code(str(order.uuid))
            except PagarMeError as e:
                logger.warning(
                    "Reconcile: erro ao consultar order %s: %s",
                    order.uuid, e,
                )
                continue

            if remote_order is None:
                link = order.payment_link
                if link and link.expires_at and link.expires_at < timezone.now() - timedelta(hours=24):
                    order.status = Order.Status.EXPIRED
                    order.save(update_fields=['status', 'updated_at'])
                    expired_count += 1
                continue

            remote_status = remote_order.get('status', '')

            event_id_str = f'reconcile_{order.uuid}'
            existing = WebhookEvent.objects.filter(gateway_event_id=event_id_str).first()
            if existing:
                if existing.processed and order.status == Order.Status.PENDING:
                    logger.warning(
                        "Reconcile: evento %s ja processado mas order %s segue PENDING "
                        "(skip_reason=%s) - investigar correlacao",
                        event_id_str, order.uuid, existing.skip_reason,
                    )
                continue

            if remote_status == 'paid':
                synthetic_payload = {
                    'type': 'order.paid',
                    'data': remote_order,
                    'id': event_id_str,
                }
                event = WebhookEvent.objects.create(
                    gateway='pagarme',
                    payload=synthetic_payload,
                    gateway_event_id=event_id_str,
                    tenant=tenant,
                )
                process_pagarme_webhook.delay(event.id)
                paid_count += 1

            elif remote_status in ('failed', 'canceled'):
                synthetic_payload = {
                    'type': 'order.payment_failed',
                    'data': remote_order,
                    'id': event_id_str,
                }
                event = WebhookEvent.objects.create(
                    gateway='pagarme',
                    payload=synthetic_payload,
                    gateway_event_id=event_id_str,
                    tenant=tenant,
                )
                process_pagarme_webhook.delay(event.id)
                failed_count += 1

            else:
                link = order.payment_link
                if link and link.expires_at and link.expires_at < timezone.now() - timedelta(hours=24):
                    order.status = Order.Status.EXPIRED
                    order.save(update_fields=['status', 'updated_at'])
                    expired_count += 1

        except Exception:
            logger.exception(
                "Reconcile: erro inesperado processando order %s, continuando batch",
                order.uuid,
            )
            continue

    logger.info(
        "Reconciliacao: %d verificadas, %d pagas, %d falhas, %d expiradas",
        counted, paid_count, failed_count, expired_count,
    )


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
