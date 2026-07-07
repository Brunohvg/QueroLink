from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def check_mercadopago_webhook_secret(app_configs, **kwargs):
    if getattr(settings, 'MP_ACCESS_TOKEN', '') and not getattr(settings, 'MP_WEBHOOK_SECRET', ''):
        return [
            Error(
                'MP_WEBHOOK_SECRET deve ser configurado quando MP_ACCESS_TOKEN esta ativo.',
                hint='Configure o segredo do webhook do Mercado Pago ou desative o billing limpando MP_ACCESS_TOKEN.',
                id='billing.E001',
            )
        ]
    return []
