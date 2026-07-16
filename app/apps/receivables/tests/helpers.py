from datetime import timedelta

from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.receivables.models import Boleto


def make_tenant(name='Loja Teste', plan=Tenant.Plan.PRO):
    return Tenant.objects.create(company_name=name, plan=plan)


def make_user(tenant, username, role):
    return User.objects.create_user(
        username=username,
        password='test-password-123',
        tenant=tenant,
        role=role,
    )


def make_seller(tenant, username='seller'):
    user = make_user(tenant, username, User.Role.SELLER)
    suffix = sum(ord(char) for char in username) % 10000
    seller = Seller.objects.create(
        tenant=tenant,
        user=user,
        name=f'Vendedor {username}',
        phone=f'31999{suffix:06d}'[-11:],
    )
    return user, seller


def boleto_data(**overrides):
    data = {
        'payer_name': 'Cliente Teste',
        'payer_document': '52998224725',
        'payer_document_type': Boleto.DocumentType.CPF,
        'payer_email': 'cliente@example.com',
        'payer_phone': '31988887777',
        'payer_zip_code': '30110000',
        'payer_street': 'Rua Teste',
        'payer_number': '100',
        'payer_complement': '',
        'payer_neighborhood': 'Centro',
        'payer_city': 'Belo Horizonte',
        'payer_state': 'MG',
        'amount_cents': 15000,
        'due_date': timezone.localdate() + timedelta(days=10),
        'instructions': 'Apos o vencimento: multa de 2% e juros de 1% ao mes.',
        'notes': '',
    }
    data.update(overrides)
    return data


def make_boleto(tenant, seller, created_by, **overrides):
    return Boleto.objects.create(
        tenant=tenant,
        seller=seller,
        created_by=created_by,
        gateway_charge_id=overrides.pop('gateway_charge_id', 'ch_test'),
        gateway_order_id=overrides.pop('gateway_order_id', 'or_test'),
        **boleto_data(**overrides),
    )
