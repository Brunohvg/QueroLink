from django.core.management.base import BaseCommand, CommandError

from app.apps.accounts.backup_ops import redact_backup_output, run_backup_now


class Command(BaseCommand):
    help = 'Executa backup manual chamando /app/scripts/backup.sh.'

    def handle(self, *args, **options):
        try:
            result = run_backup_now()
        except Exception as exc:
            raise CommandError(redact_backup_output(exc)) from exc

        stdout = redact_backup_output(result.stdout).strip()
        if stdout:
            self.stdout.write(stdout)
        self.stdout.write(self.style.SUCCESS('Backup manual concluido'))
