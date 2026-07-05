from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django_ratelimit.decorators import ratelimit
from app.apps.orders.models import Order
from app.apps.accounts.models import Tenant, User
from django.db.models import Sum


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
@ratelimit(key='post:identifier', rate='5/m', method='POST', block=True)
def login_view(request):
    next_url = request.GET.get('next', 'dashboard:home')

    if request.user.is_authenticated:
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
            next_url = 'dashboard:home'
        return redirect(next_url)

    if request.method == 'POST':
        identifier = request.POST.get('identifier', request.POST.get('email', '')).strip()
        password = request.POST.get('password', '')

        user = authenticate(request, username=identifier, password=password)
        if user is None and '@' in identifier:
            from app.apps.accounts.models import User as UserModel
            try:
                u = UserModel.objects.get(email=identifier)
                user = authenticate(request, username=u.username, password=password)
            except UserModel.DoesNotExist:
                pass

        if user is not None:
            auth_login(request, user)

            if user.role == User.Role.SELLER:
                request.session.set_expiry(60 * 60 * 24 * 30)
            elif request.POST.get('remember_me'):
                request.session.set_expiry(60 * 60 * 24 * 30)
            else:
                request.session.set_expiry(0)

            next_url = request.POST.get('next', '')
            if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
                if user.role in (User.Role.MANAGER, User.Role.ADMIN):
                    next_url = 'dashboard:gestor_home'
                elif user.role == User.Role.FINANCEIRO:
                    next_url = 'dashboard:financeiro_fila'
                else:
                    next_url = 'dashboard:mobile_home'
            return redirect(next_url)
        else:
            messages.error(request, 'Credenciais invalidas.')

    return render(request, 'dashboard/login.html', {'next': next_url})

def logout_view(request):
    auth_logout(request)
    return redirect('dashboard:login')

@login_required
def plano_expirado(request):
    tenant = request.user.tenant
    trial_expired = (
        tenant
        and tenant.trial_ends_at
        and tenant.trial_ends_at < timezone.now()
    )
    return render(request, 'dashboard/plano_expirado.html', {
        'tenant': tenant,
        'trial_expired': trial_expired,
        'suspended': tenant and not tenant.is_active,
    })


@login_required
def assinatura(request):
    tenant = request.user.tenant
    from app.apps.billing.models import Subscription, plan_amount
    from app.apps.webhooks.models import WebhookEvent
    from app.apps.sellers.models import Seller
    from django.conf import settings

    if request.method == 'POST':
        billing_email = request.POST.get('billing_email', '').strip()
        if billing_email:
            tenant.billing_email = billing_email
            tenant.save(update_fields=['billing_email', 'updated_at'])
            from django.contrib import messages
            messages.success(request, 'Email de cobranca atualizado.')
        return redirect('dashboard:assinatura')

    try:
        sub = Subscription.objects.get(tenant=tenant)
    except Subscription.DoesNotExist:
        sub = None

    limits = getattr(settings, 'PLAN_SELLER_LIMITS', {})
    prices = getattr(settings, 'PLAN_PRICES', {})
    plan_limit = limits.get(tenant.plan, None)

    active_sellers = Seller.objects.filter(tenant=tenant, is_active=True).count()

    billing_history = []
    if sub and sub.gateway_subscription_id:
        events = WebhookEvent.objects.filter(
            gateway='mercadopago',
        ).order_by('-received_at')[:50]

        for ev in events:
            p = ev.payload if isinstance(ev.payload, dict) else {}
            data = p.get('data', {})
            if str(data.get('id', '')) != str(sub.gateway_subscription_id):
                continue
            mp_type = p.get('type', '')
            action = p.get('action', '')
            billing_history.append({
                'date': ev.received_at,
                'event': f'{mp_type}.{action}' if action else mp_type,
                'processed': ev.processed,
            })
            if len(billing_history) >= 12:
                break

    PLAN_FEATURES = {
        'STARTER': [
            'Ate 5 vendedores',
            'App do vendedor + dashboard do gestor',
            'Fechamento de comissoes',
            'Links de pagamento',
            'Lembretes automaticos por WhatsApp',
        ],
        'PRO': [
            'Ate 15 vendedores',
            'Tudo do Starter',
            'Pacote contabil automatico por e-mail para seu contador',
            'Relatorios em PDF e previa de fechamento',
            'Importacao de vendas por CSV',
            'Notificacoes push no celular do vendedor',
        ],
        'BUSINESS': [
            'Ate 50 vendedores',
            'Tudo do Pro',
            'Suporte prioritario',
        ],
        'ENTERPRISE': [
            'Vendedores ilimitados (legado)',
            'Tudo do Business',
            'Suporte dedicado',
        ],
    }

    has_active_sub = sub and sub.status not in ('CANCELED',)
    plan_names = dict(Tenant.Plan.choices)
    if 'ENTERPRISE' not in dict(plan_names):
        plan_names['ENTERPRISE'] = 'Enterprise (legado)'
    all_plans = []
    for key in ['STARTER', 'PRO', 'BUSINESS']:
        monthly = prices.get(key, 0)
        yearly = plan_amount(key, 'YEARLY') if monthly else 0
        all_plans.append({
            'id': key,
            'name': plan_names.get(key, key),
            'seller_limit': limits.get(key, '—'),
            'price_monthly': monthly,
            'price_yearly': yearly,
            'features': PLAN_FEATURES.get(key, []),
            'is_current': has_active_sub and tenant.plan == key,
        })

    limit_remaining = (plan_limit - active_sellers) if plan_limit is not None else None

    current_plan_amount = plan_amount(tenant.plan, sub.billing_cycle if sub else 'MONTHLY')

    return render(request, 'dashboard/assinatura.html', {
        'tenant': tenant,
        'subscription': sub,
        'plan_limit': plan_limit,
        'active_sellers': active_sellers,
        'billing_history': billing_history,
        'all_plans': all_plans,
        'limit_remaining': limit_remaining,
        'current_plan_amount': current_plan_amount,
    })


@login_required
def dashboard_home(request):
    user = request.user
    if user.role in (User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:gestor_home')
    elif user.role == User.Role.FINANCEIRO:
        return redirect('dashboard:financeiro_fila')
    elif user.role == User.Role.SELLER:
        return redirect('dashboard:mobile_home')

    tenant = request.user.tenant
    if not tenant:
        if request.user.is_superuser:
            tenant = Tenant.objects.first()
        if not tenant:
            return render(request, 'dashboard/index.html', {"error": "Nenhum tenant cadastrado no sistema ainda."})

    total_orders = Order.objects.filter(tenant=tenant).count()
    paid_orders = Order.objects.filter(tenant=tenant, status=Order.Status.COMPLETED)
    total_revenue = paid_orders.aggregate(total=Sum('total_amount'))['total'] or 0
    total_revenue_formatted = total_revenue / 100
    recent_orders = Order.objects.filter(tenant=tenant).order_by('-created_at')[:10]

    return render(request, 'dashboard/index.html', {
        'total_orders': total_orders,
        'paid_orders_count': paid_orders.count(),
        'total_revenue': total_revenue_formatted,
        'recent_orders': recent_orders,
    })


