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


def _verify_pagarme_signature(body: bytes, api_key: str, received_sig: str) -> bool:
    if not received_sig:
        return False
    key_bytes = api_key.encode()
    body_hmac = hmac.new(key_bytes, body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(body_hmac, received_sig):
        return True
    body_hmac_sha1 = hmac.new(key_bytes, body, hashlib.sha1).hexdigest()
    if hmac.compare_digest(body_hmac_sha1, received_sig):
        return True
    key_with_colon = f"{api_key}:"
    body_hmac_colon = hmac.new(key_with_colon.encode(), body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(body_hmac_colon, received_sig):
        return True
    body_hash = hashlib.sha256(key_bytes + body).hexdigest()
    if hmac.compare_digest(body_hash, received_sig):
        return True
    return False


@csrf_exempt
def pagarme_webhook(request, tenant_slug=None):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    raw_body = request.body
    received_sig = (
        request.headers.get('x-pagarme-signature', '')
        or request.headers.get('X-Hub-Signature-256', '')
        or request.headers.get('X-Hub-Signature', '')
        or ''
    )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    from django.conf import settings
    from app.apps.accounts.models import Tenant
    from app.services.gateway.pagar_me import _normalize_api_key

    raw_key = getattr(settings, 'API_KEY_PAGAR_ME', '')

    if tenant_slug:
        try:
            tenant = Tenant.objects.get(slug=tenant_slug, is_active=True)
            tenant_key = tenant.pagarme_api_key
            if tenant_key:
                raw_key = tenant_key
        except Tenant.DoesNotExist:
            logger.warning("Pagarme webhook: tenant slug=%s not found", tenant_slug)

    normalized_key = _normalize_api_key(raw_key)

    sig = received_sig
    if sig.startswith('sha256='):
        sig = sig[7:]

    if sig:
        verified = _verify_pagarme_signature(raw_body, normalized_key, sig)
        if not verified and raw_key != normalized_key:
            verified = _verify_pagarme_signature(raw_body, raw_key, sig)

        if not verified:
            logger.warning(
                "Pagarme webhook signature verification failed: "
                "sig=%s key_prefix=%s body_len=%d tenant=%s",
                sig[:16],
                normalized_key[:4] if normalized_key else '(empty)',
                len(raw_body),
                tenant_slug or 'none',
            )
            return JsonResponse({"error": "Forbidden"}, status=403)
    else:
        logger.info(
            "Pagarme webhook without signature header (payment-link event) — "
            "skipping verification, body_len=%d tenant=%s",
            len(raw_body),
            tenant_slug or 'none',
        )

    sanitized = scrub_payment_payload(payload)
    event = WebhookEvent.objects.create(gateway='pagarme', payload=sanitized)
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
    except json.JSONDecodeError:
        logger.warning("Evolution webhook invalid JSON from %s", instance_name)
    except Exception as e:
        logger.error("Evolution webhook error: %s", e)

    return JsonResponse({"status": "received"}, status=200)


@csrf_exempt
def evolution_webhook_legacy(request, instance_name, tenant_uuid):
    logger.info("Legacy webhook (no token): instance=%s tenant=%s", instance_name, tenant_uuid)
    return JsonResponse({"status": "ignored"}, status=200)
