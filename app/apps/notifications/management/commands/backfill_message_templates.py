from django.core.management.base import BaseCommand, CommandError

from app.apps.accounts.models import Tenant
from app.apps.notifications.services import ensure_default_message_templates


class Command(BaseCommand):
    help = 'Cria templates padrao ausentes sem sobrescrever templates customizados.'

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--dry-run', action='store_true')
        mode.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        apply = options['apply']
        if not dry_run and not apply:
            raise CommandError('Use --dry-run ou --apply.')

        totals = {'created': 0, 'existing': 0, 'missing': 0}
        tenants = Tenant.objects.order_by('uuid')
        tenant_count = tenants.count()
        for tenant in tenants:
            summary = ensure_default_message_templates(tenant, dry_run=dry_run)
            for key, value in summary.items():
                totals[key] += value

        if dry_run:
            self.stdout.write(
                'Dry run concluido. '
                f"Tenants: {tenant_count}. "
                f"Existentes: {totals['existing']}. "
                f"Ausentes: {totals['missing']}."
            )
            return

        self.stdout.write(
            'Backfill concluido. '
            f"Tenants: {tenant_count}. "
            f"Criados: {totals['created']}. "
            f"Existentes: {totals['existing']}."
        )
