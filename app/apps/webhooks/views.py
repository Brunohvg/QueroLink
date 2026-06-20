import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from app.apps.webhooks.models import WebhookEvent

@csrf_exempt
def pagarme_webhook(request):
    if request.method == "POST":
        try:
            payload = json.loads(request.body)
            # Guarda o evento bruto no banco
            event = WebhookEvent.objects.create(
                gateway='pagarme',
                payload=payload
            )
            from app.apps.webhooks.tasks import process_pagarme_webhook
            process_pagarme_webhook.delay(event.id)
            return JsonResponse({"status": "received"}, status=200)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
    return JsonResponse({"error": "Method not allowed"}, status=405)
