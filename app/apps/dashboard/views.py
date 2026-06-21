from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from app.apps.orders.models import Order
from app.apps.accounts.models import Tenant, User
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


