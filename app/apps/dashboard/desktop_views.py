import json
import logging
import uuid
from django.shortcuts import render, redirect, get_object_or_404

logger = logging.getLogger(__name__)
from django.http import JsonResponse, HttpResponseForbidden, Http404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum
from django_ratelimit.decorators import ratelimit
from app.apps.accounts.models import User, Tenant
from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod


def _check_role(request, *roles):
    if not request.user.is_authenticated:
        return False
    return request.user.role in roles


@login_required
def gestor_home(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    if request.method == 'POST' and request.POST.get('action') == 'dismiss_onboarding':
        from app.apps.accounts.models import OnboardingProgress
        ob, _ = OnboardingProgress.objects.get_or_create(tenant=tenant)
        ob.dismissed = True
        ob.save()
        return redirect('dashboard:gestor_home')

    from app.apps.commissions.services import (
        build_period_selector_context, get_dashboard_data, PeriodNotFound,
    )
    from datetime import timedelta
    try:
        selector_ctx = build_period_selector_context(request, tenant)
    except PeriodNotFound as exc:
        raise Http404(str(exc)) from exc
    selected_period = selector_ctx['selected_period']

    data = get_dashboard_data(tenant, period=selected_period)

    hoje = timezone.localdate()
    config_ok = tenant.pagarme_configured and tenant.whatsapp_configured

    def _fmt(val):
        r = val // 100
        c = val % 100
        return f'{r:,}.{c:02d}'.replace(',', '.')

    competencia = selected_period

    public_url = f"https://{settings.SERVICE_FQDN_WEB}/loja/{tenant.slug}/"

    total_comissao = data['commission_aberta'] + data['commission_fechada'] + data['commission_paga']

    vendedores_ativos = data.get('vendedores_ativos', 0)

    sellers_sem_lancamento = []
    if hoje.weekday() != 6:
        all_sellers = Seller.objects.filter(tenant=tenant, is_active=True)
        sellers_com_venda = Sale.objects.filter(
            tenant=tenant, origin=Sale.Origin.MANUAL, status='ATIVA',
            sale_date=hoje,
        ).values_list('seller_id', flat=True).distinct()
        sellers_sem_lancamento = list(
            all_sellers.exclude(uuid__in=sellers_com_venda).values_list('name', flat=True)
        )

    whatsapp_ok = bool(tenant.whatsapp_instance_id and tenant.whatsapp_token)
    pagarme_ok = tenant.pagarme_configured

    from app.apps.webhooks.models import WebhookEvent
    webhooks_pendentes = WebhookEvent.objects.filter(
        tenant=tenant, processed=False,
        received_at__lt=timezone.now() - timedelta(minutes=10),
    ).count()

    vendas_hoje = Sale.objects.filter(
        tenant=tenant, status='ATIVA', sale_date=hoje,
    ).aggregate(total=Sum('amount'))['total'] or 0
    dia_semana_passada = hoje - timedelta(days=7)
    vendas_semana_passada = Sale.objects.filter(
        tenant=tenant, status='ATIVA', sale_date=dia_semana_passada,
    ).aggregate(total=Sum('amount'))['total'] or 0
    variacao_semanal = (
        round((vendas_hoje - vendas_semana_passada) / vendas_semana_passada * 100)
        if vendas_semana_passada > 0 else None
    )

    from app.apps.billing.models import Subscription
    sub = Subscription.objects.filter(tenant=tenant).first()

    boleto_home_stats = {}
    from app.apps.accounts.models import tenant_has_feature
    if tenant_has_feature(tenant, 'boletos'):
        from app.apps.receivables.models import Boleto
        boleto_base = Boleto.objects.filter(tenant=tenant)
        boleto_home_stats = {
            'a_receber': boleto_base.filter(status='PENDENTE', due_date__gte=hoje).count(),
            'vencidos': boleto_base.filter(status__in=('PENDENTE', 'VENCIDO'), due_date__lt=hoje).count(),
            'pagos_mes': boleto_base.filter(
                status='PAGO', paid_at__month=hoje.month, paid_at__year=hoje.year,
            ).count(),
        }

    return render(request, 'dashboard/gestor/home.html', {
        'total_mes': data['total_vendido'],
        'total_mes_fmt': _fmt(data['total_vendido']),
        'comissao_estimada': total_comissao,
        'comissao_estimada_fmt': _fmt(total_comissao),
        'competencia': competencia,
        'vendedores_ativos': vendedores_ativos,
        'config_ok': config_ok,
        'total_orders': data['links_gerados'],
        'paid_orders_count': data['links_pagos'],
        'public_url': public_url,
        'dashboard_data': data,
        'sellers_sem_lancamento': sellers_sem_lancamento,
        'lancados_hoje': vendedores_ativos - len(sellers_sem_lancamento) if hoje.weekday() != 6 else None,
        'is_domingo': hoje.weekday() == 6,
        'whatsapp_ok': whatsapp_ok,
        'pagarme_ok': pagarme_ok,
        'webhooks_pendentes': webhooks_pendentes,
        'vendas_hoje': vendas_hoje,
        'vendas_semana_passada': vendas_semana_passada,
        'variacao_semanal': variacao_semanal,
        'onboarding': getattr(tenant, 'onboarding', None),
        'period_selector': selector_ctx,
        'periods': selector_ctx['periods'],
        'has_periods': selector_ctx['has_periods'],
        'selected_period': selector_ctx['selected_period'],
        'selected_period_uuid': selector_ctx['selected_period_uuid'],
        'boleto_home_stats': boleto_home_stats,
        'has_boletos_home': bool(boleto_home_stats),
    })


@login_required
def gestor_configuracoes(request):
    if not _check_role(request, User.Role.ADMIN, User.Role.MANAGER):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    from app.apps.notifications.models import MessageTemplate, EVENT_VARIABLES, EVENT_LABELS

    event_types = list(EVENT_LABELS.keys())

    templates_dict = {
        t.event_type: t.body
        for t in MessageTemplate.objects.filter(
            tenant=tenant, channel='whatsapp',
            event_type__in=event_types,
        )
    }
    template_events_with_body = [
        (e, EVENT_LABELS[e], templates_dict.get(e, ''), EVENT_VARIABLES.get(e, []))
        for e in event_types
    ]

    if request.method == 'POST':
        pagarme_api_key = request.POST.get('pagarme_api_key', '').strip()
        whatsapp_instance_id = request.POST.get('whatsapp_instance_id', '').strip()
        commission_rate = request.POST.get('default_commission_rate', '').strip()

        if pagarme_api_key and pagarme_api_key != '••••••••':
            tenant.pagarme_api_key = pagarme_api_key

        webhook_username = request.POST.get('pagarme_webhook_username', '').strip()
        webhook_password = request.POST.get('pagarme_webhook_password', '').strip()
        if webhook_username and webhook_username != '••••••••':
            tenant.pagarme_webhook_username = webhook_username
        if webhook_password and webhook_password != '••••••••':
            tenant.pagarme_webhook_password = webhook_password

        if whatsapp_instance_id:
            base = whatsapp_instance_id.strip().lower().replace(' ', '-')
            if not base.startswith(f"{tenant.slug}-"):
                base = f"{tenant.slug}-{base}"
            tenant.whatsapp_instance_id = base
        if commission_rate:
            try:
                tenant.default_commission_rate = float(
                    commission_rate.replace(',', '.'),
                ) / 100
            except ValueError:
                messages.error(request, 'Taxa de comissao invalida.')
                return render(request, 'dashboard/gestor/configuracoes.html', {
                    'tenant': tenant, 'link_events': template_events_with_body,
                    'commission_rate_display': float(tenant.default_commission_rate or 0) * 100,
                    'event_vars_json': EVENT_VARIABLES,
                })

        link_expires = request.POST.get('link_expires_in', '').strip()
        if link_expires:
            try:
                tenant.link_expires_in = int(link_expires)
            except ValueError:
                pass

        period_start_day = request.POST.get('period_start_day', '').strip()
        if period_start_day:
            try:
                psd = int(period_start_day)
            except (ValueError, TypeError):
                messages.error(request, 'Dia de inicio do periodo invalido.')
            else:
                if 1 <= psd <= 28:
                    tenant.period_start_day = psd
                else:
                    messages.error(request, 'Dia de inicio do periodo deve estar entre 1 e 28.')

        tenant.pix_enabled = request.POST.get('pix_enabled') == '1'
        tenant.ranking_visible_to_sellers = request.POST.get('ranking_visible_to_sellers') == '1'
        tenant.accountant_email = request.POST.get('accountant_email', '').strip() or None
        tenant.accountant_auto_send = request.POST.get('accountant_auto_send') == '1'

        store_cep = request.POST.get('store_cep', '').strip()
        if store_cep:
            digits = ''.join(filter(str.isdigit, store_cep))
            if len(digits) == 8:
                tenant.store_cep = f'{digits[:5]}-{digits[5:]}'
            else:
                messages.error(request, 'CEP da loja invalido. Use 8 digitos (ex: 00000-000).')
        freight_adjustment = request.POST.get('freight_adjustment_percent', '').strip()
        if freight_adjustment:
            try:
                val = int(freight_adjustment)
                tenant.freight_adjustment_percent = max(-50, min(100, val))
            except ValueError:
                pass
        presets_json = request.POST.get('freight_presets_json', '')
        if presets_json:
            try:
                import json as _json
                raw = _json.loads(presets_json)
                valid = []
                if isinstance(raw, list):
                    for item in raw[:6]:
                        name = str(item.get('name', '')).strip()[:20]
                        try:
                            weight = int(item.get('weight_grams', 0))
                        except (TypeError, ValueError):
                            continue
                        if name and 50 <= weight <= 30000:
                            valid.append({'name': name, 'weight_grams': weight})
                tenant.freight_presets = valid
            except (ValueError, TypeError):
                pass
        tenant.motoboy_enabled = request.POST.get('motoboy_enabled') == '1'
        motoboy_price = request.POST.get('motoboy_price_per_km_cents', '').strip()
        if motoboy_price:
            try:
                tenant.motoboy_price_per_km_cents = int(round(float(motoboy_price.replace(',', '.')) * 100))
            except ValueError:
                pass
        motoboy_min = request.POST.get('motoboy_min_price_cents', '').strip()
        if motoboy_min:
            try:
                tenant.motoboy_min_price_cents = int(round(float(motoboy_min.replace(',', '.')) * 100))
            except ValueError:
                pass
        motoboy_max = request.POST.get('motoboy_max_km', '').strip()
        if motoboy_max:
            try:
                tenant.motoboy_max_km = int(motoboy_max)
            except ValueError:
                pass

        correios_usuario = None
        if 'correios_usuario' in request.POST:
            correios_usuario = request.POST.get('correios_usuario', '').strip()
            tenant.correios_usuario = correios_usuario or None
        if 'correios_codigo_acesso' in request.POST:
            correios_codigo = request.POST.get('correios_codigo_acesso', '').strip()
            if correios_codigo and correios_codigo != '••••••••':
                tenant.correios_codigo_acesso = correios_codigo
            elif correios_usuario == '':
                tenant.correios_codigo_acesso = None
        if 'correios_contrato' in request.POST:
            correios_contrato = request.POST.get('correios_contrato', '').strip()
            tenant.correios_contrato = correios_contrato or None
        if 'correios_cartao' in request.POST:
            correios_cartao = request.POST.get('correios_cartao', '').strip()
            tenant.correios_cartao = correios_cartao or None

        weekday_values = request.POST.getlist('working_weekdays')
        try:
            working_weekdays_parsed = sorted(set(int(w) for w in weekday_values if 0 <= int(w) <= 6))
            tenant.working_weekdays = working_weekdays_parsed if working_weekdays_parsed else [0, 1, 2, 3, 4, 5]
        except (ValueError, TypeError):
            pass
        tenant.skip_national_holidays = request.POST.get('skip_national_holidays') == '1'

        tenant.save()

        if request.POST.get('test_cws') == '1' and tenant.correios_cws_enabled:
            try:
                from app.apps.freight.correios_cws import CorreiosAuthClient, CorreiosPricingClient
                token = CorreiosAuthClient().get_token(tenant)
                if token:
                    cep_origem = ''.join(filter(str.isdigit, tenant.store_cep or '')) or '01001000'
                    client = CorreiosPricingClient(
                        token=token,
                        contrato=tenant.correios_contrato or '',
                        dr=token.get('_resolved_dr', '') if isinstance(token, dict) else '',
                    )
                    options = client.calculate_batch(cep_origem, cep_origem, 300)
                    valid = [o for o in options if not o.error and o.price_cents > 0]
                    if valid:
                        sedex = next((o for o in valid if o.service == '03220'), valid[0])
                        valor = f'{sedex.price_cents / 100:.2f}'.replace('.', ',')
                        messages.success(request, f'Correios conectado — cotação oficial funcionando (SEDEX R$ {valor}).')
                    else:
                        msg = '; '.join(client.last_errors) or 'cotacao sem retorno'
                        messages.warning(request, f'Token OK, mas a cotação falhou: {msg}. Verifique contrato/cartão.')
                else:
                    messages.error(request, 'Falha na conexao: credenciais invalidas. Verifique usuario e codigo de acesso.')
            except Exception:
                messages.error(request, 'Erro ao conectar aos Correios. Tente novamente.')

        from app.apps.accounts.models import mark_onboarding_step
        if tenant.whatsapp_instance_id and tenant.whatsapp_token:
            mark_onboarding_step(tenant, 'step_whatsapp')
        if tenant.default_commission_rate and float(tenant.default_commission_rate) > 0:
            mark_onboarding_step(tenant, 'step_commission_rate')

        for event_type in event_types:
            body = request.POST.get(f'template_{event_type}', '').strip()
            if body:
                MessageTemplate.objects.update_or_create(
                    tenant=tenant, event_type=event_type, channel='whatsapp',
                    defaults={'body': body, 'is_active': True},
                )

        messages.success(request, 'Configuracoes salvas com sucesso.')
        return redirect('dashboard:gestor_configuracoes')

    from app.apps.freight.services import get_freight_presets
    freight_presets = get_freight_presets(tenant)
    working_weekdays_list = tenant.working_weekdays or [0, 1, 2, 3, 4, 5]
    return render(request, 'dashboard/gestor/configuracoes.html', {
        'tenant': tenant,
        'link_events': template_events_with_body,
        'commission_rate_display': float(tenant.default_commission_rate) * 100,
        'event_vars_json': EVENT_VARIABLES,
        'freight_presets_json': freight_presets,
        'motoboy_price_brl': (tenant.motoboy_price_per_km_cents if tenant.motoboy_price_per_km_cents > 0 else 200) / 100.0,
        'motoboy_min_price_brl': (tenant.motoboy_min_price_cents if tenant.motoboy_min_price_cents > 0 else 800) / 100.0,
        'correios_cws_enabled': tenant.correios_cws_enabled,
        'working_weekdays_list': working_weekdays_list,
    })


def _resolve_tenant_instance(tenant, instance_id):
    """Resolve o instance_id garantindo isolamento entre tenants.
    Retorna (instance_id, None) se OK, ou (None, mensagem_erro) se invalido."""
    if not instance_id:
        return None, 'Nome da instancia nao configurado.'
    prefix = f"{tenant.slug}-"
    if instance_id.startswith(prefix):
        if instance_id == prefix.rstrip('-') or instance_id == prefix:
            return None, f'Escolha um nome unico apos o prefixo. Ex: {prefix}loja'
        return instance_id, None
    if '-' in instance_id:
        return None, 'Esta instancia pertence a outro lojista.'
    return None, f'O nome deve comecar com "{prefix}". Ex: {prefix}loja'


@login_required
def whatsapp_instance_status(request):
    if not _check_role(request, User.Role.ADMIN, User.Role.MANAGER):
        return JsonResponse({'error': 'Permissao negada.'}, status=403)

    tenant = request.user.tenant
    if not tenant:
        return JsonResponse({'error': 'Tenant nao encontrado.'}, status=400)

    from app.services.messaging.whatsapp import (
        WhatsappClient, WhatsAppError, InstanceNotFoundError,
        AuthenticationError, ConnectionError,
    )

    instance_id = request.GET.get('instance') or tenant.whatsapp_instance_id or ''
    global_key = getattr(settings, 'WHATSAPP_API_KEY', '')

    instance_id, error = _resolve_tenant_instance(tenant, instance_id)
    if error:
        return JsonResponse({
            'connected': False, 'state': 'not_configured',
            'error': error,
        }, status=400)
    if not global_key:
        return JsonResponse({
            'connected': False, 'state': 'not_configured',
            'error': 'WHATSAPP_API_KEY nao configurada no servidor.',
        })

    try:
        import hashlib, hmac
        from urllib.parse import quote
        webhook_token = hmac.new(
            settings.WHATSAPP_API_KEY.encode(),
            str(tenant.uuid).encode(),
            hashlib.sha256,
        ).hexdigest()[:16]

        client = WhatsappClient(
            instance=instance_id,
            api_key=global_key,
            webhook_url=request.build_absolute_uri(
                f'/api/webhooks/evolution/{quote(instance_id, safe="")}/{tenant.uuid}/{webhook_token}/'
            ),
        )
        dados = client.create_or_get_qrcode()
        instance_key = dados.get('instance_api_key')
        if instance_key:
            tenant.whatsapp_instance_id = instance_id
            tenant.whatsapp_token = instance_key
            tenant.save(update_fields=['whatsapp_instance_id', 'whatsapp_token'])
        return JsonResponse({
            'connected': dados.get('state') == 'open',
            'state': dados.get('state'),
            'qrcode_base64': dados.get('qrcode_base64'),
            'pairing_code': dados.get('pairing_code'),
            'instance_created': dados.get('instance_created', False),
        })
    except InstanceNotFoundError:
        return JsonResponse({
            'connected': False,
            'state': 'not_found',
            'error': 'Instancia nao encontrada. Verifique o nome da instancia.',
        }, status=404)
    except AuthenticationError:
        return JsonResponse({
            'connected': False,
            'state': 'auth_error',
            'error': 'Chave de API invalida. Verifique o token.',
        }, status=401)
    except ConnectionError as e:
        return JsonResponse({
            'connected': False,
            'state': 'error',
            'error': str(e),
        }, status=500)
    except WhatsAppError as e:
        msg = str(e)
        state = 'already_connected' if 'ja esta conectada' in msg else 'error'
        return JsonResponse({
            'connected': False,
            'state': state,
            'error': msg,
        }, status=400 if state == 'error' else 200)
    except Exception as e:
        return JsonResponse({
            'connected': False,
            'state': 'error',
            'error': f'Erro inesperado: {e}',
        }, status=500)


@login_required
def whatsapp_connection_state(request):
    if not _check_role(request, User.Role.ADMIN, User.Role.MANAGER):
        return JsonResponse({'error': 'Permissao negada.'}, status=403)

    tenant = request.user.tenant
    if not tenant:
        return JsonResponse({'error': 'Tenant nao encontrado.'}, status=400)

    from app.services.messaging.whatsapp import (
        WhatsappClient, WhatsAppError, InstanceNotFoundError,
        AuthenticationError, ConnectionError,
    )

    instance_id = request.GET.get('instance') or tenant.whatsapp_instance_id or ''

    instance_id, error = _resolve_tenant_instance(tenant, instance_id)
    if error:
        return JsonResponse({
            'connected': False, 'state': 'not_configured',
            'error': error,
        }, status=400)

    api_key = tenant.whatsapp_token or getattr(settings, 'WHATSAPP_API_KEY', '')
    if not api_key:
        return JsonResponse({
            'connected': False, 'state': 'not_configured',
            'error': 'WHATSAPP_API_KEY nao configurada no servidor. Adicione a variavel no Coolify.',
        })

    try:
        client = WhatsappClient(
            instance=instance_id,
            api_key=api_key,
        )
        data = client.get_connection_state()
        return JsonResponse({
            'connected': data.get('connected'),
            'state': data.get('state'),
            'instance_name': data.get('instance_name'),
            'owner': data.get('owner'),
        })
    except InstanceNotFoundError:
        return JsonResponse({
            'connected': False, 'state': 'not_found',
            'error': 'Instancia nao encontrada. Verifique o nome.',
        }, status=404)
    except AuthenticationError:
        return JsonResponse({
            'connected': False, 'state': 'auth_error',
            'error': 'Chave de API invalida.',
        }, status=401)
    except ConnectionError as e:
        return JsonResponse({
            'connected': False, 'state': 'error',
            'error': str(e),
        }, status=500)
    except WhatsAppError as e:
        return JsonResponse({
            'connected': False, 'state': 'error',
            'error': str(e),
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'connected': False, 'state': 'error',
            'error': f'Erro inesperado: {e}',
        }, status=500)


@login_required
def whatsapp_disconnect(request):
    if not _check_role(request, User.Role.ADMIN, User.Role.MANAGER):
        return JsonResponse({'error': 'Permissao negada.'}, status=403)

    tenant = request.user.tenant
    if not tenant:
        return JsonResponse({'error': 'Tenant nao encontrado.'}, status=400)

    from app.services.messaging.whatsapp import (
        WhatsappClient, WhatsAppError,
    )

    instance_id = request.GET.get('instance') or tenant.whatsapp_instance_id or ''
    instance_id, error = _resolve_tenant_instance(tenant, instance_id)
    if error:
        return JsonResponse({'error': error}, status=400)

    api_key = tenant.whatsapp_token or getattr(settings, 'WHATSAPP_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'WHATSAPP_API_KEY nao configurada.'}, status=400)

    try:
        client = WhatsappClient(instance=instance_id, api_key=api_key)
        client.disconnect()
        return JsonResponse({'disconnected': True})
    except WhatsAppError as e:
        return JsonResponse({'error': str(e)}, status=500)
    except Exception as e:
        return JsonResponse({'error': f'Erro inesperado: {e}'}, status=500)


@login_required
def whatsapp_delete_instance(request):
    if not _check_role(request, User.Role.ADMIN, User.Role.MANAGER):
        return JsonResponse({'error': 'Permissao negada.'}, status=403)

    tenant = request.user.tenant
    if not tenant:
        return JsonResponse({'error': 'Tenant nao encontrado.'}, status=400)

    from app.services.messaging.whatsapp import (
        WhatsappClient, WhatsAppError,
    )

    instance_id = request.GET.get('instance') or tenant.whatsapp_instance_id or ''
    instance_id, error = _resolve_tenant_instance(tenant, instance_id)
    if error:
        return JsonResponse({'error': error}, status=400)

    api_key = getattr(settings, 'WHATSAPP_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'WHATSAPP_API_KEY nao configurada.'}, status=400)

    try:
        client = WhatsappClient(instance=instance_id, api_key=api_key)
        client.delete_instance()
        tenant.whatsapp_instance_id = ''
        tenant.whatsapp_token = ''
        tenant.save(update_fields=['whatsapp_instance_id', 'whatsapp_token'])
        return JsonResponse({'deleted': True})
    except WhatsAppError as e:
        return JsonResponse({'error': str(e)}, status=500)
    except Exception as e:
        return JsonResponse({'error': f'Erro inesperado: {e}'}, status=500)


@login_required
def gestor_ranking(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    return render(request, 'dashboard/gestor/ranking.html')


@login_required
def gestor_vendedores(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    tenant = request.user.tenant
    selector_ctx = {}
    if tenant:
        from app.apps.commissions.services import (
            build_period_selector_context, PeriodNotFound,
        )
        try:
            selector_ctx = build_period_selector_context(request, tenant)
        except PeriodNotFound as exc:
            raise Http404(str(exc)) from exc
    return render(request, 'dashboard/gestor/vendedores.html', {
        'periods': selector_ctx.get('periods', []),
        'has_periods': selector_ctx.get('has_periods', False),
        'selected_period': selector_ctx.get('selected_period'),
        'selected_period_uuid': selector_ctx.get('selected_period_uuid', ''),
    })


@login_required
def gestor_importar_vendas(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    selector_ctx = {}
    if tenant:
        from app.apps.commissions.services import (
            build_period_selector_context, PeriodNotFound,
        )
        try:
            selector_ctx = build_period_selector_context(request, tenant)
        except PeriodNotFound:
            pass

    return render(request, 'dashboard/gestor/importar_vendas.html', {
        'periods': selector_ctx.get('periods', []),
        'has_periods': selector_ctx.get('has_periods', False),
        'selected_period': selector_ctx.get('selected_period'),
        'selected_period_uuid': selector_ctx.get('selected_period_uuid', ''),
    })


@login_required
def gestor_download_template(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    from django.http import HttpResponse
    from datetime import datetime

    try:
        start_str = request.GET.get('start')
        end_str = request.GET.get('end')
        if start_str and end_str:
            from datetime import datetime
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
        else:
            from app.apps.commissions.models import CommissionPeriod
            period_uuid = request.GET.get('period_uuid')
            if period_uuid:
                period = CommissionPeriod.objects.get(uuid=period_uuid, tenant=tenant)
                start_date = period.start_date
                end_date = period.end_date
            else:
                hoje = timezone.localdate()
                start_date = hoje.replace(day=1)
                end_date = hoje

        if end_date < start_date:
            from django.contrib import messages
            messages.error(request, 'Data final deve ser posterior a data inicial.')
            return redirect('dashboard:gestor_importar_vendas')

        max_days = 62
        if (end_date - start_date).days > max_days:
            from django.contrib import messages
            messages.error(request, f'Periodo maximo de {max_days} dias.')
            return redirect('dashboard:gestor_importar_vendas')

        from app.apps.sales.services_matrix import generate_template_xlsx
        output = generate_template_xlsx(tenant, start_date, end_date)

        filename = f'modelo_vendas_{start_date.strftime("%Y%m%d")}_{end_date.strftime("%Y%m%d")}.xlsx'
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    except Exception:
        logger.exception('Erro ao gerar template XLSX')
        from django.contrib import messages
        messages.error(
            request,
            'Nao foi possivel gerar o modelo. Tente novamente.',
        )
        return redirect('dashboard:gestor_importar_vendas')


@login_required
def gestor_vendedor_detalhe(request, seller_id):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    try:
        seller = Seller.objects.get(uuid=seller_id, tenant=tenant)
    except Seller.DoesNotExist:
        return redirect('dashboard:gestor_vendedores')

    from app.apps.commissions.services import (
        build_period_selector_context, PeriodNotFound,
    )
    try:
        selector_ctx = build_period_selector_context(request, tenant)
    except PeriodNotFound as exc:
        raise Http404(str(exc)) from exc

    return render(request, 'dashboard/gestor/vendedor_detalhe.html', {
        'seller': seller,
        'seller_json': {
            'uuid': str(seller.uuid),
            'name': seller.name,
        },
        'periods': selector_ctx['periods'],
        'has_periods': selector_ctx['has_periods'],
        'selected_period': selector_ctx['selected_period'],
        'selected_period_uuid': selector_ctx['selected_period_uuid'],
    })


@login_required
def gestor_fechamento(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    tenant = request.user.tenant
    has_export = bool(
        tenant and getattr(settings, 'PLAN_FEATURES', {}).get(tenant.plan, {}).get('export_contabil', False)
    )
    return render(request, 'dashboard/gestor/fechamento.html', {
        'has_export': has_export,
        'accountant_email': tenant.accountant_email if tenant else '',
        'accountant_auto_send': tenant.accountant_auto_send if tenant else False,
    })


@login_required
def gestor_previa_fechamento(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    hoje = timezone.localdate()
    return render(request, 'dashboard/gestor/previa_fechamento.html', {
        'current_month': hoje.month,
        'current_year': hoje.year,
        'years': list(range(hoje.year - 2, hoje.year + 1)),
    })


@login_required
def financeiro_fila(request):
    if not _check_role(
        request, User.Role.FINANCEIRO, User.Role.ADMIN, User.Role.MANAGER,
    ):
        return redirect('dashboard:home')
    return render(request, 'dashboard/financeiro/fila_aprovacao.html')


@login_required
def financeiro_historico(request):
    if not _check_role(
        request, User.Role.FINANCEIRO, User.Role.ADMIN, User.Role.MANAGER,
    ):
        return redirect('dashboard:home')
    return render(request, 'dashboard/financeiro/historico_pagamentos.html')


@login_required
def gestor_webhooks(request):
    if not _check_role(request, User.Role.ADMIN, User.Role.MANAGER):
        return redirect('dashboard:home')
    tenant = request.user.tenant

    from app.apps.webhooks.models import WebhookEvent
    from datetime import timedelta
    from django.conf import settings

    now = timezone.now()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)

    base_qs = WebhookEvent.objects.filter(gateway='pagarme', tenant=tenant)

    recent_24h = base_qs.filter(received_at__gte=last_24h).count()
    pending_10min = base_qs.filter(
        processed=False, received_at__lte=now - timedelta(minutes=10),
    ).count()
    ignored_7d = base_qs.filter(
        processed=True, received_at__gte=last_7d,
    ).exclude(skip_reason__isnull=True).count()

    events = base_qs.order_by('-received_at')[:50]

    webhook_url = (
        f"https://{settings.SERVICE_FQDN_WEB}"
        f"/api/webhooks/pagarme/{tenant.slug}/"
    )
    creds_configured = bool(
        tenant.pagarme_webhook_username and tenant.pagarme_webhook_password
    )

    events_data = []
    for e in events:
        if e.skip_reason:
            status_label = 'Ignorado'
        elif e.processed:
            status_label = 'Processado'
        else:
            status_label = 'Pendente'

        events_data.append({
            'received_at': e.received_at,
            'event_type': e.payload.get('type', '?') if isinstance(e.payload, dict) else '?',
            'status_label': status_label,
            'processed': e.processed,
            'skip_reason': e.skip_reason,
            'payload': e.payload,
        })

    return render(request, 'dashboard/gestor/webhooks.html', {
        'tenant': tenant,
        'events': events_data,
        'recent_24h': recent_24h,
        'pending_10min': pending_10min,
        'ignored_7d': ignored_7d,
        'webhook_url': webhook_url,
        'creds_configured': creds_configured,
    })


@login_required
def gestor_cobrancas(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    from app.apps.dashboard.charge_center import (
        build_charge_center, charge_center_stats,
    )
    from app.apps.sellers.models import Seller as SellerModel
    can_create_links = True

    from app.apps.accounts.models import can_create_receivable
    can_create_boletos = can_create_receivable(request.user, tenant)

    cobrancas, orders_data, boletos_data = build_charge_center(tenant)

    logger.info(
        "gestor_cobrancas: tenant=%s orders=%d boletos=%d total=%d",
        tenant.pk, len(orders_data), len(boletos_data), len(cobrancas),
    )

    unified_stats = charge_center_stats(tenant)

    sellers = list(SellerModel.objects.filter(
        tenant=tenant, is_active=True,
    ).values('uuid', 'name'))

    return render(request, 'dashboard/gestor/cobrancas.html', {
        'cobrancas_json': cobrancas,
        'sellers': sellers,
        'can_create_links': can_create_links,
        'can_create_boletos': can_create_boletos,
        'unified_stats_json': unified_stats,
        'pagarme_configured': tenant.pagarme_configured,
        'boleto_stats_json': {
            'a_receber': (
                unified_stats['boleto_awaiting']
                + unified_stats['boleto_overdue']
            ),
            'vencidos': unified_stats['boleto_overdue'],
            'pagos': unified_stats['boleto_paid'],
        },
    })


@login_required
def gestor_link_new(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    if request.method == 'POST':
        from app.apps.orders.services import create_payment_link
        from app.apps.sellers.models import Seller

        raw_value = request.POST.get('amount', '').replace('R$', '').replace(',', '.').strip()
        try:
            total_amount = int(round(float(raw_value) * 100))
        except (ValueError, TypeError):
            messages.error(request, 'Valor invalido.')
            return redirect('dashboard:gestor_link_new')

        if total_amount < 100:
            messages.error(request, 'Valor minimo e R$ 1,00.')
            return redirect('dashboard:gestor_link_new')

        customer_name = request.POST.get('customer_name', '').strip()
        if not customer_name:
            messages.error(request, 'Nome do cliente e obrigatorio.')
            return redirect('dashboard:gestor_link_new')

        seller_uuid = request.POST.get('seller_uuid', '')
        if not seller_uuid:
            messages.error(request, 'Selecione um vendedor.')
            return redirect('dashboard:gestor_link_new')

        try:
            seller = Seller.objects.get(uuid=seller_uuid, tenant=tenant)
        except Seller.DoesNotExist:
            messages.error(request, 'Vendedor nao encontrado.')
            return redirect('dashboard:gestor_link_new')

        installments_str = request.POST.get('installments', '1')
        try:
            installments = int(installments_str)
            installments = max(1, min(installments, 12))
        except (ValueError, TypeError):
            installments = 1

        try:
            order, link_url = create_payment_link(
                tenant=tenant, seller=seller,
                customer_name=customer_name,
                amount_cents=total_amount,
                installments=installments,
            )
            from app.apps.audit.utils import log_action
            log_action(request, 'order.link_created', instance=order)

            try:
                from app.apps.notifications.tasks import notify_seller_link_status
                notify_seller_link_status(seller, order, 'link_created')
            except Exception:
                logger.error(
                    'Failed to send link_created notification for order %s',
                    order.uuid, exc_info=True,
                )

            messages.success(request, 'Link de pagamento gerado com sucesso!')
            return redirect('dashboard:gestor_link_detalhe', order_uuid=order.uuid)
        except Exception:
            logger.exception('Erro ao criar link de pagamento')
            messages.error(request, 'Erro ao criar link. Tente novamente.')
            return redirect('dashboard:gestor_link_new')

    from app.apps.sellers.models import Seller as SellerModel
    sellers = list(SellerModel.objects.filter(
        tenant=tenant, is_active=True,
    ).values('uuid', 'name').order_by('name'))

    return render(request, 'dashboard/gestor/links/new.html', {
        'sellers': sellers,
    })


@login_required
def gestor_links(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    from app.apps.orders.models import Order

    seller_uuid = request.GET.get('seller')
    orders = Order.objects.filter(
        tenant=tenant,
    ).select_related('seller', 'payment_link').prefetch_related(
        'payments',
    ).order_by('-created_at')
    if seller_uuid:
        orders = orders.filter(seller__uuid=seller_uuid)
    orders = orders[:100]

    logger.info("gestor_links: tenant=%s orders_count=%d", tenant.pk, orders.count())

    orders_data = []
    for o in orders:
        payment = o.payments.first()
        refusal = payment.refusal_reason if payment else None
        link = o.payment_link if hasattr(o, 'payment_link') else None
        link_url = link.gateway_url if link else None
        try:
            customer_name = o.customer_name
        except Exception as e:
            customer_name = '[erro: %s]' % e
            logger.error("gestor_links order=%s customer_name error: %s", o.uuid, e)
        orders_data.append({
            'uuid': str(o.uuid),
            'customer_name': customer_name,
            'amount': o.total_amount,
            'status': o.status,
            'status_display': o.status_display_pt,
            'seller_name': o.seller.name if o.seller else '-',
            'seller_uuid': str(o.seller.uuid) if o.seller else '',
            'refusal_reason': refusal,
            'created_at': o.created_at.isoformat(),
            'link_url': link_url or '',
        })

    from app.apps.sellers.models import Seller as SellerModel
    sellers = list(SellerModel.objects.filter(
        tenant=tenant, is_active=True,
    ).values('uuid', 'name'))

    logger.info("gestor_links: returning %d orders, %d sellers", len(orders_data), len(sellers))

    return render(request, 'dashboard/gestor/links.html', {
        'orders_json': orders_data,
        'sellers': sellers,
    })


@login_required
def gestor_link_detalhe(request, order_uuid):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    from app.apps.orders.models import Order, PaymentLink
    from app.apps.payments.models import Payment

    try:
        order = get_object_or_404(Order, uuid=order_uuid, tenant=tenant)
        payment = order.payments.order_by('created_at').first()
        payment_link = PaymentLink.objects.filter(order=order).first()

        total_centavos = order.total_amount
        valor_reais = f"{total_centavos // 100},{total_centavos % 100:02d}"

        raw_payload = (payment.raw_callback_payload or {}) if payment else {}
        last_txn = raw_payload.get('last_transaction') or {}

        return render(request, 'dashboard/gestor/link_detalhe.html', {
            'order': order,
            'payment': payment,
            'payment_link': payment_link,
            'valor_reais': valor_reais,
            'paid_at': payment.paid_at if payment else None,
            'card_brand': payment.card_brand if payment else '',
            'card_last4': payment.card_last4 if payment else '',
            'acquirer_message': last_txn.get('acquirer_message', '') if payment else '',
            'acquirer_name': last_txn.get('acquirer_name', '') if payment else '',
            'refusal_reason': payment.refusal_reason if payment else '',
            'clicks_count': payment_link.clicks_count if payment_link else 0,
            'opened_at': payment_link.opened_at if payment_link else None,
            'expires_at': payment_link.expires_at if payment_link else None,
        })
    except Exception as e:
        logger.exception('Erro ao carregar detalhe do link %s', order_uuid)
        messages.error(request, 'Erro ao carregar os dados do link. Tente novamente.')
        return redirect('dashboard:gestor_links')


@login_required
def gestor_link_cancelar(request, order_uuid):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        messages.error(request, 'Permissao negada.')
        return redirect('dashboard:gestor_links')

    tenant = request.user.tenant
    from app.apps.orders.models import Order, PaymentLink
    order = get_object_or_404(Order, uuid=order_uuid, tenant=tenant)

    if order.status != Order.Status.PENDING:
        messages.error(request, 'So e possivel cancelar links pendentes.')
        return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)

    # Cancel on Pagar.me first
    payment_link = PaymentLink.objects.filter(order=order).first()
    if payment_link and payment_link.gateway_link_id:
        try:
            from app.services.gateway.pagar_me import PagarMeGateway
            gw = PagarMeGateway(api_key=tenant.pagarme_api_key or '')
            gw.cancel_payment_link(payment_link.gateway_link_id)
        except Exception as e:
            logger.warning("Pagar.me cancel failed (link already invalid?): %s", e)

    order.status = Order.Status.CANCELED
    order.save(update_fields=['status'])

    if order.seller:
        try:
            from app.apps.notifications.tasks import notify_seller_link_status
            notify_seller_link_status(order.seller, order, 'link_canceled')
        except Exception:
            logger.error(
                'Failed to send link_canceled notification for order %s',
                order.uuid, exc_info=True
            )

    messages.success(request, 'Link cancelado com sucesso.')
    return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)


@login_required
@ratelimit(key='user', rate='6/m', method='POST', block=True)
def gestor_link_verificar_pagamento(request, order_uuid):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        messages.error(request, 'Permissao negada.')
        return redirect('dashboard:gestor_links')
    if request.method != 'POST':
        return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)

    tenant = request.user.tenant
    from app.apps.orders.models import Order
    from app.apps.webhooks.models import WebhookEvent
    from app.apps.webhooks.services import process_paid_pagarme_event
    from app.apps.webhooks.tasks import _notify_link_status_after_commit
    from app.services.gateway.pagar_me import PagarMeGateway
    from app.apps.audit.utils import log_action

    order = get_object_or_404(Order, uuid=order_uuid, tenant=tenant)
    if not tenant.pagarme_api_key:
        messages.error(request, 'Pagar.me nao configurado.')
        return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)

    try:
        gateway = PagarMeGateway(api_key=tenant.pagarme_api_key)
        remote_order = gateway.find_order_by_code(str(order.uuid))
    except Exception:
        logger.exception('Verificacao Pagar.me falhou para order %s', order.uuid)
        messages.error(request, 'Nao foi possivel consultar o Pagar.me agora.')
        return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)

    if not remote_order:
        messages.info(request, 'Pagamento ainda nao localizado no Pagar.me.')
        return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)

    if remote_order.get('status') != 'paid':
        messages.info(request, 'Pagamento ainda nao confirmado no Pagar.me.')
        return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)

    event_id_str = f'manual_verify_{order.uuid}_{remote_order.get("id", "")}'
    event, _ = WebhookEvent.objects.get_or_create(
        gateway='pagarme',
        gateway_event_id=event_id_str,
        defaults={
            'tenant': tenant,
            'payload': {
                'id': event_id_str,
                'type': 'order.paid',
                'data': remote_order,
            },
        },
    )
    result = process_paid_pagarme_event(event.id)
    if result.notify_event_type and result.order_uuid:
        refreshed = Order.objects.select_related('seller').get(uuid=result.order_uuid)
        if refreshed.seller:
            _notify_link_status_after_commit(refreshed, result.notify_event_type)

    log_action(request, 'pagarme.payment_verified', instance=order, changes={
        'event_id': event.id,
        'result': result.status,
    })
    messages.success(request, 'Verificacao concluida. Status atualizado quando confirmado.')
    return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)


@login_required
@ratelimit(key='user', rate='6/m', method='POST', block=True)
def gestor_link_reenviar_vendedor(request, order_uuid):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        messages.error(request, 'Permissao negada.')
        return redirect('dashboard:gestor_links')
    if request.method != 'POST':
        return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)

    tenant = request.user.tenant
    from app.apps.orders.models import Order
    from app.apps.notifications.tasks import notify_seller_link_status
    from app.apps.audit.utils import log_action

    order = get_object_or_404(Order, uuid=order_uuid, tenant=tenant)
    if not order.seller:
        messages.error(request, 'Link sem vendedor vinculado.')
        return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)

    try:
        notification = notify_seller_link_status(order.seller, order, 'link_created')
    except Exception:
        logger.exception('Reenvio de link falhou para order %s', order.uuid)
        messages.error(request, 'Nao foi possivel reenviar o link ao vendedor.')
        return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)

    log_action(request, 'order.link_resent_to_seller', instance=order)
    if notification is None:
        messages.error(request, 'Vendedor sem telefone para receber o link.')
    else:
        messages.success(request, 'Link reenviado ao vendedor.')
    return redirect('dashboard:gestor_link_detalhe', order_uuid=order_uuid)


@login_required
def gestor_link_estornar(request, order_uuid):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return JsonResponse({'error': 'Permissao negada.'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo nao permitido.'}, status=405)

    tenant = request.user.tenant
    from app.apps.orders.models import Order
    from app.apps.payments.models import Payment
    order = get_object_or_404(Order, uuid=order_uuid, tenant=tenant)
    payment = order.payments.order_by('created_at').first()

    if not payment or payment.status != Payment.Status.PAID:
        return JsonResponse({'error': 'Pagamento nao esta concluido.'}, status=400)

    if not payment.gateway_transaction_id:
        return JsonResponse({'error': 'Transacao nao encontrada no gateway.'}, status=400)

    import json as json_module
    try:
        body = json_module.loads(request.body) if request.body else {}
    except Exception:
        body = {}
    raw_amount = body.get('amount')
    try:
        amount = int(raw_amount) if raw_amount not in (None, '') else 0
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Valor de estorno invalido.'}, status=400)
    if amount < 0:
        return JsonResponse({'error': 'Valor de estorno invalido.'}, status=400)
    if amount > order.total_amount:
        return JsonResponse({'error': 'Valor de estorno maior que o pagamento.'}, status=400)
    is_partial = amount > 0

    from app.services.gateway.pagar_me import PagarMeGateway
    try:
        gw = PagarMeGateway(api_key=tenant.pagarme_api_key)
        if is_partial:
            gw.partial_cancel_charge(payment.gateway_transaction_id, amount)
        else:
            gw.cancel_charge(payment.gateway_transaction_id)
    except Exception as e:
        return JsonResponse({'error': f'Erro ao estornar: {e}'}, status=500)

    payment.status = Payment.Status.REFUNDED if not is_partial else Payment.Status.REFUNDED
    payment.save(update_fields=['status'])
    order.status = Order.Status.CANCELED
    order.save(update_fields=['status'])

    msg = 'Estorno parcial' if is_partial else 'Estorno total'
    return JsonResponse({'message': f'{msg} realizado com sucesso.'})


@login_required
def admin_metrics(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden('Acesso restrito.')

    from datetime import timedelta
    from django.db.models import Sum as DSum, Count as DCount
    from app.apps.accounts.models import Tenant, OnboardingProgress
    from app.apps.billing.models import Subscription
    from app.apps.sales.models import Sale
    from app.apps.notifications.models import Notification
    from app.apps.sellers.models import Seller

    hoje = timezone.localdate()
    trinta_dias = hoje - timedelta(days=30)

    total_tenants = Tenant.objects.filter(is_active=True).count()
    trial_ativos = Tenant.objects.filter(
        is_active=True, trial_ends_at__gt=timezone.now(),
    ).exclude(subscription__status='ACTIVE').count()
    pagantes = Subscription.objects.filter(status='ACTIVE').count()
    churn_30d = Subscription.objects.filter(
        status='CANCELED', updated_at__date__gte=trinta_dias,
    ).count()
    mrr = Subscription.objects.filter(status='ACTIVE').aggregate(
        total=DSum('amount'),
    )['total'] or 0

    criados_30d = Tenant.objects.filter(created_at__date__gte=trinta_dias).count()
    ativados_30d = OnboardingProgress.objects.filter(
        completed_at__isnull=False, completed_at__date__gte=trinta_dias,
    ).count()
    taxa_ativacao = round(ativados_30d / criados_30d * 100) if criados_30d > 0 else 0

    vendas_30d = Sale.objects.filter(created_at__date__gte=trinta_dias, status='ATIVA').count()
    notificacoes_30d = Notification.objects.filter(
        created_at__date__gte=trinta_dias, status='SENT',
    ).count()

    top_tenants = Sale.objects.filter(
        sale_date__month=hoje.month, sale_date__year=hoje.year, status='ATIVA',
    ).values('tenant__company_name').annotate(
        total=DSum('amount'), count=DCount('id'),
    ).order_by('-total')[:5]

    recentes = Tenant.objects.filter(is_active=True).order_by('-created_at')[:10]
    recentes_data = []
    for t in recentes:
        sub = Subscription.objects.filter(tenant=t).first()
        sellers_count = Seller.objects.filter(tenant=t, is_active=True).count()
        ob = OnboardingProgress.objects.filter(tenant=t).first()
        recentes_data.append({
            'name': t.company_name,
            'plan': t.get_plan_display(),
            'created': t.created_at.strftime('%d/%m/%Y'),
            'status': sub.status if sub else ('trial' if t.trial_ends_at and t.trial_ends_at > timezone.now() else 'expirado'),
            'sellers': sellers_count,
            'onboarding': 'Sim' if ob and ob.completed_at else 'Nao',
        })

    def _fmt(val):
        r = val // 100
        c = val % 100
        return f'{r:,}.{c:02d}'.replace(',', '.')

    return render(request, 'dashboard/admin/metrics.html', {
        'total_tenants': total_tenants,
        'trial_ativos': trial_ativos,
        'pagantes': pagantes,
        'mrr': mrr,
        'mrr_fmt': _fmt(mrr),
        'churn_30d': churn_30d,
        'taxa_ativacao': taxa_ativacao,
        'vendas_30d': vendas_30d,
        'notificacoes_30d': notificacoes_30d,
        'top_tenants': top_tenants,
        'recentes_data': recentes_data,
    })


@login_required
def gestor_contabilidade(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    hoje = timezone.localdate()

    from decimal import Decimal, ROUND_HALF_UP
    from app.apps.commissions.models import CommissionPeriod, SellerCommission
    from app.apps.commissions.services import get_commission_rate
    from app.apps.sales.models import Sale as SModel

    sellers_qs = list(Seller.objects.filter(tenant=tenant, is_active=True))

    periods_qs = CommissionPeriod.objects.filter(
        tenant=tenant,
    ).exclude(
        status=CommissionPeriod.Status.CANCELADA,
    ).prefetch_related('seller_commissions').order_by('-start_date')[:12]

    competencias = []
    for period in periods_qs:
        total_sold = SModel.objects.filter(
            tenant=tenant, status='ATIVA',
            origin__in=SModel.COMMISSION_ORIGINS,
            sale_date__gte=period.start_date,
            sale_date__lte=period.end_date,
        ).aggregate(t=Sum('amount'))['t'] or 0

        total_comm = 0
        scs = list(period.seller_commissions.all())
        sc_by_seller = {sc.seller_id: sc for sc in scs}
        for s in sellers_qs:
            sc = sc_by_seller.get(s.pk)
            if sc and period.status != CommissionPeriod.Status.ABERTA:
                total_comm += sc.commission_amount
            else:
                seller_sales = SModel.objects.filter(
                    tenant=tenant, seller=s, status='ATIVA',
                    origin__in=SModel.COMMISSION_ORIGINS,
                    sale_date__gte=period.start_date,
                    sale_date__lte=period.end_date,
                ).aggregate(t=Sum('amount'))['t'] or 0
                rate = get_commission_rate(s)
                total_comm += int(
                    (Decimal(str(seller_sales)) * rate).quantize(
                        Decimal('1'), rounding=ROUND_HALF_UP,
                    )
                )

        all_paid = bool(scs) and not [
            sc for sc in scs
            if sc.status not in (
                SellerCommission.Status.PAGA, SellerCommission.Status.CANCELADA,
            )
        ]
        status = period.get_status_display()
        sent_at = period.sent_to_accounting_at

        competencias.append({
            'month': period.month,
            'year': period.year,
            'label': period.display_label,
            'start_date': period.start_date,
            'end_date': period.end_date,
            'range': f'{period.start_date.strftime("%d/%m/%Y")} a {period.end_date.strftime("%d/%m/%Y")}',
            'total_sold': total_sold,
            'total_comm': total_comm,
            'status': status,
            'is_complete': all_paid,
            'sent_to_accounting_at': sent_at,
        })

    sellers_sem_cpf = list(Seller.objects.filter(
        tenant=tenant, is_active=True, cpf__isnull=True,
    ).values_list('name', flat=True))
    has_pendencia_cpf = bool(sellers_sem_cpf)

    has_export = getattr(settings, 'PLAN_FEATURES', {}).get(tenant.plan, {}).get('export_contabil', False)

    return render(request, 'dashboard/gestor/contabilidade.html', {
        'competencias': competencias,
        'accountant_email': tenant.accountant_email,
        'accountant_auto_send': tenant.accountant_auto_send,
        'has_export': has_export,
        'tenant': tenant,
        'sellers_sem_cpf': sellers_sem_cpf,
        'has_pendencia_cpf': has_pendencia_cpf,
    })
