from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name='sale',
            name='updated_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='sales_editadas',
                to='accounts.user',
            ),
        ),
        migrations.AddIndex(
            model_name='sale',
            index=models.Index(
                fields=['seller', 'sale_date'],
                name='sales_sale_selle_3c02cb_idx',
            ),
        ),
        migrations.AddConstraint(
            model_name='sale',
            constraint=models.UniqueConstraint(
                fields=['seller', 'sale_date'],
                condition=Q(origin='MANUAL'),
                name='unique_manual_entry_per_day',
            ),
        ),
    ]
