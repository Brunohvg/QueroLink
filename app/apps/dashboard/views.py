from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from app.apps.orders.models import Order
from app.apps.accounts.models import Tenant
from django.db.models import Sum

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:home')
        
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        user = authenticate(request, username=email, password=password)
        if user is not None:
            auth_login(request, user)
            return redirect('dashboard:home')
        else:
            messages.error(request, 'Email ou senha inválidos.')
            
    return render(request, 'dashboard/login.html')

def logout_view(request):
    auth_logout(request)
    return redirect('dashboard:login')

@login_required
def dashboard_home(request):
    # Por enquanto assumimos que o usuário pertence ao primeiro tenant (ou criamos uma lógica de multi-tenant se o usuário tiver tenant atrelado)
    # Como o sistema tem um tenant único no momento ("Bibelô Oficial"):
    tenant = Tenant.objects.first()
    
    if not tenant:
        return render(request, 'dashboard/index.html', {"error": "Nenhum tenant configurado."})

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
