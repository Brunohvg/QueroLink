from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.apps.audit.models import AuditLog
from app.apps.orders.models import Order
from app.apps.webhooks.models import WebhookEvent
from app.apps.webhooks.services import PAID_EVENT_TYPES, process_paid_pagarme_event
from app.apps.webhooks.tasks import _notify_link_status_after_commit


class Command(BaseCommand):
    help = 'Reprocessa um WebhookEvent Pagar.me pelo servico oficial idempotente.'

    def add_arguments(self, parser):
        parser.add_argument('--event-id', required=True, type=int)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--operator', default='system')

    def handle(self, *args, **options):
        event_id = options['event_id']
        dry_run = options['dry_run']
        operator = options['operator']

        try:
            event = WebhookEvent.objects.get(id=event_id)
        except WebhookEvent.DoesNotExist as exc:
            raise CommandError('WebhookEvent nao encontrado.') from exc

        if event.gateway != 'pagarme':
            raise CommandError('Somente eventos Pagar.me podem ser reprocessados por este comando.')

        payload = event.payload if isinstance(event.payload, dict) else {}
        event_type = payload.get('type', '')
        if event_type not in PAID_EVENT_TYPES:
            raise CommandError(
                'Este comando reprocessa apenas eventos Pagar.me de pagamento confirmado.',
            )

        result = process_paid_pagarme_event(event.id, dry_run=dry_run)

        if not dry_run:
            if result.notify_event_type and result.order_uuid:
                order = Order.objects.select_related('seller').get(uuid=result.order_uuid)
                if order.seller:
                    _notify_link_status_after_commit(order, result.notify_event_type)

            with transaction.atomic():
                refreshed = WebhookEvent.objects.select_related('tenant').get(id=event.id)
                AuditLog.objects.create(
                    tenant=refreshed.tenant,
                    action='pagarme.webhook_reprocessed',
                    model_name='WebhookEvent',
                    object_id=str(refreshed.id),
                    changes={
                        'operator': operator,
                        'result': result.status,
                        'event_type': result.event_type,
                        'order_uuid': result.order_uuid,
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'{result.status}: event={event.id} type={result.event_type} '
                f'order={result.order_uuid or "-"} dry_run={dry_run}'
            )
        )
