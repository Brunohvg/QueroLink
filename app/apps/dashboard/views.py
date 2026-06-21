from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.crypto import get_random_string
from django.utils.text import slugify
from app.apps.orders.models import Order
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from django.db.models import Sum


def login_view(request):
    next_url = request.GET.get('next', 'dashboard:home')
    
    if request.user.is_authenticated:
        return redirect(next_url)
        
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        user = authenticate(request, username=email, password=password)
        if user is not None:
            auth_login(request, user)
            next_url = request.POST.get('next', '')
            if not next_url or next_url == 'dashboard:home':
                if user.role in (User.Role.MANAGER, User.Role.ADMIN):
                    next_url = 'dashboard:gestor_home'
                elif user.role == User.Role.FINANCEIRO:
                    next_url = 'dashboard:financeiro_fila'
                else:
                    next_url = 'dashboard:mobile_home'
            return redirect(next_url)
        else:
            messages.error(request, 'Email ou senha inválidos.')
            
    return render(request, 'dashboard/login.html', {'next': next_url})

def logout_view(request):
    auth_logout(request)
    return redirect('dashboard:login')

@login_required
def dashboard_home(request):
    # O dashboard agora é multi-tenant real: usa o tenant vinculado ao usuário logado
    tenant = request.user.tenant
    
    if not tenant:
        # Se for um superuser sem tenant, ele pode ter uma visão global ou ser bloqueado
        if request.user.is_superuser:
            tenant = Tenant.objects.first()
            if not tenant:
                return render(request, 'dashboard/index.html', {"error": "Nenhum tenant cadastrado no sistema ainda."})
        else:
            return render(request, 'dashboard/index.html', {"error": "Sua conta não está vinculada a nenhuma loja/tenant."})

    # Métricas
    total_orders = Order.objects.filter(tenant=tenant).count()
    paid_orders = Order.objects.filter(tenant=tenant, status=Order.Status.COMPLETED)
    total_revenue = paid_orders.aggregate(total=Sum('total_amount'))['total'] or 0
    total_revenue_formatted = total_revenue / 100

    recent_orders = Order.objects.filter(tenant=tenant).order_by('-created_at')[:10]

    context = {
        'total_orders': total_orders,
        'paid_orders_count': paid_orders.count(),
        'total_revenue': total_revenue_formatted,
        'recent_orders': recent_orders,
    }
    return render(request, 'dashboard/index.html', context)


@login_required
def seller_create(request):
    tenant = request.user.tenant
    if not tenant:
        return render(request, 'dashboard/seller_create.html', {"error": "Sua conta nao esta vinculada a nenhum tenant."})

    result = None

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()

        if not name:
            return render(request, 'dashboard/seller_create.html', {"error": "Nome do vendedor e obrigatorio."})
        if not phone:
            return render(request, 'dashboard/seller_create.html', {"error": "Telefone do vendedor e obrigatorio."})

        base_username = slugify(name)
        existing = set(User.objects.values_list("username", flat=True))
        username = base_username
        counter = 2
        while username in existing:
            username = f"{base_username}-{counter}"
            counter += 1

        password = get_random_string(12)

        user = User.objects.create_user(
            username=username,
            password=password,
            role=User.Role.SELLER,
            tenant=tenant,
        )

        commission_rate = tenant.default_commission_rate

        seller = Seller.objects.create(
            tenant=tenant,
            user=user,
            name=name,
            phone=phone,
            commission_rate=commission_rate,
        )

        notification_error = None
        try:
            from app.apps.notifications.tasks import notify_seller_credentials
            notify_seller_credentials(seller, password)
        except Exception as e:
            notification_error = str(e)

        result = {
            "seller": seller,
            "username": username,
            "password": password,
            "notification_error": notification_error,
        }

    return render(request, 'dashboard/seller_create.html', {
        "result": result,
        "error": request.GET.get("error", None),
    })
