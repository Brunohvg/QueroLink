from django.db import migrations, models
from django.utils.text import slugify


def populate_slugs(apps, schema_editor):
    Tenant = apps.get_model('accounts', 'Tenant')
    for tenant in Tenant.objects.filter(slug=''):
        base_slug = slugify(tenant.company_name)
        slug = base_slug
        counter = 1
        while Tenant.objects.filter(slug=slug).exists():
            counter += 1
            slug = f"{base_slug}-{counter}"
        tenant.slug = slug
        tenant.save(update_fields=['slug'])


def noop(apps, schema_editor):
    pass


def ensure_slug_column(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute(
                "ALTER TABLE accounts_tenant ADD COLUMN IF NOT EXISTS slug varchar(100) NOT NULL DEFAULT '';"
            )
        elif vendor == 'sqlite':
            cursor.execute("PRAGMA table_info(accounts_tenant);")
            columns = [row[1] for row in cursor.fetchall()]
            if 'slug' not in columns:
                cursor.execute(
                    "ALTER TABLE accounts_tenant ADD COLUMN slug varchar(100) NOT NULL DEFAULT '';"
                )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_plan_fields'),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                'DROP INDEX IF EXISTS accounts_tenant_slug_b48b18a8_like;'
                'DROP INDEX IF EXISTS accounts_tenant_slug_key;'
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(ensure_slug_column, reverse_code=noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='tenant',
                    name='slug',
                    field=models.SlugField(blank=True, max_length=100),
                ),
            ],
        ),
        migrations.RunPython(populate_slugs, reverse_code=noop),
        migrations.AlterField(
            model_name='tenant',
            name='slug',
            field=models.SlugField(blank=True, max_length=100, unique=True),
        ),
    ]
