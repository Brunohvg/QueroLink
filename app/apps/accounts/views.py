from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django_ratelimit.decorators import ratelimit
from app.apps.accounts.forms import TenantRegistrationForm
from app.apps.accounts.models import Tenant, User
from app.apps.audit.utils import log_action


# Tenant ativado imediatamente (is_active=True, login automatico) — decisao consciente do produto.
# Risco aceito: volume de cadastros e controlado, sem campanha publica de marketing ainda.
# Revisitar antes de qualquer divulgacao publica: considerar verificacao de e-mail (Opcao A).
@ratelimit(key='ip', rate='5/h', method='POST', block=True)
def signup_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:home')

    if request.method == 'POST':
        form = TenantRegistrationForm(request.POST)
        if form.is_valid():
            plan = form.cleaned_data['plan']
            billing_cycle = form.cleaned_data['billing_cycle']
            from datetime import timedelta

            with transaction.atomic():
                tenant = Tenant.objects.create(
                    company_name=form.cleaned_data['company_name'],
                    cnpj=form.cleaned_data['cnpj'],
                    is_active=True,
                    plan=plan,
                    billing_cycle=billing_cycle,
                    trial_ends_at=timezone.now() + timedelta(days=14),
                )

                email = form.cleaned_data['email']
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=form.cleaned_data['password'],
                    first_name=form.cleaned_data['responsible_name'],
                    role=User.Role.ADMIN,
                    tenant=tenant,
                )

            user = authenticate(request, username=email, password=form.cleaned_data['password'])
            if user is not None:
                auth_login(request, user)
                log_action(request, 'tenant.created', instance=tenant)
                messages.success(request, f'Bem-vindo(a)! A loja "{tenant.company_name}" foi criada com sucesso.')
                return redirect('dashboard:gestor_home')

        for field, errors in form.errors.items():
            for error in errors:
                if field == '__all__':
                    messages.error(request, error)
                else:
                    messages.error(request, f'{form.fields.get(field, field).label if field in form.fields else field}: {error}')

    else:
        initial = {}
        requested_plan = request.GET.get('plano', '').upper()
        if requested_plan in dict(Tenant.Plan.choices):
            initial['plan'] = requested_plan
        requested_cycle = request.GET.get('ciclo', '').upper()
        if requested_cycle in ('MONTHLY', 'YEARLY'):
            initial['billing_cycle'] = requested_cycle
        form = TenantRegistrationForm(initial=initial)

    return render(request, 'accounts/signup.html', {'form': form})


def landing_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:home')
    return render(request, 'accounts/landing.html')


def landing_page(request):
    if request.user.is_authenticated:
        return redirect('dashboard:home')
    return render(request, 'accounts/landing.html')
