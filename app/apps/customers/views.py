from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from app.apps.accounts.models import User, tenant_has_feature
from app.apps.audit.models import AuditLog

from .models import Customer


def _manager_access(request):
    return request.user.role in (User.Role.ADMIN, User.Role.MANAGER)


@login_required
def customer_list(request):
    if not _manager_access(request):
        return redirect("dashboard:home")
    if not tenant_has_feature(request.user.tenant, "customer_management"):
        return render(
            request, "dashboard/gestor/customers/list.html", {"feature_locked": True}
        )
    customers = Customer.objects.filter(tenant=request.user.tenant).annotate(
        interaction_count=Count("activities"),
        total_cents=Sum("activities__amount_cents"),
        last_interaction=Max("activities__occurred_at"),
    )
    search = request.GET.get("search", "").strip()
    consent = request.GET.get("consent", "").strip().upper()
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
    if consent:
        customers = [c for c in customers if c.marketing_consent == consent]
    return render(
        request,
        "dashboard/gestor/customers/list.html",
        {
            "customers": customers[:250],
            "search": search,
            "consent_filter": consent,
            "customer_count": Customer.objects.filter(
                tenant=request.user.tenant
            ).count(),
            "consented_count": Customer.objects.filter(
                tenant=request.user.tenant,
                marketing_consent=Customer.ConsentStatus.GRANTED,
            ).count(),
        },
    )


@login_required
def customer_detail(request, customer_uuid):
    if not _manager_access(request):
        return redirect("dashboard:home")
    if not tenant_has_feature(request.user.tenant, "customer_management"):
        return redirect("dashboard:gestor_customers")
    customer = get_object_or_404(
        Customer.objects.prefetch_related("activities"),
        tenant=request.user.tenant,
        uuid=customer_uuid,
    )
    if request.method == "POST":
        value = request.POST.get("marketing_consent", "").upper()
        source = request.POST.get("consent_source", "").strip()[:100]
        if (
            value in (Customer.ConsentStatus.GRANTED, Customer.ConsentStatus.REVOKED)
            and not source
        ):
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
                    "consent_choices": Customer.ConsentStatus.choices,
                    "consent_error": (
                        "Informe a origem do consentimento ou da revogacao."
                    ),
                },
                status=400,
            )
        if value in Customer.ConsentStatus.values:
            customer.marketing_consent = value
            customer.marketing_consent_at = timezone.now()
            customer.marketing_consent_source = source
            customer.marketing_consent_updated_by = request.user
            customer.notes = request.POST.get("notes", "").strip()
            customer.save(
                update_fields=[
                    "marketing_consent",
                    "marketing_consent_at",
                    "marketing_consent_source",
                    "marketing_consent_updated_by",
                    "notes",
                    "updated_at",
                ]
            )
            AuditLog.objects.create(
                user=request.user,
                tenant=request.user.tenant,
                action="customer.consent_updated",
                model_name="Customer",
                object_id=str(customer.uuid),
                changes={"marketing_consent": value},
            )
            return redirect(
                "dashboard:gestor_customer_detail", customer_uuid=customer.uuid
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
            "consent_choices": Customer.ConsentStatus.choices,
        },
    )
