from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Sum
from django.shortcuts import get_object_or_404, redirect, render

from app.apps.accounts.models import User, tenant_has_feature

from .models import Customer


def _manager_access(request):
    return request.user.role in (User.Role.ADMIN, User.Role.MANAGER)


@login_required
def customer_list(request):
    if not _manager_access(request):
        return redirect("dashboard:home")
    if not tenant_has_feature(request.user.tenant, "boletos"):
        return render(
            request, "dashboard/gestor/customers/list.html", {"feature_locked": True}
        )
    customers = Customer.objects.filter(tenant=request.user.tenant).annotate(
        interaction_count=Count("activities"),
        total_cents=Sum("activities__amount_cents"),
        last_interaction=Max("activities__occurred_at"),
    )
    search = request.GET.get("search", "").strip()
    if search:
        lowered = search.lower()
        digits = "".join(filter(str.isdigit, search))
        customers = [
            customer
            for customer in customers[:1000]
            if (
                lowered in customer.name.lower()
                or lowered in (customer.email or "").lower()
                or (digits and digits in (customer.phone or ""))
                or (digits and digits in (customer.document or ""))
            )
        ]
    else:
        customers = list(customers[:250])
    return render(
        request,
        "dashboard/gestor/customers/list.html",
        {
            "customers": customers[:250],
            "search": search,
            "customer_count": Customer.objects.filter(
                tenant=request.user.tenant
            ).count(),
        },
    )


@login_required
def customer_detail(request, customer_uuid):
    if not _manager_access(request):
        return redirect("dashboard:home")
    if not tenant_has_feature(request.user.tenant, "boletos"):
        return redirect("dashboard:gestor_customers")
    customer = get_object_or_404(
        Customer.objects.prefetch_related("activities"),
        tenant=request.user.tenant,
        uuid=customer_uuid,
    )
    totals = customer.activities.aggregate(
        total_cents=Sum("amount_cents"),
        count=Count("id"),
    )
    return render(
        request,
        "dashboard/gestor/customers/detail.html",
        {
            "customer": customer,
            "activities": customer.activities.all()[:100],
            "total_cents": totals["total_cents"] or 0,
            "interaction_count": totals["count"],
        },
    )
