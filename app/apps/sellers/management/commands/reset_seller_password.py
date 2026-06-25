from django.core.management.base import BaseCommand, CommandError
from django.utils.crypto import get_random_string
from app.apps.sellers.models import Seller
from app.apps.accounts.models import Tenant


class Command(BaseCommand):
    help = "Gera uma nova senha temporaria para um vendedor e notifica via WhatsApp."

    def add_arguments(self, parser):
        parser.add_argument("seller_uuid", type=str, help="UUID do vendedor")
        parser.add_argument(
            "--tenant", type=str, required=True,
            help="Slug do tenant (obrigatorio para isolamento multi-tenant)",
        )

    def handle(self, *args, **options):
        seller_uuid = options["seller_uuid"]
        tenant_slug = options["tenant"]

        try:
            tenant = Tenant.objects.get(slug=tenant_slug)
        except Tenant.DoesNotExist:
            raise CommandError(f"Tenant com slug '{tenant_slug}' nao encontrado.")

        try:
            seller = Seller.objects.select_related("user").get(
                uuid=seller_uuid, user__tenant=tenant,
            )
        except Seller.DoesNotExist:
            raise CommandError(
                f"Vendedor com UUID '{seller_uuid}' nao encontrado "
                f"no tenant '{tenant_slug}'."
            )

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
                f"\nSenha temporaria para '{seller.name}' gerada com sucesso.\n"
                f"  Username: {seller.user.username}\n"
            )
        )

        try:
            from app.apps.notifications.tasks import notify_seller_credentials
            notify_seller_credentials(seller, password)
            self.stdout.write("  Notificacao WhatsApp enviada.\n")
        except Exception as e:
            self.stderr.write(
                self.style.WARNING(
                    f"  Aviso: Nao foi possivel enviar notificacao WhatsApp: {e}\n"
                )
            )
