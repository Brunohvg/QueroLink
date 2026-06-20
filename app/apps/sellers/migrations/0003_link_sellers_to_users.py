from django.db import migrations
from django.utils.crypto import get_random_string
from django.utils.text import slugify


def generate_unique_username(name, existing_usernames):
    base = slugify(name)
    username = base
    counter = 2
    while username in existing_usernames:
        username = f"{base}-{counter}"
        counter += 1
    existing_usernames.add(username)
    return username


def link_sellers_to_users(apps, schema_editor):
    Seller = apps.get_model("sellers", "Seller")
    User = apps.get_model("accounts", "User")

    existing_usernames = set(User.objects.values_list("username", flat=True))

    sellers_without_user = Seller.objects.filter(user__isnull=True).select_related("tenant")
    if not sellers_without_user.exists():
        print("Nenhum Seller sem User encontrado. Data migration pulada.")
        return

    print(f"Encontrados {sellers_without_user.count()} Sellers sem User vinculado.")
    print("CREDENCIAIS GERADAS (username / senha temporaria):")
    print("-" * 55)

    for seller in sellers_without_user:
        username = generate_unique_username(seller.name, existing_usernames)
        password = get_random_string(12)

        user = User.objects.create_user(
            username=username,
            password=password,
            role="SELLER",
            tenant=seller.tenant,
        )

        seller.user = user
        seller.save(update_fields=["user"])

        print(f"  {username:30s}  {password}")

    print("-" * 55)
    print("Guarde as senhas acima. Elas NAO serao exibidas novamente.")


def unlink_sellers_from_users(apps, schema_editor):
    Seller = apps.get_model("sellers", "Seller")
    sellers_with_user = Seller.objects.filter(user__isnull=False)
    for seller in sellers_with_user:
        seller.user = None
        seller.save(update_fields=["user"])


class Migration(migrations.Migration):

    dependencies = [
        ("sellers", "0002_add_user_and_commission_rate"),
        ("accounts", "0002_add_user_and_commission_rate"),
    ]

    operations = [
        migrations.RunPython(
            link_sellers_to_users,
            reverse_code=unlink_sellers_from_users,
        ),
    ]
