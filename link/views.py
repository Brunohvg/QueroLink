from django.shortcuts import render, redirect, get_object_or_404
from .api.pagar_me import PagarMeOrderApi
from .api.whatsapp import Whatsapp, formatar_numero
from .models import PagarMeOrder, PagarMeTransaction
from django.contrib import messages
from django.http import HttpResponse, HttpResponseNotFound

ENVIAR_MENSAGEM = Whatsapp()

def formatar_valor(valor):
    """Converte valor em centavos para formato em reais."""
    return valor / 100

def index(request):
    """Renderiza a página inicial."""
    return render(request, "link/index.html")

def create_link(request):
    """Cria um link de pagamento e envia via WhatsApp."""
    if request.method == "POST":
        link_name = request.POST.get("linkName")
        link_value = request.POST.get("linkValue")
        installments = request.POST.get("installments")
        whatsapp = request.POST.get("whatsapp")

        # Validação de entrada
        if not all([link_name, link_value, installments, whatsapp]):
            messages.error(request, "Todos os campos são obrigatórios.")
            return redirect("link:index")

        try:
            valor_formatado = float(link_value.replace("R$", "").replace(",", ".").strip())
            total_amount = int(valor_formatado * 100)

            # Limpa o link anterior da sessão
            request.session.pop("generated_link", None)

            order = PagarMeOrder.objects.create(
                total_amount=total_amount,
                max_installments=int(installments),
                customer_name=link_name,
                whatsapp=whatsapp,
            )

            api_order = PagarMeOrderApi(total_amount, int(installments), link_name)
            response = api_order.create_order()
            link = response.get("checkouts", [{}])[0].get("payment_url", "")

            # Envio da mensagem via WhatsApp
            try:
                NUMERO_ENVIO = formatar_numero(numero=whatsapp)
                MESSAGE = (
                    f"Olá, {link_name}! 😊\n"
                    "Seu link está disponível. 🔗\n"
                    "Envie para seu cliente. 📤\n"
                    "Assim que recebermos o pagamento, avisaremos: 💰\n"
                    f"{link} 📩"
                )
                ENVIAR_MENSAGEM.message_send_text(NUMERO_ENVIO, MESSAGE)
            except Exception as sms_error:
                messages.warning(request, f"Mensagem não enviada: {str(sms_error)}")

            PagarMeTransaction.objects.create(
                order=order,
                transaction_id=response.get("id", ""),
                status=response.get("status", "pending"),
                link=link,
            )

            request.session["generated_link"] = link
            messages.success(request, "Link gerado com sucesso!")
            return redirect("link:index")

        except ValueError:
            messages.error(request, "Valor inválido.")
            return redirect("link:index")
        except Exception as e:
            messages.error(request, f"Ocorreu um erro inesperado: {str(e)}")
            return redirect("link:index")

    return HttpResponse("Erro: Método não suportado.")

def paid_transaction(request, transaction_id):
    """Processa a confirmação de um pagamento."""
    if request.method == "GET":
        transaction = get_object_or_404(PagarMeTransaction, transaction_id=transaction_id)

        if transaction.status == "pending":
            transaction.status = "Pago"
            transaction.save()

            try:
                NUMERO_ENVIO = formatar_numero(numero=transaction.order.whatsapp)
                valor_em_reais = formatar_valor(transaction.order.total_amount)

                ENVIAR_MENSAGEM.message_send_text(
                    NUMERO_ENVIO,
                    f"Olá! Seu pedido no valor de R${valor_em_reais:.2f} foi recebido e está marcado como PAGO. Obrigado pela sua compra! 🎉"
                )
            except Exception as sms_error:
                messages.warning(request, f"Mensagem de confirmação não enviada: {str(sms_error)}")
            return HttpResponse("Transação marcada como paga.")
        else:
            return HttpResponse("Estado já é pago.")

    return HttpResponse("Método de requisição inválido.")