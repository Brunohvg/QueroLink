from django.core.management.base import BaseCommand, CommandError

from app.apps.accounts.backup_ops import redact_backup_output, run_backup_check


class Command(BaseCommand):
    help = 'Valida rclone/Google Drive para backups sem gerar dump do banco.'

    def handle(self, *args, **options):
        try:
            steps = run_backup_check()
        except Exception as exc:
            raise CommandError(redact_backup_output(exc)) from exc

        self.stdout.write(self.style.SUCCESS('Backup check OK'))
        for step in steps:
            self.stdout.write(f'- {step}')
