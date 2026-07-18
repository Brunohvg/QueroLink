import app.apps.receivables.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

ADD_COLUMNS_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='receivables_boleto' AND column_name='invoice_pdf') THEN
        ALTER TABLE receivables_boleto ADD COLUMN invoice_pdf varchar(255) NOT NULL DEFAULT '';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='receivables_boleto' AND column_name='invoice_pdf_uploaded_at') THEN
        ALTER TABLE receivables_boleto ADD COLUMN invoice_pdf_uploaded_at timestamp with time zone NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='receivables_boleto' AND column_name='invoice_pdf_uploaded_by_id') THEN
        ALTER TABLE receivables_boleto ADD COLUMN invoice_pdf_uploaded_by_id uuid NULL
            REFERENCES accounts_user(uuid) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='receivables_boleto' AND column_name='invoice_xml') THEN
        ALTER TABLE receivables_boleto ADD COLUMN invoice_xml varchar(255) NOT NULL DEFAULT '';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='receivables_boleto' AND column_name='invoice_xml_uploaded_at') THEN
        ALTER TABLE receivables_boleto ADD COLUMN invoice_xml_uploaded_at timestamp with time zone NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='receivables_boleto' AND column_name='invoice_xml_uploaded_by_id') THEN
        ALTER TABLE receivables_boleto ADD COLUMN invoice_xml_uploaded_by_id uuid NULL
            REFERENCES accounts_user(uuid) ON DELETE SET NULL;
    END IF;
END $$;
"""

DROP_COLUMNS_SQL = """
ALTER TABLE receivables_boleto DROP COLUMN IF EXISTS invoice_pdf;
ALTER TABLE receivables_boleto DROP COLUMN IF EXISTS invoice_pdf_uploaded_at;
ALTER TABLE receivables_boleto DROP COLUMN IF EXISTS invoice_pdf_uploaded_by_id;
ALTER TABLE receivables_boleto DROP COLUMN IF EXISTS invoice_xml;
ALTER TABLE receivables_boleto DROP COLUMN IF EXISTS invoice_xml_uploaded_at;
ALTER TABLE receivables_boleto DROP COLUMN IF EXISTS invoice_xml_uploaded_by_id;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('receivables', '0004_commission_impact_review'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='boleto',
                    name='invoice_pdf',
                    field=models.FileField(blank=True, max_length=255, upload_to=app.apps.receivables.models.invoice_pdf_upload_to),
                ),
                migrations.AddField(
                    model_name='boleto',
                    name='invoice_pdf_uploaded_at',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='boleto',
                    name='invoice_pdf_uploaded_by',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoice_pdfs_uploaded', to=settings.AUTH_USER_MODEL),
                ),
                migrations.AddField(
                    model_name='boleto',
                    name='invoice_xml',
                    field=models.FileField(blank=True, max_length=255, upload_to=app.apps.receivables.models.invoice_xml_upload_to),
                ),
                migrations.AddField(
                    model_name='boleto',
                    name='invoice_xml_uploaded_at',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='boleto',
                    name='invoice_xml_uploaded_by',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoice_xmls_uploaded', to=settings.AUTH_USER_MODEL),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=ADD_COLUMNS_SQL,
                    reverse_sql=DROP_COLUMNS_SQL,
                ),
            ],
        ),
    ]
