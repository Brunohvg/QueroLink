import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from app.apps.accounts.models import User


class Command(BaseCommand):
    help = 'Cria o superuser inicial a partir de variaveis de ambiente.'

    def handle(self, *args, **options):
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip()
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '')

        if not email:
            raise CommandError('DJANGO_SUPERUSER_EMAIL e obrigatorio.')
        if not password:
            raise CommandError('DJANGO_SUPERUSER_PASSWORD e obrigatorio.')
        if len(password) < 12:
            raise CommandError('DJANGO_SUPERUSER_PASSWORD deve ter pelo menos 12 caracteres.')

        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write('Superuser ja existe. Nada a fazer.')
            return

        candidate = User(username=email, email=email, is_superuser=True, is_staff=True)
        try:
            validate_password(password, user=candidate)
        except ValidationError as e:
            raise CommandError('DJANGO_SUPERUSER_PASSWORD nao atende a politica de senha.') from e

        User.objects.create_superuser(username=email, email=email, password=password)
        self.stdout.write('Superuser criado com sucesso.')
