from django.shortcuts import render, redirect
from .api.pagar_me import PagarMePaymentlinks
from .api.whatsapp import Whatsapp, formatar_numero
from .models import PagarMePayment
from django.contrib import messages
from django.http import HttpResponse

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
            # Converte o valor para centavos
            valor_formatado = float(link_value.replace("R$", "").replace(",", ".").strip())
            total_amount = int(valor_formatado * 100)  # Converte para centavos

            # Limpa o link anterior da sessão
            request.session.pop("generated_link", None)

            # Cria o link de pagamento via API do PagarMe
            api_order = PagarMePaymentlinks(
                total_amount, int(installments), link_name, free_installments=int(installments)
            )
            response = api_order.create_link_api()

            # Obtém o link gerado pela API
            link = response.get("url", "")

            if not link:
                messages.error(request, "Falha ao gerar o link de pagamento.")
                return redirect("link:index")

            # Envia a mensagem via WhatsApp
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

            # Cria o pagamento no banco de dados (pedido + transação em um único modelo)
            try:
                payment = PagarMePayment.objects.create(
                    total_amount=total_amount,
                    max_installments=int(installments),
                    customer_name=link_name,
                    whatsapp=whatsapp,
                    transaction_id=response.get("id", ""),
                    status=response.get("status", "pending"),
                    link=link,
                )

                # Salva o link gerado na sessão
                request.session["generated_link"] = link
                messages.success(request, "Link gerado com sucesso!")
                return redirect("link:index")
            except Exception as e:
                messages.error(request, f"Ocorreu um erro ao salvar os dados no banco de dados: {str(e)}")
                return redirect("link:index")

        except ValueError:
            messages.error(request, "Valor inválido.")
            return redirect("link:index")
        except Exception as e:
            messages.error(request, f"Ocorreu um erro inesperado: {str(e)}")
            return redirect("link:index")

    return HttpResponse("Erro: Método não suportado.")



