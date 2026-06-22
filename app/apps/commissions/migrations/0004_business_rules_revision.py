from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('commissions', '0003_fix_approval_status_null'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('sellers', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommissionAdjustment',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name='ID',
                )),
                ('previous_amount', models.PositiveIntegerField(
                    help_text='Valor da comissao antes do ajuste em centavos',
                )),
                ('new_amount', models.PositiveIntegerField(
                    help_text='Valor da comissao depois do ajuste em centavos',
                )),
                ('difference', models.IntegerField(
                    help_text=(
                        'Diferenca (positivo = aumento, negativo = reducao) '
                        'em centavos'
                    ),
                )),
                ('reason', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('adjusted_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='adjustments_made',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('seller_commission', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='adjustments',
                    to='commissions.sellercommission',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),

        migrations.AlterField(
            model_name='commissionperiod',
            name='status',
            field=models.CharField(
                choices=[
                    ('ABERTA', 'Aberta'),
                    ('FECHADA', 'Fechada'),
                    ('PAGA', 'Paga'),
                    ('AJUSTADA', 'Ajustada'),
                    ('CANCELADA', 'Cancelada'),
                ],
                default='ABERTA', max_length=20,
            ),
        ),

        migrations.AddField(
            model_name='commissionperiod',
            name='closed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commissionperiod',
            name='closed_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='periods_closed',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='commissionperiod',
            name='paid_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='periods_paid',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='commissionperiod',
            name='adjusted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commissionperiod',
            name='adjusted_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='periods_adjusted',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='commissionperiod',
            name='adjustment_reason',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commissionperiod',
            name='cancelled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commissionperiod',
            name='cancelled_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='periods_cancelled',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='commissionperiod',
            name='cancel_reason',
            field=models.TextField(blank=True, null=True),
        ),

        migrations.RemoveField(
            model_name='sellercommission',
            name='approval_status',
        ),
        migrations.AddField(
            model_name='sellercommission',
            name='closed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sellercommission',
            name='closed_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='commissions_closed',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='sellercommission',
            name='paid_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sellercommission',
            name='paid_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='commissions_paid',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='sellercommission',
            name='paid_amount',
            field=models.PositiveIntegerField(
                blank=True, null=True,
                help_text='Valor efetivamente pago em centavos',
            ),
        ),
        migrations.AlterField(
            model_name='sellercommission',
            name='payment_method',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AlterField(
            model_name='sellercommission',
            name='payment_notes',
            field=models.TextField(blank=True, null=True),
        ),
    ]
