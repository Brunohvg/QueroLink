import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('receivables', '0002_receivable_outbox'),
        ('sales', '0007_alter_sale_origin_saleimportbatch'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReceivableAllocation',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('amount_cents', models.PositiveIntegerField()),
                ('sale_date', models.DateField()),
                ('status', models.CharField(choices=[('ACTIVE', 'Active'), ('REVERSED', 'Reversed')], default='ACTIVE', max_length=10)),
                ('allocated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('reversed_at', models.DateTimeField(blank=True, null=True)),
                ('reversal_reason', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('allocated_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='receivable_allocations_created', to=settings.AUTH_USER_MODEL)),
                ('boleto', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='allocations', to='receivables.boleto')),
                ('sale', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='receivable_allocations', to='sales.sale')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='receivable_allocations', to='accounts.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'status'], name='receivables_tenant__0150ad_idx'), models.Index(fields=['tenant', 'sale'], name='receivables_tenant__74f319_idx')],
                'constraints': [models.UniqueConstraint(fields=('tenant', 'boleto'), name='uniq_receivable_allocation_boleto')],
            },
        ),
    ]
