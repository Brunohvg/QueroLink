import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse
from django_ratelimit.decorators import ratelimit
from app.apps.orders.models import Order
from app.apps.accounts.models import Tenant, tenant_operational
from app.apps.sellers.models import Seller
from app.apps.orders.services import create_payment_link

logger = logging.getLogger(__name__)


def _check_tenant_operational(tenant_slug):
    tenant = get_object_or_404(Tenant, slug=tenant_slug)
    if not tenant_operational(tenant):
        from django.http import Http404
        raise Http404("Tenant nao encontrado ou inativo.")
    return tenant


def index(request, tenant_slug):
    """Renderiza a pagina de link de pagamento para um tenant especifico."""
    tenant = _check_tenant_operational(tenant_slug)
    sellers = Seller.objects.filter(tenant=tenant, is_active=True)
    setup_needed = not sellers.exists()
    return render(request, "orders/index.html", {
        "sellers": sellers,
        "tenant": tenant,
        "setup_needed": setup_needed,
    })


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def create_link(request, tenant_slug):
    tenant = _check_tenant_operational(tenant_slug)

    if request.method == "POST":
        link_name = request.POST.get("linkName")
        link_value = request.POST.get("linkValue")
        installments = request.POST.get("installments")
        vendedor_uuid = request.POST.get("drop_vendedor")

        if not all([link_name, link_value, installments, vendedor_uuid]):
            messages.error(request, "Todos os campos são obrigatórios.")
            return redirect("orders:index", tenant_slug=tenant_slug)

        try:
            from decimal import Decimal, ROUND_HALF_UP
            valor_str = link_value.replace("R$", "").replace(",", ".").strip()
            total_amount = int(
                Decimal(valor_str).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100
            )
            if total_amount < 100:
                messages.error(request, "Valor minimo e R$ 1,00.")
                return redirect("orders:index", tenant_slug=tenant_slug)

            session_data = request.session.get("generated_link_data", {})
            if session_data.get("link_name") == link_name and session_data.get("link_value") == link_value and session_data.get("vendedor") == vendedor_uuid:
                messages.info(request, "O link ja foi gerado com os mesmos dados.")
                return redirect("orders:index", tenant_slug=tenant_slug)

            request.session.pop("generated_link", None)

            try:
                seller = Seller.objects.get(uuid=vendedor_uuid, tenant=tenant)
            except Seller.DoesNotExist:
                messages.error(request, "Vendedor nao encontrado.")
                return redirect("orders:index", tenant_slug=tenant_slug)

            order, link_url = create_payment_link(
                tenant=tenant,
                seller=seller,
                customer_name=link_name,
                amount_cents=total_amount,
                installments=int(installments),
            )

            request.session["generated_link"] = link_url
            request.session["generated_link_data"] = {
                "link_name": link_name,
                "link_value": link_value,
                "vendedor": vendedor_uuid
            }
            messages.success(request, "Link gerado com sucesso!")

            return redirect("orders:index", tenant_slug=tenant_slug)

        except ValueError as e:
            messages.error(request, str(e))
            return redirect("orders:index", tenant_slug=tenant_slug)
        except Exception as e:
            logger.exception("Erro inesperado ao criar link de pagamento")
            messages.error(request, "Ocorreu um erro inesperado. Tente novamente.")
            return redirect("orders:index", tenant_slug=tenant_slug)

    return HttpResponse("Erro: Metodo nao suportado.")


def payment_success(request, order_uuid):
    order = get_object_or_404(Order, uuid=order_uuid)
    completed = order.status == Order.Status.COMPLETED
    return render(request, "orders/payment_success.html", {
        "order": order,
        "completed": completed,
    })
