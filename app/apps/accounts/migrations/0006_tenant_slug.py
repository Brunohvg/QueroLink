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


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_plan_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='slug',
            field=models.SlugField(blank=True, max_length=100),
        ),
        migrations.RunPython(populate_slugs, reverse_code=noop),
        migrations.AlterField(
            model_name='tenant',
            name='slug',
            field=models.SlugField(blank=True, max_length=100, unique=True),
        ),
    ]
