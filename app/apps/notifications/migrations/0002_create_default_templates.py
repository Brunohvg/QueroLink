from django.db import migrations


DEFAULT_TEMPLATES = [
    {
        "event_type": "seller_credentials",
        "channel": "whatsapp",
        "body": (
            "Ola {{vendedor}}! Seu acesso ao sistema de comissoes foi criado.\n"
            "Usuario: {{usuario}}\n"
            "Senha temporaria: {{senha}}\n"
            "Acesse e troque sua senha no primeiro login."
        ),
    },
    {
        "event_type": "commission_paid",
        "channel": "whatsapp",
        "body": (
            "Ola {{vendedor}}! Sua comissao de {{periodo}} "
            "no valor de {{valor}} foi paga. "
            "Confira os detalhes no app."
        ),
    },
]


def create_default_templates(apps, schema_editor):
    MessageTemplate = apps.get_model("notifications", "MessageTemplate")
    Tenant = apps.get_model("accounts", "Tenant")

    for tenant in Tenant.objects.all():
        for tmpl in DEFAULT_TEMPLATES:
            _, created = MessageTemplate.objects.get_or_create(
                tenant=tenant,
                event_type=tmpl["event_type"],
                channel=tmpl["channel"],
                defaults={"body": tmpl["body"]},
            )
            if created:
                print(f"  Template {tmpl['event_type']}/{tmpl['channel']} criado para tenant {tenant.company_name}")


def remove_default_templates(apps, schema_editor):
    MessageTemplate = apps.get_model("notifications", "MessageTemplate")
    MessageTemplate.objects.filter(
        event_type__in=[t["event_type"] for t in DEFAULT_TEMPLATES],
        channel__in=list({t["channel"] for t in DEFAULT_TEMPLATES}),
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0001_generalize_notification"),
    ]

    operations = [
        migrations.RunPython(
            create_default_templates,
            reverse_code=remove_default_templates,
        ),
    ]
