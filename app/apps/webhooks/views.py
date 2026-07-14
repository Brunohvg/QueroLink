import json
import hmac
import hashlib
import logging
import time
from datetime import timedelta

from django.db import IntegrityError, OperationalError, transaction
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

    for attempt in range(3):
        try:
            with transaction.atomic():
                return WebhookEvent.objects.get_or_create(
                    gateway=gateway,
                    gateway_event_id=gateway_event_id,
                    defaults={
                        'payload': payload,
                        'tenant': tenant,
                    },
                )
        except (IntegrityError, OperationalError):
            if attempt == 2:
                raise
            time.sleep(0.05)

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

    sanitized = scrub_payment_payload(payload)
    gateway_event_id = payload.get('id') or ''
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
            return JsonResponse({"status": "duplicate"}, status=200)
        if action == 'processing':
            logger.info("Webhook ja em processamento: gateway_event_id=%s", gateway_event_id)
            return JsonResponse({"status": "processing"}, status=200)

        if action == 'recover':
            event.status = WebhookEvent.Status.RECEIVED
            event.processing_error = ''
            event.processing_started_at = None
            event.save(update_fields=[
                'status', 'processing_error', 'processing_started_at',
            ])
        from app.apps.webhooks.tasks import process_pagarme_webhook
        process_pagarme_webhook.delay(event.id)
        return JsonResponse({"status": "received"}, status=200)

    from app.apps.webhooks.tasks import process_pagarme_webhook
    process_pagarme_webhook.delay(event.id)
    return JsonResponse({"status": "received"}, status=200)


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
