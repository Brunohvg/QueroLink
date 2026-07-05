from django.core.management.base import BaseCommand
from app.apps.accounts.models import User


class Command(BaseCommand):
    help = 'Remove usuarios SELLER sem perfil de vendedor (orfanos).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Aplica as exclusoes. Sem esta flag, apenas relata (dry-run).',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        mode = 'APLICANDO' if apply else 'DRY-RUN (sem gravacao)'
        self.stdout.write(f'=== fix_orphan_users — {mode} ===\n')

        orphans = []
        for user in User.objects.filter(role=User.Role.SELLER).select_related('tenant'):
            try:
                user.seller_profile
            except Exception:
                orphans.append(user)

        if not orphans:
            self.stdout.write('Nenhum usuario orfao encontrado.')
            return

        for user in orphans:
            tenant_name = user.tenant.company_name if user.tenant else '(sem tenant)'
            last_login = (
                user.last_login.strftime('%d/%m/%Y %H:%M') if user.last_login
                else 'nunca'
            )
            self.stdout.write(
                f'  ORFAO  username={user.username}  tenant={tenant_name}  '
                f'ultimo_login={last_login}'
            )

        self.stdout.write(
            f'\n=== RESUMO ===\n'
            f'  Usuarios orfaos: {len(orphans)}\n'
        )

        if apply:
            for user in orphans:
                user.delete()
            self.stdout.write(
                self.style.SUCCESS(f'{len(orphans)} usuarios removidos.')
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    'Dry-run concluido. Use --apply para excluir os usuarios.'
                )
            )
