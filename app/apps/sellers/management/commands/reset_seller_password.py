from django.core.management.base import BaseCommand, CommandError
from django.utils.crypto import get_random_string
from app.apps.sellers.models import Seller


class Command(BaseCommand):
    help = "Gera uma nova senha temporaria para um vendedor e imprime no terminal."

    def add_arguments(self, parser):
        parser.add_argument("seller_uuid", type=str, help="UUID do vendedor")

    def handle(self, *args, **options):
        seller_uuid = options["seller_uuid"]

        try:
            seller = Seller.objects.select_related("user").get(uuid=seller_uuid)
        except Seller.DoesNotExist:
            raise CommandError(f"Vendedor com UUID '{seller_uuid}' nao encontrado.")

        if not seller.user:
            raise CommandError(
                f"Vendedor '{seller.name}' nao possui User vinculado. "
                f"Execute a data migration primeiro."
            )

        password = get_random_string(12)
        seller.user.set_password(password)
        seller.user.save(update_fields=["password"])

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSenha temporaria para '{seller.name}':\n"
                f"  Username: {seller.user.username}\n"
                f"  Senha:    {password}\n"
                f"\nGuarde esta senha. Ela NAO sera exibida novamente.\n"
            )
        )
