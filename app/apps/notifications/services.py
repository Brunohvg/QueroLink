from django.db import transaction

from app.apps.accounts.models import Tenant
from app.apps.notifications.models import MessageTemplate


DEFAULT_MESSAGE_TEMPLATES = (
    (
        MessageTemplate.EventType.SELLER_CREDENTIALS,
        MessageTemplate.Channel.WHATSAPP,
        'Ola {{vendedor}}! Seu acesso ao sistema de comissoes foi criado.\n'
        'Usuario: {{usuario}}\n'
        'Sua senha chega na proxima mensagem. Toque nela, segure e escolha Copiar.\n'
        'Acesse e troque sua senha no primeiro login.',
    ),
    (
        MessageTemplate.EventType.COMMISSION_PAID,
        MessageTemplate.Channel.WHATSAPP,
        'Ola {{vendedor}}! Sua comissao de {{periodo}} no valor de {{valor}} foi paga. '
        'Confira os detalhes no app.',
    ),
    (
        MessageTemplate.EventType.COMMISSION_ADJUSTED,
        MessageTemplate.Channel.WHATSAPP,
        'Sua comissao do periodo {{periodo}} recebeu um ajuste de {{valor}}. '
        'Acesse o sistema para conferir os detalhes.',
    ),
    (
        MessageTemplate.EventType.LINK_CREATED,
        MessageTemplate.Channel.WHATSAPP,
        'Link de pagamento criado\n'
        'Cliente: {{cliente}}\n'
        'Valor: {{valor}}\n'
        'Pagamento: {{parcelas}}\n'
        'Link:\n'
        '{{link}}\n'
        'Encaminhe este link ao cliente.',
    ),
    (
        MessageTemplate.EventType.PAYMENT_PAID,
        MessageTemplate.Channel.WHATSAPP,
        'Ola {{vendedor}}! O link de {{valor}} do(a) {{cliente}} foi pago!',
    ),
    (
        MessageTemplate.EventType.LINK_CANCELED,
        MessageTemplate.Channel.WHATSAPP,
        'Ola {{vendedor}}! O link de {{valor}} do(a) {{cliente}} foi cancelado.',
    ),
    (
        MessageTemplate.EventType.PAYMENT_EXPIRED,
        MessageTemplate.Channel.WHATSAPP,
        'Ola {{vendedor}}! O link de {{valor}} do(a) {{cliente}} expirou.',
    ),
    (
        MessageTemplate.EventType.PAYMENT_FAILED,
        MessageTemplate.Channel.WHATSAPP,
        'Ola {{vendedor}}! O pagamento de {{valor}} do(a) {{cliente}} falhou. '
        'Motivo: {{motivo}}',
    ),
    (
        MessageTemplate.EventType.PAYMENT_REFUNDED,
        MessageTemplate.Channel.WHATSAPP,
        'Ola {{vendedor}}! O pagamento de {{valor}} do(a) {{cliente}} foi estornado.',
    ),
    (
        MessageTemplate.EventType.DAILY_REMINDER,
        MessageTemplate.Channel.WHATSAPP,
        'Ola {{vendedor}}! Voce ainda nao lancou suas vendas de hoje. '
        'Lance agora pelo app para manter sua comissao em dia.',
    ),
)


def ensure_default_message_templates(tenant, dry_run=False):
    summary = {'created': 0, 'existing': 0, 'missing': 0}

    if dry_run:
        for event_type, channel, _body in DEFAULT_MESSAGE_TEMPLATES:
            exists = MessageTemplate.objects.filter(
                tenant=tenant,
                event_type=event_type,
                channel=channel,
            ).exists()
            if exists:
                summary['existing'] += 1
            else:
                summary['missing'] += 1
        return summary

    with transaction.atomic():
        locked_tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
        for event_type, channel, body in DEFAULT_MESSAGE_TEMPLATES:
            exists = MessageTemplate.objects.filter(
                tenant=locked_tenant,
                event_type=event_type,
                channel=channel,
            ).exists()
            if exists:
                summary['existing'] += 1
                continue
            MessageTemplate.objects.create(
                tenant=locked_tenant,
                event_type=event_type,
                channel=channel,
                body=body,
            )
            summary['created'] += 1
    return summary
