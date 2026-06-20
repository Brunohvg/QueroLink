from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponse
from app.services.gateway.pagar_me import PagarMeGateway
from app.apps.orders.models import Order, PaymentLink
from app.apps.payments.models import Payment
from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller
from django.db import transaction
import uuid

def formatar_valor(valor):
    """Converte valor em centavos para formato em reais."""
    return valor / 100

def index(request):
    """Renderiza a página inicial."""
    sellers = Seller.objects.filter(is_active=True)
    return render(request, "orders/index.html", {"sellers": sellers})

def create_link(request):
    if request.method == "POST":
        link_name = request.POST.get("linkName")
        link_value = request.POST.get("linkValue")
        installments = request.POST.get("installments")
        vendedor_uuid = request.POST.get("drop_vendedor")

        if not all([link_name, link_value, installments, vendedor_uuid]):
            messages.error(request, "Todos os campos são obrigatórios.")
            return redirect("orders:index")

        try:
            valor_formatado = float(link_value.replace("R$", "").replace(",", ".").strip())
            total_amount = int(valor_formatado * 100)

            session_data = request.session.get("generated_link_data", {})
            if session_data.get("link_name") == link_name and session_data.get("link_value") == link_value and session_data.get("vendedor") == vendedor_uuid:
                messages.info(request, "O link já foi gerado com os mesmos dados.")
                return redirect("orders:index")

            request.session.pop("generated_link", None)

            # Get default tenant and seller
            tenant = Tenant.objects.first()
            if not tenant:
                tenant = Tenant.objects.create(company_name="Default Tenant")
            
            try:
                seller = Seller.objects.get(uuid=vendedor_uuid)
            except Seller.DoesNotExist:
                messages.error(request, "Vendedor não encontrado.")
                return redirect("orders:index")

            with transaction.atomic():
                order = Order.objects.create(
                    tenant=tenant,
                    seller=seller,
                    customer_name=link_name,
                    total_amount=total_amount,
                    status=Order.Status.PENDING
                )

                # Gateway call
                gateway = PagarMeGateway(api_key=tenant.pagarme_api_key)
                response = gateway.create_payment_link(
                    total_amount=total_amount,
                    max_installments=int(installments),
                    name=link_name,
                    free_installments=int(installments),
                    order_code=str(order.uuid)
                )

                link_url = response.get("url", "")
                gateway_id = response.get("id", "")

                if not link_url:
                    # Em caso de falha, se quisermos reverter o Order,
                    # o transaction.atomic já garante se houver exceção, 
                    # mas como a exception não foi lançada aqui, forçamos um erro:
                    raise Exception("Falha ao gerar o link de pagamento no Pagar.me.")

            # Em vez de enviar o WhatsApp aqui de forma síncrona,
            # nós vamos registrar no banco e o Celery poderia cuidar disso.
            # (No app notifications)

                # O order já foi criado no início do bloco atomic.
                # Só precisamos criar o payment e payment_link.
                payment = Payment.objects.create(
                    order=order,
                    gateway_name='pagarme',
                    gateway_transaction_id=gateway_id,
                    status=Payment.Status.PENDING,
                    installments=int(installments)
                )

                payment_link = PaymentLink.objects.create(
                    order=order,
                    gateway_url=link_url,
                    gateway_link_id=gateway_id
                )

            request.session["generated_link"] = link_url
            request.session["generated_link_data"] = {
                "link_name": link_name,
                "link_value": link_value,
                "vendedor": vendedor_uuid
            }
            messages.success(request, "Link gerado com sucesso!")
            
            # Aqui podemos despachar a task do Celery para enviar o WhatsApp:
            # send_whatsapp_message_task.delay(order.uuid)
            
            return redirect("orders:index")

        except Exception as e:
            messages.error(request, f"Ocorreu um erro: {str(e)}")
            return redirect("orders:index")

    return HttpResponse("Erro: Método não suportado.")
