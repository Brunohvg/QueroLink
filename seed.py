import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.config.settings.production')
django.setup()

from django.utils.crypto import get_random_string
from django.utils.text import slugify

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller


def run_seed():
    print("Iniciando seed...")

    tenant, created = Tenant.objects.get_or_create(
        company_name="Bibelô Oficial",
        defaults={
            "pagarme_api_key": os.environ.get("API_KEY_PAGAR_ME", ""),
            "whatsapp_instance_id": os.environ.get("INSTANCE", ""),
            "whatsapp_token": os.environ.get("API_KEY_INSTANCIA", ""),
        }
    )
    if created:
        print(f"Tenant criado: {tenant.company_name}")
    else:
        print(f"Tenant ja existia: {tenant.company_name}")

    admin_email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@bibelo.com.br")
    admin_password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin123")

    if not User.objects.filter(email=admin_email).exists() and not User.objects.filter(username=admin_email).exists():
        admin = User.objects.create_superuser(
            username=admin_email,
            email=admin_email,
            password=admin_password
        )
        print(f"Superusuario criado: {admin_email}")
    else:
        admin = User.objects.filter(username=admin_email).first()
        print(f"Superusuario ja existia: {admin_email}")

    antigos_vendedores = [
        {"name": "Bibelô", "phone": "(31) 99243-0500"},
        {"name": "Célia", "phone": "(31) 99166-2461"},
        {"name": "Danúbia", "phone": "(31) 98982-3859"},
        {"name": "Eliete", "phone": "(31) 99981-8017"},
        {"name": "Flaviane", "phone": "(31) 97232-0859"},
        {"name": "Iolanda", "phone": "(31) 99748-1638"},
        {"name": "Léo", "phone": "(31) 98457-0657"},
        {"name": "Leonardo Bruno", "phone": "(31) 98014-5966"},
        {"name": "Luciana", "phone": "(31) 97364-3559"},
        {"name": "Luana", "phone": "(31) 97575-8998"},
        {"name": "Marcila", "phone": "(31) 99748-6119"},
        {"name": "Maria", "phone": "(31) 97131-7368"},
        {"name": "Nelma", "phone": "(31) 99480-7724"},
        {"name": "Regiane", "phone": "(31) 98471-9920"},
        {"name": "Tamara", "phone": "(31) 97349-6057"},
        {"name": "Bruno Vidal", "phone": "(31) 97312-1650"},
    ]

    existing_usernames = set(User.objects.values_list("username", flat=True))

    for dados in antigos_vendedores:
        if Seller.objects.filter(tenant=tenant, name=dados['name']).exists():
            print(f"Vendedor ja existe: {dados['name']}")
            continue

        base = slugify(dados['name'])
        username = base
        n = 2
        while username in existing_usernames:
            username = f"{base}-{n}"
            n += 1
        existing_usernames.add(username)

        password = get_random_string(12)

        user = User.objects.create_user(
            username=username,
            password=password,
            role=User.Role.SELLER,
            tenant=tenant,
        )

        Seller.objects.create(
            tenant=tenant,
            user=user,
            name=dados['name'],
            phone=dados['phone'],
            commission_rate=tenant.default_commission_rate,
        )

        print(f"Vendedor criado: {dados['name']}")

    print("Seed finalizado com sucesso!")


if __name__ == '__main__':
    run_seed()
