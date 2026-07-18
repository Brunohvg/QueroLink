from django.db import migrations, models


def heal_missing_columns(apps, schema_editor):
    """Adiciona todas as colunas do modelo Boleto que nao existem no banco."""
    Boleto = apps.get_model('receivables', 'Boleto')
    table = Boleto._meta.db_table
    model_fields = {f.column: f for f in Boleto._meta.fields if not f.primary_key}

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            [table],
        )
        existing = {row[0] for row in cursor.fetchall()}

    for col_name, field in model_fields.items():
        if col_name in existing:
            continue
        try:
            schema_editor.add_field(Boleto, field)
        except Exception as e:
            # Fallback: SQL direto para tipos simples
            db_type = field.db_type(schema_editor.connection)
            nullable = 'NULL' if field.null else 'NOT NULL'
            default = ''
            if field.has_default():
                if isinstance(field.default, bool):
                    default = f" DEFAULT {'true' if field.default else 'false'}"
                elif isinstance(field.default, (int, str)):
                    default = f" DEFAULT '{field.default}'"
            sql = f'ALTER TABLE {table} ADD COLUMN {col_name} {db_type} {nullable}{default}'
            cursor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ('receivables', '0007_receivable_notification_delivery'),
    ]

    operations = [
        migrations.RunPython(heal_missing_columns, migrations.RunPython.noop),
    ]
