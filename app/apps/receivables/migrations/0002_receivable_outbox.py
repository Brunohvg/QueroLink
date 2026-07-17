import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0024_tenant_receivables_enabled'),
        ('receivables', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='boleto',
            name='refund_reason',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='boleto',
            name='refunded_amount_cents',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='boleto',
            name='refunded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='IntegrationOutbox',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('aggregate_type', models.CharField(max_length=50)),
                ('aggregate_uuid', models.UUIDField()),
                ('event_type', models.CharField(max_length=100)),
                ('event_key', models.CharField(max_length=150)),
                ('payload', models.JSONField(default=dict)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('PROCESSING', 'Processing'), ('PROCESSED', 'Processed'), ('FAILED', 'Failed')], default='PENDING', max_length=10)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('available_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('processing_started_at', models.DateTimeField(blank=True, null=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='receivables_outbox_events', to='accounts.tenant')),
            ],
            options={
                'ordering': ['available_at', 'created_at'],
                'indexes': [models.Index(fields=['status', 'available_at'], name='receivables_status_9f694c_idx')],
                'constraints': [models.UniqueConstraint(fields=('tenant', 'event_key'), name='uniq_outbox_tenant_event_key')],
            },
        ),
    ]
