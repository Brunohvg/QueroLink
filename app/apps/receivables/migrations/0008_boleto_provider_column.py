from django.db import migrations


SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='receivables_boleto' AND column_name='provider'
    ) THEN
        ALTER TABLE receivables_boleto
        ADD COLUMN provider varchar(20) NOT NULL DEFAULT 'PAGARME';
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('receivables', '0007_receivable_notification_delivery'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
