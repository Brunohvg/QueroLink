import json
import hmac
import hashlib
import logging
import time
from datetime import timedelta

from django.db import IntegrityError, OperationalError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from app.apps.webhooks.models import WebhookEvent
from app.apps.accounts.fields import scrub_payment_payload

logger = logging.getLogger(__name__)

PROCESSING_TIMEOUT_MINUTES = 10


def _get_or_create_webhook_event(gateway, payload, gateway_event_id=None, tenant=None):
    if not gateway_event_id:
        return WebhookEvent.objects.create(
            gateway=gateway,
            payload=payload,
            gateway_event_id=None,
            tenant=tenant,
        ), True

    for attempt in range(6):
        try:
            event = WebhookEvent.objects.filter(
                gateway=gateway,
                gateway_event_id=gateway_event_id,
            ).first()
            if event:
                return event, False

            return WebhookEvent.objects.create(
                gateway=gateway,
                gateway_event_id=gateway_event_id,
                payload=payload,
                tenant=tenant,
            ), True
        except IntegrityError:
            event = WebhookEvent.objects.filter(
                gateway=gateway,
                gateway_event_id=gateway_event_id,
            ).first()
            if event:
                return event, False
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))
        except OperationalError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))

    return WebhookEvent.objects.get(
        gateway=gateway,
        gateway_event_id=gateway_event_id,
    ), False


def _pagarme_existing_event_action(event):
    if event.processed or event.status in (
        WebhookEvent.Status.PROCESSED,
        WebhookEvent.Status.SKIPPED,
    ):
        return 'duplicate'

    if event.status in (WebhookEvent.Status.RECEIVED, WebhookEvent.Status.FAILED):
        return 'reenqueue'

    if event.status == WebhookEvent.Status.PROCESSING:
        started_at = event.processing_started_at or event.last_attempt_at or event.received_at
        if started_at and started_at >= timezone.now() - timedelta(
            minutes=PROCESSING_TIMEOUT_MINUTES,
        ):
            return 'processing'
        return 'recover'

    return 'reenqueue'


def _verify_webhook_token(tenant_uuid, token):
    from django.conf import settings
    secret = getattr(settings, 'WHATSAPP_API_KEY', None) or settings.SECRET_KEY
    expected = hmac.new(
        secret.encode(),
        str(tenant_uuid).encode(),
        hashlib.sha256,
    ).hexdigest()[:16]
    return hmac.compare_digest(expected, token)


def _add_if_present(target, value):
    if isinstance(value, str) and value.strip():
        target.add(value.strip())


def _pagarme_business_payload_belongs_to_tenant(payload, tenant):
    """Retorna False para eventos Pagar.me de pagamento que nao sao do Merito."""
    if not isinstance(payload, dict):
        return False

    event_type = payload.get('type') or ''
    if not (
        event_type.startswith('charge.')
        or event_type.startswith('order.')
        or event_type.startswith('payment-link.')
    ):
        return True

    data = payload.get('data') or {}
    if not isinstance(data, dict):
        return False

    order_codes = set()
    link_ids = set()
    charge_ids = set()
    gateway_order_ids = set()

    order_data = data.get('order') or {}
    if not isinstance(order_data, dict):
        order_data = {}

    metadata = data.get('metadata') or {}
    if not isinstance(metadata, dict):
        metadata = {}
    order_metadata = order_data.get('metadata') or {}
    if not isinstance(order_metadata, dict):
        order_metadata = {}

    _add_if_present(order_codes, data.get('code'))
    _add_if_present(order_codes, order_data.get('code'))
    _add_if_present(gateway_order_ids, order_data.get('id'))
    if event_type.startswith('order.'):
        _add_if_present(gateway_order_ids, data.get('id'))

    payment_link = order_data.get('payment_link') or {}
    if isinstance(payment_link, dict):
        _add_if_present(link_ids, payment_link.get('id'))
    _add_if_present(link_ids, data.get('payment_link_id'))
    _add_if_present(link_ids, metadata.get('payment_link_id'))
    _add_if_present(link_ids, order_metadata.get('payment_link_id'))
    if event_type.startswith('payment-link.'):
        _add_if_present(link_ids, data.get('id'))
    if event_type.startswith('charge.'):
        _add_if_present(charge_ids, data.get('id'))

    charges = data.get('charges') or []
    if isinstance(charges, list):
        for charge in charges:
            if not isinstance(charge, dict):
                continue
            _add_if_present(charge_ids, charge.get('id'))
            _add_if_present(link_ids, charge.get('payment_link_id'))
            charge_metadata = charge.get('metadata') or {}
            if isinstance(charge_metadata, dict):
                _add_if_present(link_ids, charge_metadata.get('payment_link_id'))

    from app.apps.orders.models import Order, PaymentLink
    from app.apps.payments.models import Payment
    from app.apps.receivables.providers import get_provider
    from app.apps.webhooks.services import find_boleto_for_webhook
    from uuid import UUID

    valid_order_uuids = []
    for code in order_codes:
        try:
            valid_order_uuids.append(UUID(code))
        except (TypeError, ValueError):
            continue
    if valid_order_uuids and Order.objects.filter(
        tenant=tenant, uuid__in=valid_order_uuids,
    ).exists():
        return True

    if link_ids and PaymentLink.objects.filter(
        order__tenant=tenant, gateway_link_id__in=link_ids,
    ).exists():
        return True

    if charge_ids and Payment.objects.filter(
        order__tenant=tenant, gateway_transaction_id__in=charge_ids,
    ).exists():
        return True

    if gateway_order_ids and Payment.objects.filter(
        order__tenant=tenant, gateway_order_id__in=gateway_order_ids,
    ).exists():
        return True

    try:
        normalized = get_provider(tenant).parse_webhook(payload)
    except Exception:
        normalized = None
    if normalized and find_boleto_for_webhook(tenant, normalized):
        return True

    return False


@csrf_exempt
def pagarme_webhook(request, tenant_slug):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    from app.apps.accounts.models import Tenant as TenantModel
    try:
        tenant = TenantModel.objects.get(slug=tenant_slug)
    except TenantModel.DoesNotExist:
        logger.warning("Webhook recebido para tenant_slug inexistente: %s", tenant_slug)
        return JsonResponse({"error": "Not found"}, status=404)

    from django.conf import settings
    auth_required = getattr(settings, 'WEBHOOK_AUTH_REQUIRED', True)

    if tenant.pagarme_webhook_username and tenant.pagarme_webhook_password:
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Basic '):
            logger.warning("Webhook sem Basic Auth para tenant %s", tenant_slug)
            return JsonResponse({"error": "Unauthorized"}, status=401)

        import base64
        try:
            decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
            username, password = decoded.split(':', 1)
        except Exception:
            logger.warning("Webhook Basic Auth mal formatado para tenant %s", tenant_slug)
            return JsonResponse({"error": "Unauthorized"}, status=401)

        if username != tenant.pagarme_webhook_username or password != tenant.pagarme_webhook_password:
            logger.warning("Webhook credenciais invalidas para tenant %s", tenant_slug)
            return JsonResponse({"error": "Unauthorized"}, status=401)
    elif auth_required:
        logger.warning(
            "Webhook rejeitado: tenant %s sem credenciais de webhook configuradas",
            tenant_slug,
        )
        return JsonResponse({"error": "Unauthorized"}, status=401)
    else:
        logger.warning(
            "Webhook permitido sem auth: tenant %s sem credenciais (WEBHOOK_AUTH_REQUIRED=False)",
            tenant_slug,
        )

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not _pagarme_business_payload_belongs_to_tenant(payload, tenant):
        logger.info(
            "Webhook Pagar.me externo ignorado sem persistir: tenant=%s type=%s",
            tenant_slug, payload.get('type') if isinstance(payload, dict) else '?',
        )
        return JsonResponse({"status": "ignored_foreign"}, status=200)

    from app.apps.webhooks.services import generate_receipt_id, generate_correlation_id

    sanitized = scrub_payment_payload(payload)
    gateway_event_id = payload.get('id') or ''
    receipt_id = generate_receipt_id()
    correlation_id = generate_correlation_id()

    event, created = _get_or_create_webhook_event(
        gateway='pagarme',
        payload=sanitized,
        gateway_event_id=gateway_event_id or None,
        tenant=tenant,
    )
    if not created:
        action = _pagarme_existing_event_action(event)
        if action == 'duplicate':
            logger.info("Webhook duplicado ignorado: gateway_event_id=%s", gateway_event_id)
            return JsonResponse(
                {"received": True, "processor": "merito-webhooks", "version": "v1",
                 "event_id": gateway_event_id, "receipt_id": receipt_id,
                 "correlation_id": correlation_id, "duplicate": True},
                status=200,
                headers={
                    "X-Merito-Webhook": "accepted",
                    "X-Merito-Receipt-Id": receipt_id,
                    "X-Correlation-Id": correlation_id,
                },
            )
        if action == 'processing':
            logger.info("Webhook ja em processamento: gateway_event_id=%s", gateway_event_id)
            return JsonResponse(
                {"received": True, "processor": "merito-webhooks", "version": "v1",
                 "event_id": gateway_event_id, "receipt_id": receipt_id,
                 "correlation_id": correlation_id, "duplicate": True},
                status=200,
                headers={
                    "X-Merito-Webhook": "accepted",
                    "X-Merito-Receipt-Id": receipt_id,
                    "X-Correlation-Id": correlation_id,
                },
            )

        if action == 'recover':
            event.status = WebhookEvent.Status.RECEIVED
            event.processing_error = ''
            event.processing_started_at = None
            event.receipt_id = receipt_id
            event.correlation_id = correlation_id
            event.save(update_fields=[
                'status', 'processing_error', 'processing_started_at',
                'receipt_id', 'correlation_id',
            ])
        from app.apps.webhooks.tasks import process_pagarme_webhook
        process_pagarme_webhook.delay(event.id)
        return JsonResponse(
            {"received": True, "processor": "merito-webhooks", "version": "v1",
             "event_id": gateway_event_id, "receipt_id": receipt_id,
             "correlation_id": correlation_id, "duplicate": False},
            status=200,
            headers={
                "X-Merito-Webhook": "accepted",
                "X-Merito-Receipt-Id": receipt_id,
                "X-Correlation-Id": correlation_id,
            },
        )

    event.receipt_id = receipt_id
    event.correlation_id = correlation_id
    event.save(update_fields=['receipt_id', 'correlation_id'])

    from app.apps.webhooks.tasks import process_pagarme_webhook
    process_pagarme_webhook.delay(event.id)
    return JsonResponse(
        {"received": True, "processor": "merito-webhooks", "version": "v1",
         "event_id": gateway_event_id, "receipt_id": receipt_id,
         "correlation_id": correlation_id, "duplicate": False},
        status=200,
        headers={
            "X-Merito-Webhook": "accepted",
            "X-Merito-Receipt-Id": receipt_id,
            "X-Correlation-Id": correlation_id,
        },
    )


@csrf_exempt
def evolution_webhook(request, instance_name, tenant_uuid, token):
    try:
        if request.method != "POST":
            return JsonResponse({"error": "Method not allowed"}, status=405)

        if not _verify_webhook_token(tenant_uuid, token):
            return JsonResponse({"error": "Forbidden"}, status=403)

        payload = json.loads(request.body)
        event_type = payload.get('event', '')
        data = payload.get('data', {})

        logger.info(
            "Evolution webhook: instance=%s tenant=%s event=%s",
            instance_name, tenant_uuid, event_type,
        )

        extra = {}
        if event_type == 'CONNECTION_UPDATE':
            state = data.get('state') or data.get('instance', {}).get('state', '')
            extra['state'] = state
        elif event_type == 'QRCODE_UPDATE':
            pass

        _get_or_create_webhook_event(
            gateway='evolution',
            payload={
                'event': event_type,
                'instance': instance_name,
                'tenant_uuid': str(tenant_uuid),
                **extra,
            },
        )

        return JsonResponse({"status": "received"}, status=200)
    except json.JSONDecodeError:
        logger.warning("Evolution webhook invalid JSON from %s", instance_name)
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error("Evolution webhook error: %s", e)
        return JsonResponse({"error": "Internal error"}, status=500)


@csrf_exempt
def billing_webhook(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    raw_body = request.body

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    from django.conf import settings
    webhook_secret = getattr(settings, 'MP_WEBHOOK_SECRET', '')
    if not webhook_secret:
        logger.critical('Billing webhook rejeitado: MP_WEBHOOK_SECRET ausente')
        return JsonResponse({"error": "Billing webhook unavailable"}, status=503)

    x_sig = request.META.get('HTTP_X_SIGNATURE', '')
    if not x_sig:
        logger.warning("Billing webhook sem x-signature, rejeitado")
        return JsonResponse({"error": "Forbidden"}, status=403)

    import hashlib
    import hmac

    x_request_id = request.META.get('HTTP_X_REQUEST_ID', '')
    data_id = request.GET.get('data.id', '')

    if data_id:
        data_id = data_id.lower()

    parts = {}
    for pair in x_sig.split(','):
        if '=' in pair:
            k, v = pair.split('=', 1)
            parts[k.strip()] = v.strip()

    ts = parts.get('ts', '')
    v1 = parts.get('v1', '')

    if not ts or not v1:
        logger.warning("Billing webhook x-signature mal formatada")
        return JsonResponse({"error": "Forbidden"}, status=403)

    manifest_parts = []
    if data_id:
        manifest_parts.append(f"id:{data_id};")
    if x_request_id:
        manifest_parts.append(f"request-id:{x_request_id};")
    manifest_parts.append(f"ts:{ts};")
    manifest = ''.join(manifest_parts)

    expected = hmac.new(
        webhook_secret.encode('utf-8'),
        manifest.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, v1):
        logger.warning("Billing webhook x-signature invalida (data_id=%s)", data_id)
        return JsonResponse({"error": "Forbidden"}, status=403)

    logger.info("Billing webhook x-signature OK (data_id=%s)", data_id)

    event, created = _get_or_create_webhook_event(
        gateway='mercadopago',
        payload=payload,
        gateway_event_id=str(payload.get('id', '')) or None,
    )
    if not created:
        logger.info("Billing webhook duplicado ignorado: gateway_event_id=%s", event.gateway_event_id)
        return JsonResponse({"status": "duplicate"}, status=200)

    from app.apps.webhooks.tasks import process_billing_webhook
    process_billing_webhook.delay(event.id)
    return JsonResponse({"status": "received"}, status=200)
