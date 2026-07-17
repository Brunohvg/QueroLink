import uuid

from django.core.management.base import BaseCommand, CommandError

from app.apps.accounts.models import Tenant
from app.apps.customers.models import CustomerActivity
from app.apps.customers.services import project_boleto, resolve_customer
from app.apps.orders.models import Order
from app.apps.receivables.models import Boleto


class Command(BaseCommand):
    help = 'Projeta Orders/PaymentLinks e Boletos existentes no Customer Ledger.'

    def add_arguments(self, parser):
        scope = parser.add_mutually_exclusive_group(required=True)
        scope.add_argument('--tenant', help='UUID do tenant a processar.')
        scope.add_argument('--all-tenants', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--batch-size', type=int, default=200)
        parser.add_argument(
            '--cursor',
            help='Cursor fonte:UUID exibido pelo comando (orders:... ou boletos:...).',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        if batch_size < 1 or batch_size > 1000:
            raise CommandError('--batch-size deve estar entre 1 e 1000.')
        cursor = self._parse_cursor(options.get('cursor'))

        if options['all_tenants']:
            tenants = Tenant.objects.order_by('uuid')
        else:
            try:
                tenants = Tenant.objects.filter(pk=uuid.UUID(options['tenant']))
            except (TypeError, ValueError) as exc:
                raise CommandError('--tenant deve ser um UUID valido.') from exc
            if not tenants.exists():
                raise CommandError('Tenant nao encontrado.')

        totals = {'tenants': 0, 'processed': 0, 'created': 0, 'errors': 0}
        for tenant in tenants.iterator():
            totals['tenants'] += 1
            try:
                counts = self._backfill_tenant(
                    tenant,
                    dry_run=options['dry_run'],
                    batch_size=batch_size,
                    cursor=cursor,
                )
                totals['processed'] += counts['processed']
                totals['created'] += counts['created']
            except Exception as exc:
                totals['errors'] += 1
                self.stderr.write(
                    f'Falha no tenant {tenant.uuid}: {exc.__class__.__name__}'
                )

        self.stdout.write(
            'Customer Ledger: '
            f"tenants={totals['tenants']} processed={totals['processed']} "
            f"created={totals['created']} errors={totals['errors']} "
            f"dry_run={str(options['dry_run']).lower()}"
        )

    @staticmethod
    def _parse_cursor(value):
        if not value:
            return None
        try:
            source, raw_uuid = value.split(':', 1)
            cursor_uuid = uuid.UUID(raw_uuid)
        except (AttributeError, TypeError, ValueError) as exc:
            raise CommandError(
                '--cursor deve usar o formato orders:UUID ou boletos:UUID.'
            ) from exc
        if source not in ('orders', 'boletos'):
            raise CommandError(
                '--cursor deve usar o formato orders:UUID ou boletos:UUID.'
            )
        return source, cursor_uuid

    def _backfill_tenant(self, tenant, *, dry_run, batch_size, cursor):
        counts = {'processed': 0, 'created': 0}
        sources = (
            ('orders', Order.objects.filter(tenant=tenant).select_related('seller')),
            ('boletos', Boleto.objects.filter(tenant=tenant).select_related('seller')),
        )
        cursor_source_index = (
            next(
                index for index, (source_name, _) in enumerate(sources)
                if source_name == cursor[0]
            ) if cursor else None
        )
        for source_index, (source_name, queryset) in enumerate(sources):
            if cursor and source_index < cursor_source_index:
                continue
            if cursor and source_name == cursor[0]:
                queryset = queryset.filter(uuid__gt=cursor[1])
            queryset = queryset.order_by('uuid')
            last_uuid = None
            while True:
                batch = list(queryset.filter(
                    **({'uuid__gt': last_uuid} if last_uuid else {})
                )[:batch_size])
                if not batch:
                    break
                for item in batch:
                    counts['processed'] += 1
                    existed = CustomerActivity.objects.filter(
                        tenant=tenant,
                        source=(
                            CustomerActivity.Source.BOLETO
                            if source_name == 'boletos' else 'PAYMENT_LINK'
                        ),
                        source_uuid=item.uuid,
                    ).exists()
                    if not dry_run:
                        if source_name == 'boletos':
                            project_boleto(item)
                        else:
                            self._project_order(item)
                    if not existed:
                        counts['created'] += 1
                    last_uuid = item.uuid
                self.stdout.write(
                    f'tenant={tenant.uuid} cursor={source_name}:{last_uuid}'
                )
        return counts

    @staticmethod
    def _project_order(order):
        customer = resolve_customer(
            order.tenant,
            name=order.customer_name,
            phone=order.customer_phone or '',
            source='PAYMENT_LINK',
            source_uuid=order.uuid,
        )
        CustomerActivity.objects.update_or_create(
            tenant=order.tenant,
            source='PAYMENT_LINK',
            source_uuid=order.uuid,
            defaults={
                'customer': customer,
                'seller_name': order.seller.name if order.seller else '',
                'amount_cents': order.total_amount,
                'status': order.status,
                'occurred_at': order.updated_at,
            },
        )
