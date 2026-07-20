from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from app.apps.accounts.models import User

from .models import Customer
from .query_services import (
    activity_detail_url,
    customer_detail_context,
    customer_summary_queryset,
    mask_document,
    mask_email,
    mask_phone,
)

ALLOWED_ROLES = (User.Role.ADMIN, User.Role.MANAGER, User.Role.FINANCEIRO)


def _allowed(request):
    return bool(
        request.user.is_authenticated
        and request.user.tenant_id
        and request.user.role in ALLOWED_ROLES
        and getattr(settings, 'CUSTOMER_LEDGER_UI_ENABLED', False)
    )


@login_required
def customer_list(request):
    if not _allowed(request):
        return redirect('dashboard:home')
    queryset = customer_summary_queryset(request.user.tenant, request.GET)
    page = Paginator(queryset, 25).get_page(request.GET.get('page'))
    rows = []
    for customer in page.object_list:
        rows.append({
            'customer': customer,
            'document_masked': mask_document(customer.document),
            'phone_masked': mask_phone(customer.phone),
            'email_masked': mask_email(customer.email),
            'seller_name': customer.last_seller_name or '',
        })
    return render(request, 'customers/list.html', {
        'page': page,
        'rows': rows,
        'filters': request.GET,
    })


@login_required
def customer_detail(request, customer_uuid):
    if not _allowed(request):
        return redirect('dashboard:home')
    customer = get_object_or_404(
        Customer, uuid=customer_uuid, tenant=request.user.tenant
    )
    totals, activities_queryset = customer_detail_context(customer)
    activities = []
    can_open_charges = request.user.role in (User.Role.ADMIN, User.Role.MANAGER)
    for activity in activities_queryset[:100]:
        activities.append({
            'activity': activity,
            'detail_url': activity_detail_url(activity) if can_open_charges else '',
        })
    return render(request, 'customers/detail.html', {
        'customer': customer,
        'document_masked': mask_document(customer.document),
        'phone_masked': mask_phone(customer.phone),
        'email_masked': mask_email(customer.email),
        'totals': totals,
        'activities': activities,
    })
