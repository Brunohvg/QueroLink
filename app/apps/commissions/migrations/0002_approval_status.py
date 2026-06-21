from django.db import migrations, models


def set_default_approval_status(apps, schema_editor):
    SellerCommission = apps.get_model('commissions', 'SellerCommission')
    SellerCommission.objects.filter(approval_status='').update(approval_status='PENDENTE')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('commissions', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='sellercommission',
            name='approval_status',
            field=models.CharField(choices=[('PENDENTE', 'Pendente'), ('APROVADO', 'Aprovado'), ('REJEITADO', 'Rejeitado')], default='PENDENTE', max_length=20),
        ),
        migrations.RunPython(set_default_approval_status, reverse_code=noop),
    ]
