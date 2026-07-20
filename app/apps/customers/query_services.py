from django.db.models import (
    Case,
    Count,
    Exists,
    IntegerField,
    Max,
    OuterRef,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from app.apps.accounts.fields import compute_hash

from .models import Customer, CustomerActivity

PAID_STATUSES = ('PAGO', 'COMPLETED', 'PAID')
OPEN_STATUSES = ('PENDENTE', 'PENDING')
OVERDUE_STATUSES = ('VENCIDO', 'EXPIRED')


def customer_summary_queryset(tenant, filters=None):
    filters = filters or {}
    latest_activity = CustomerActivity.objects.filter(
        customer=OuterRef('pk')
    ).order_by('-occurred_at')
    queryset = Customer.objects.filter(tenant=tenant).annotate(
        charge_count=Count('activities'),
        total_charged_cents=Coalesce(
            Sum('activities__amount_cents'), Value(0), output_field=IntegerField()
        ),
        total_paid_cents=Coalesce(
            Sum(Case(
                When(
                    activities__status__in=PAID_STATUSES,
                    then='activities__amount_cents',
                ),
                default=Value(0), output_field=IntegerField(),
            )),
            Value(0), output_field=IntegerField(),
        ),
        total_open_cents=Coalesce(
            Sum(Case(
                When(
                    activities__status__in=OPEN_STATUSES,
                    then='activities__amount_cents',
                ),
                default=Value(0), output_field=IntegerField(),
            )),
            Value(0), output_field=IntegerField(),
        ),
        total_overdue_cents=Coalesce(
            Sum(Case(
                When(
                    activities__status__in=OVERDUE_STATUSES,
                    then='activities__amount_cents',
                ),
                default=Value(0), output_field=IntegerField(),
            )),
            Value(0), output_field=IntegerField(),
        ),
        last_activity_at=Max('activities__occurred_at'),
        last_seller_name=Subquery(latest_activity.values('seller_name')[:1]),
    )

    name = str(filters.get('name', '')).strip()
    document = ''.join(filter(str.isdigit, filters.get('document', '')))
    phone = ''.join(filter(str.isdigit, filters.get('phone', '')))
    seller = str(filters.get('seller', '')).strip()
    document_type = str(filters.get('document_type', '')).strip().upper()
    state = str(filters.get('state', '')).strip().lower()

    if name:
        queryset = queryset.filter(name_hash=compute_hash(name))
    if document:
        queryset = queryset.filter(document_hash=compute_hash(document))
    if phone:
        queryset = queryset.filter(phone_hash=compute_hash(phone))
    if seller:
        queryset = queryset.filter(Exists(
            CustomerActivity.objects.filter(
                customer=OuterRef('pk'), seller_name__iexact=seller,
            )
        ))
    if document_type in ('CPF', 'CNPJ'):
        queryset = queryset.filter(document_type=document_type)
    if state == 'open':
        queryset = queryset.filter(Exists(
            CustomerActivity.objects.filter(
                customer=OuterRef('pk'), status__in=OPEN_STATUSES,
            )
        ))
    elif state == 'overdue':
        queryset = queryset.filter(Exists(
            CustomerActivity.objects.filter(
                customer=OuterRef('pk'), status__in=OVERDUE_STATUSES,
            )
        ))

    return queryset.distinct().order_by('-last_activity_at', '-updated_at')


def customer_detail_context(customer):
    activities = customer.activities.order_by('-occurred_at', '-created_at')
    totals = activities.aggregate(
        charge_count=Count('uuid'),
        total_charged_cents=Coalesce(
            Sum('amount_cents'), Value(0), output_field=IntegerField()
        ),
        total_paid_cents=Coalesce(
            Sum(Case(
                When(status__in=PAID_STATUSES, then='amount_cents'),
                default=Value(0), output_field=IntegerField(),
            )), Value(0), output_field=IntegerField(),
        ),
        total_open_cents=Coalesce(
            Sum(Case(
                When(status__in=OPEN_STATUSES, then='amount_cents'),
                default=Value(0), output_field=IntegerField(),
            )), Value(0), output_field=IntegerField(),
        ),
        total_overdue_cents=Coalesce(
            Sum(Case(
                When(status__in=OVERDUE_STATUSES, then='amount_cents'),
                default=Value(0), output_field=IntegerField(),
            )), Value(0), output_field=IntegerField(),
        ),
        last_activity_at=Max('occurred_at'),
    )
    seller = activities.exclude(seller_name='').values('seller_name').annotate(
        uses=Count('uuid')
    ).order_by('-uses', 'seller_name').first()
    totals['primary_seller_name'] = seller['seller_name'] if seller else ''
    return totals, activities


def mask_document(value):
    digits = ''.join(filter(str.isdigit, value or ''))
    if len(digits) == 11:
        return f'***.***.***-{digits[-2:]}'
    if len(digits) == 14:
        return f'**.***.***/****-{digits[-2:]}'
    return f'***{digits[-2:]}' if digits else ''


def mask_phone(value):
    digits = ''.join(filter(str.isdigit, value or ''))
    return f'({digits[:2]}) *****-{digits[-4:]}' if len(digits) >= 10 else ''


def mask_email(value):
    value = str(value or '').strip()
    if '@' not in value:
        return ''
    local, domain = value.rsplit('@', 1)
    return f'{local[:1]}***@{domain}'


def activity_detail_url(activity):
    if activity.source == CustomerActivity.Source.BOLETO:
        return f'/dashboard/gestor/boletos/{activity.source_uuid}/'
    if activity.source == CustomerActivity.Source.PAYMENT_LINK:
        return f'/dashboard/gestor/links/{activity.source_uuid}/'
    return ''
