from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login
from django.contrib import messages
from django.db import transaction
from django_ratelimit.decorators import ratelimit
from app.apps.accounts.forms import TenantRegistrationForm
from app.apps.accounts.models import Tenant, User


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
            with transaction.atomic():
                tenant = Tenant.objects.create(
                    company_name=form.cleaned_data['company_name'],
                    cnpj=form.cleaned_data['cnpj'],
                    is_active=True,
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
                messages.success(request, f'Bem-vindo(a)! A loja "{tenant.company_name}" foi criada com sucesso.')
                return redirect('dashboard:gestor_home')

        for field, errors in form.errors.items():
            for error in errors:
                if field == '__all__':
                    messages.error(request, error)
                else:
                    messages.error(request, f'{form.fields.get(field, field).label if field in form.fields else field}: {error}')

    else:
        form = TenantRegistrationForm()

    return render(request, 'accounts/signup.html', {'form': form})
