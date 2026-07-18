import app.apps.receivables.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def _add_if_missing(apps, schema_editor):
    """Idempotent: adiciona colunas da 0005 somente se ausentes.
    Django resolve tipo da FK e PK do User automaticamente."""
    Boleto = apps.get_model('receivables', 'Boleto')
    User = apps.get_model('accounts', 'User')

    table = Boleto._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            [table],
        )
        existing_cols = {row[0] for row in cursor.fetchall()}

    def add_field_if(name, field, db_column):
        if db_column not in existing_cols:
            field.set_attributes_from_name(name)
            schema_editor.add_field(Boleto, field)

    add_field_if(
        'invoice_pdf',
        models.FileField(
            max_length=255, blank=True,
            upload_to=app.apps.receivables.models.invoice_pdf_upload_to,
        ),
        'invoice_pdf',
    )
    add_field_if(
        'invoice_pdf_uploaded_at',
        models.DateTimeField(blank=True, null=True),
        'invoice_pdf_uploaded_at',
    )
    add_field_if(
        'invoice_pdf_uploaded_by',
        models.ForeignKey(
            User, blank=True, null=True,
            on_delete=django.db.models.deletion.SET_NULL,
            related_name='invoice_pdfs_uploaded',
        ),
        'invoice_pdf_uploaded_by_id',
    )
    add_field_if(
        'invoice_xml',
        models.FileField(
            max_length=255, blank=True,
            upload_to=app.apps.receivables.models.invoice_xml_upload_to,
        ),
        'invoice_xml',
    )
    add_field_if(
        'invoice_xml_uploaded_at',
        models.DateTimeField(blank=True, null=True),
        'invoice_xml_uploaded_at',
    )
    add_field_if(
        'invoice_xml_uploaded_by',
        models.ForeignKey(
            User, blank=True, null=True,
            on_delete=django.db.models.deletion.SET_NULL,
            related_name='invoice_xmls_uploaded',
        ),
        'invoice_xml_uploaded_by_id',
    )


def _noop(apps, schema_editor):
    pass


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
                migrations.RunPython(
                    code=_add_if_missing,
                    reverse_code=_noop,
                ),
            ],
        ),
    ]
