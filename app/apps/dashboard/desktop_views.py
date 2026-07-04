import json
import logging
import uuid
from django.shortcuts import render, redirect, get_object_or_404

logger = logging.getLogger(__name__)
from django.http import JsonResponse, HttpResponseForbidden
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum
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

    from app.apps.commissions.services import get_dashboard_data
    from datetime import timedelta

    data = get_dashboard_data(tenant)

    hoje = timezone.localdate()
    config_ok = tenant.pagarme_configured and tenant.whatsapp_configured

    def _fmt(val):
        r = val // 100
        c = val % 100
        return f'{r:,}.{c:02d}'.replace(',', '.')

    competencia = CommissionPeriod.objects.filter(
        tenant=tenant,
        month=hoje.month,
        year=hoje.year,
    ).first()

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
    })


@login_required
def gestor_configuracoes(request):
    if not _check_role(request, User.Role.ADMIN):
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
                import json as _json_err
                return render(request, 'dashboard/gestor/configuracoes.html', {
                    'tenant': tenant, 'link_events': template_events_with_body,
                    'commission_rate_display': float(tenant.default_commission_rate or 0) * 100,
                    'event_vars_json': _json_err.dumps(EVENT_VARIABLES),
                })

        link_expires = request.POST.get('link_expires_in', '').strip()
        if link_expires:
            try:
                tenant.link_expires_in = int(link_expires)
            except ValueError:
                pass

        tenant.pix_enabled = request.POST.get('pix_enabled') == '1'
        tenant.ranking_visible_to_sellers = request.POST.get('ranking_visible_to_sellers') == '1'
        tenant.accountant_email = request.POST.get('accountant_email', '').strip() or None
        tenant.accountant_auto_send = request.POST.get('accountant_auto_send') == '1'

        tenant.save()

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

    import json as _json
    return render(request, 'dashboard/gestor/configuracoes.html', {
        'tenant': tenant,
        'link_events': template_events_with_body,
        'commission_rate_display': float(tenant.default_commission_rate) * 100,
        'event_vars_json': _json.dumps(EVENT_VARIABLES),
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
    if not _check_role(request, User.Role.ADMIN):
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
    if not _check_role(request, User.Role.ADMIN):
        return JsonResponse({'error': 'Permissao negada.'}, status=403)

    tenant = request.user.tenant
    if not tenant:
        return JsonResponse({'error': 'Tenant nao encontrado.'}, status=400)

    from app.services.messaging.whatsapp import (
        WhatsappClient, InstanceNotFoundError,
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
    if not _check_role(request, User.Role.ADMIN):
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
    if not _check_role(request, User.Role.ADMIN):
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
    return render(request, 'dashboard/gestor/vendedores.html')


@login_required
def gestor_importar_vendas(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    return render(request, 'dashboard/gestor/importar_vendas.html')


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

    return render(request, 'dashboard/gestor/vendedor_detalhe.html', {
        'seller': seller,
        'seller_json': {
            'uuid': str(seller.uuid),
            'name': seller.name,
        },
    })


@login_required
def gestor_fechamento(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    return render(request, 'dashboard/gestor/fechamento.html')


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
    ).select_related('seller', 'payment_link').order_by('-created_at')
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
            'status_display': o.get_status_display(),
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
        logger.error("gestor_link_detalhe error: %s", e, exc_info=True)
        raise


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
    amount = body.get('amount')
    is_partial = bool(amount and amount > 0)

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

    from app.apps.commissions.services import calculate_estimated_commission
    from app.apps.commissions.models import CommissionPeriod, SellerCommission
    from app.apps.sales.models import Sale as SModel

    competencias = []
    for i in range(12):
        month = hoje.month - i
        year = hoje.year
        while month <= 0:
            month += 12
            year -= 1

        total_sold = SModel.objects.filter(
            tenant=tenant, status='ATIVA',
            sale_date__month=month, sale_date__year=year,
        ).aggregate(t=Sum('amount'))['t'] or 0

        total_comm = 0
        sellers_qs = Seller.objects.filter(tenant=tenant, is_active=True)
        for s in sellers_qs:
            c, _ = calculate_estimated_commission(s, month, year)
            total_comm += c

        period = CommissionPeriod.objects.filter(tenant=tenant, month=month, year=year).first()
        if period:
            scs = SellerCommission.objects.filter(period=period)
            all_paid = scs.exists() and not scs.exclude(
                status__in=[SellerCommission.Status.PAGA, SellerCommission.Status.CANCELADA],
            ).exists()
            status = period.get_status_display()
            sent_at = period.sent_to_accounting_at
        else:
            all_paid = False
            status = 'Sem fechamento'
            sent_at = None

        competencias.append({
            'month': month,
            'year': year,
            'label': f'{month:02d}/{year}',
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
