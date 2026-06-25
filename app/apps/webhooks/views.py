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
    secret = getattr(settings, 'WHATSAPP_API_KEY', None) or settings.SECRET_KEY
    expected = hmac.new(
        secret.encode(),
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
            event = WebhookEvent.objects.create(gateway='pagarme', payload=sanitized)
            from app.apps.webhooks.tasks import process_pagarme_webhook
            process_pagarme_webhook.delay(event.id)
            return JsonResponse({"status": "received"}, status=200)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
    return JsonResponse({"error": "Method not allowed"}, status=405)


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

        WebhookEvent.objects.create(
            gateway='evolution',
            payload={
                'event': event_type,
                'instance': instance_name,
                'tenant_uuid': str(tenant_uuid),
                **extra,
            },
        )
    except json.JSONDecodeError:
        logger.warning("Evolution webhook invalid JSON from %s", instance_name)
    except Exception as e:
        logger.error("Evolution webhook error: %s", e)

    return JsonResponse({"status": "received"}, status=200)


@csrf_exempt
def evolution_webhook_legacy(request, instance_name, tenant_uuid):
    logger.info("Legacy webhook (no token): instance=%s tenant=%s", instance_name, tenant_uuid)
    return JsonResponse({"status": "ignored"}, status=200)
