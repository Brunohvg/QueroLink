import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit

from app.apps.accounts.models import User
from app.apps.audit.utils import log_action
from . import services

logger = logging.getLogger(__name__)


def _get_correios_options(tenant, cep_destino_digits, weight_grams):
    if tenant.correios_cws_enabled:
        try:
            from .correios_cws import CorreiosAuthClient, CorreiosPricingClient
            token = CorreiosAuthClient().get_token(tenant)
            if token:
                options = CorreiosPricingClient(
                    token=token,
                    contrato=tenant.correios_contrato or '',
                    cartao=tenant.correios_cartao or '',
                ).calculate_batch(
                    cep_origem=''.join(filter(str.isdigit, tenant.store_cep or '')),
                    cep_destino=cep_destino_digits,
                    peso_gramas=weight_grams or 100,
                )
                valid = [o for o in options if not o.error and o.price_cents > 0]
                if valid:
                    return valid, True
        except Exception:
            logger.exception('CWS falhou para tenant %s — usando tabela', tenant.pk)
    result = services.estimate_correios(
        cep_destino_digits, weight_grams or 100,
        adjustment_percent=tenant.freight_adjustment_percent,
    )
    return result, False


# ── POST /api/freight/quote/ ────────────────────────────

@csrf_exempt
@ratelimit(key='user', rate='30/m', method='POST', block=True)
def freight_quote_view(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'error': 'Autenticacao necessaria.'}, status=401)

    tenant = request.user.tenant
    if not tenant:
        return JsonResponse({'success': False, 'error': 'Tenant nao encontrado.'}, status=400)

    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'JSON invalido.'}, status=400)

    cep_destino = body.get('cep_destino', '').strip()
    weight_grams = body.get('weight_grams', 0)

    digits = ''.join(filter(str.isdigit, cep_destino))
    if len(digits) != 8:
        return JsonResponse({'success': False, 'error': 'CEP invalido. Use 8 digitos.'}, status=400)

    if not tenant.store_cep:
        return JsonResponse({
            'success': False,
            'error': 'Peca ao gestor para configurar o CEP da loja em Configuracoes.',
        }, status=400)

    cep_client = services.ViaCepClient()
    cep_info = cep_client.get_cep_info(digits)
    if not cep_info:
        return JsonResponse({'success': False, 'error': 'CEP nao encontrado.'}, status=404)

    options, is_official = _get_correios_options(tenant, digits, weight_grams)

    motoboy = services.estimate_motoboy(tenant, cep_info)
    if motoboy:
        options.append(motoboy)

    destination = {
        'city': cep_info.city,
        'state': cep_info.state,
        'neighborhood': cep_info.neighborhood,
    }

    log_action(request, 'freight.quote', changes={
        'cep': digits, 'weight_grams': weight_grams,
        'city': cep_info.city, 'state': cep_info.state,
        'official': is_official,
    })

    return JsonResponse({
        'success': True,
        'official': is_official,
        'destination': destination,
        'options': [
            {
                'service': o.service,
                'label': o.label,
                'price_cents': o.price_cents,
                'delivery_days': o.delivery_days,
                'official': o.official,
                **({'error': o.error} if o.error else {}),
            }
            for o in options
        ],
        'disclaimer': ('Valores do seu contrato Correios.'
                       if is_official
                       else 'Valores estimados. Confirme no envio.'),
    })


# ── POST /api/freight/test-cws/ ──────────────────────────

@csrf_exempt
def freight_test_cws_view(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required.'}, status=405)

    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'error': 'Autenticacao necessaria.'}, status=401)

    if request.user.role != User.Role.ADMIN:
        return JsonResponse({'ok': False, 'error': 'Apenas o gestor pode testar.'}, status=403)

    tenant = request.user.tenant
    if not tenant:
        return JsonResponse({'ok': False, 'error': 'Tenant nao encontrado.'}, status=400)

    if not tenant.correios_cws_enabled:
        return JsonResponse({'ok': False, 'error': 'Credenciais dos Correios nao configuradas.'}, status=400)

    try:
        from .correios_cws import CorreiosAuthClient
        token = CorreiosAuthClient().get_token(tenant)
        if token:
            return JsonResponse({'ok': True})
        return JsonResponse({'ok': False, 'error': 'Credenciais invalidas.'})
    except Exception as e:
        logger.warning('test-cws falhou para tenant %s: %s', tenant.pk, e)
        return JsonResponse({'ok': False, 'error': 'Erro ao conectar aos Correios.'})
