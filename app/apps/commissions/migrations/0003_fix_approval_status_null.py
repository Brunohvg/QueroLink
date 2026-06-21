from django.db import migrations


def fix_null_approval_status(apps, schema_editor):
    SellerCommission = apps.get_model('commissions', 'SellerCommission')
    from django.db.models import Q
    SellerCommission.objects.filter(
        Q(approval_status='') | Q(approval_status__isnull=True)
    ).update(approval_status='PENDENTE')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('commissions', '0002_approval_status'),
    ]

    operations = [
        migrations.RunPython(fix_null_approval_status, reverse_code=noop),
    ]
