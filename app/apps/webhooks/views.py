import json
import hmac
import hashlib
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from app.apps.webhooks.models import WebhookEvent
from app.apps.accounts.fields import scrub_payment_payload

logger = logging.getLogger(__name__)


def _verify_webhook_token(tenant_uuid, token):
    expected = hmac.new(
        settings.WHATSAPP_API_KEY.encode(),
        str(tenant_uuid).encode(),
        hashlib.sha256,
    ).hexdigest()[:16]
    return hmac.compare_digest(expected, token)


@csrf_exempt
def pagarme_webhook(request):
    if request.method == "POST":
        try:
            payload = json.loads(request.body)
            sanitized = scrub_payment_payload(payload)

            event = WebhookEvent.objects.create(
                gateway='pagarme',
                payload=sanitized,
            )
            from app.apps.webhooks.tasks import process_pagarme_webhook
            process_pagarme_webhook.delay(event.id)
            return JsonResponse({"status": "received"}, status=200)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
def evolution_webhook(request, instance_name, tenant_uuid, token):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    if not _verify_webhook_token(tenant_uuid, token):
        logger.warning("Evolution webhook invalid token: tenant=%s", tenant_uuid)
        return JsonResponse({"error": "Forbidden"}, status=403)


@csrf_exempt
def evolution_webhook_legacy(request, instance_name, tenant_uuid):
    logger.info("Legacy webhook (no token): instance=%s tenant=%s", instance_name, tenant_uuid)
    return JsonResponse({"status": "ignored"}, status=200)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    logger.info(
        "Evolution webhook: instance=%s tenant=%s event=%s",
        instance_name, tenant_uuid, payload.get('event'),
    )

    from app.apps.accounts.models import Tenant
    tenant = Tenant.objects.filter(uuid=tenant_uuid).first()
    if not tenant:
        logger.warning("Tenant %s not found for webhook", tenant_uuid)
        return JsonResponse({"status": "ignored"}, status=200)

    event_type = payload.get('event', '')
    data = payload.get('data', {})

    extra = {}
    if event_type == 'CONNECTION_UPDATE':
        state = data.get('state') or data.get('instance', {}).get('state', '')
        extra['state'] = state
        if state == 'open':
            logger.info("WhatsApp connected for tenant %s", tenant_uuid)

    elif event_type == 'QRCODE_UPDATE':
        logger.info("QR code updated for tenant %s", tenant_uuid)

    WebhookEvent.objects.create(
        gateway='evolution',
        payload={
            'event': event_type,
            'instance': instance_name,
            'tenant_uuid': str(tenant_uuid),
            **extra,
        },
    )

    return JsonResponse({"status": "received"}, status=200)
