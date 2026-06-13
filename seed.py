import os
import django

# Configura o ambiente do Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.config.settings.production')
django.setup()

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller

def run_seed():
    print("Iniciando seed...")
    
    # Cria o tenant padrão se não existir
    tenant, created = Tenant.objects.get_or_create(
        company_name="Bibelô Oficial",
        defaults={
            "pagarme_api_key": "sua_chave_pagar_me_aqui",
            "whatsapp_instance_id": "sua_instancia_aqui",
            "whatsapp_token": "seu_token_aqui"
        }
    )
    if created:
        print(f"Tenant criado: {tenant.company_name}")
    else:
        print(f"Tenant já existia: {tenant.company_name}")

    # Cria o superusuário padrão se não existir
    admin_email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@bibelo.com.br")
    admin_password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin123")
    
    if not User.objects.filter(email=admin_email).exists() and not User.objects.filter(username=admin_email).exists():
        User.objects.create_superuser(
            username=admin_email,
            email=admin_email,
            password=admin_password
        )
        print(f"Superusuário criado: {admin_email} / Senha: {admin_password}")
    else:
        print(f"Superusuário já existia: {admin_email}")

    # Lista dos antigos vendedores em HTML
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

    for dados in antigos_vendedores:
        seller, s_created = Seller.objects.get_or_create(
            tenant=tenant,
            name=dados['name'],
            defaults={'phone': dados['phone']}
        )
        if s_created:
            print(f"Vendedor criado: {seller.name}")

    print("Seed finalizado com sucesso!")

if __name__ == '__main__':
    run_seed()
