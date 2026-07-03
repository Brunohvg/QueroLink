import json
import hmac
import hashlib
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from app.apps.webhooks.models import WebhookEvent
from app.apps.accounts.fields import scrub_payment_payload

logger = logging.getLogger(__name__)


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
    if gateway_event_id and gateway_event_id.startswith('evt_'):
        if WebhookEvent.objects.filter(gateway_event_id=gateway_event_id).exists():
            logger.info("Webhook duplicado ignorado: gateway_event_id=%s", gateway_event_id)
            return JsonResponse({"status": "duplicate"}, status=200)

    event = WebhookEvent.objects.create(
        gateway='pagarme',
        payload=sanitized,
        gateway_event_id=gateway_event_id or None,
        tenant=tenant,
    )
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
    except json.JSONDecodeError:
        logger.warning("Evolution webhook invalid JSON from %s", instance_name)
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error("Evolution webhook error: %s", e)
        return JsonResponse({"error": "Internal error"}, status=500)


@csrf_exempt
def evolution_webhook_legacy(request, instance_name, tenant_uuid):
    logger.info("Legacy webhook (no token): instance=%s tenant=%s", instance_name, tenant_uuid)
    return JsonResponse({"status": "ignored"}, status=200)
