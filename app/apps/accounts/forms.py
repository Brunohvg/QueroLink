import re
from django import forms
from django.contrib.auth.password_validation import validate_password
from app.apps.accounts.models import Tenant, User


def _clean_cnpj(cnpj):
    return re.sub(r'[^0-9]', '', cnpj)


def _validate_cnpj_check_digits(cnpj):
    cnpj = _clean_cnpj(cnpj)
    if len(cnpj) != 14:
        return False
    if cnpj == cnpj[0] * 14:
        return False

    def _calc_digit(prefix, weights):
        total = sum(int(prefix[i]) * weights[i] for i in range(len(weights)))
        remainder = total % 11
        return 0 if remainder < 2 else 11 - remainder

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

    d1 = _calc_digit(cnpj[:12], w1)
    d2 = _calc_digit(cnpj[:13], w2)

    return int(cnpj[12]) == d1 and int(cnpj[13]) == d2


class TenantRegistrationForm(forms.Form):
    company_name = forms.CharField(
        label='Nome da empresa',
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Nome da sua loja',
            'autofocus': True,
        }),
    )
    cnpj = forms.CharField(
        label='CNPJ',
        max_length=18,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '00.000.000/0000-00',
        }),
    )
    responsible_name = forms.CharField(
        label='Nome do responsavel',
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Seu nome completo',
        }),
    )
    email = forms.EmailField(
        label='E-mail',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'seu@email.com',
        }),
    )
    password = forms.CharField(
        label='Senha',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '••••••••',
        }),
    )
    password_confirm = forms.CharField(
        label='Confirmar senha',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '••••••••',
        }),
    )
    plan = forms.ChoiceField(
        label='Plano',
        choices=Tenant.Plan.choices,
        initial=Tenant.Plan.ESSENCIAL,
        widget=forms.Select(attrs={
            'class': 'form-control',
        }),
    )
    billing_cycle = forms.ChoiceField(
        label='Ciclo',
        choices=[('MONTHLY', 'Mensal'), ('YEARLY', 'Anual')],
        initial='MONTHLY',
        widget=forms.Select(attrs={
            'class': 'form-control',
        }),
    )

    website = forms.CharField(
        required=False,
        label='',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'style': 'display:none;',
            'tabindex': '-1',
            'autocomplete': 'off',
        }),
    )

    def clean_cnpj(self):
        cnpj = _clean_cnpj(self.cleaned_data['cnpj'])
        if len(cnpj) != 14 or not cnpj.isdigit():
            raise forms.ValidationError('CNPJ invalido. Digite os 14 digitos.')

        if not _validate_cnpj_check_digits(cnpj):
            raise forms.ValidationError('CNPJ invalido. Verifique os digitos e tente novamente.')

        if Tenant.objects.filter(cnpj=cnpj).exists():
            raise forms.ValidationError('Este CNPJ ja esta cadastrado no sistema.')

        return cnpj

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.filter(email=email).exists() or User.objects.filter(username=email).exists():
            raise forms.ValidationError('Este e-mail ja esta cadastrado no sistema.')
        return email

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        password_confirm = cleaned.get('password_confirm')
        email = cleaned.get('email')
        responsible_name = cleaned.get('responsible_name')

        if password and password_confirm and password != password_confirm:
            self.add_error('password_confirm', 'As senhas nao conferem.')

        if password and not self.errors:
            temp_user = User(
                username=email or '',
                email=email or '',
                first_name=responsible_name or '',
            )
            validate_password(password, user=temp_user)

        if cleaned.get('website'):
            raise forms.ValidationError('Submissao invalida.')

        return cleaned
