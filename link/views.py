from django.shortcuts import render, redirect
from .api.pagar_me import PagarMeOrderApi
from .api.whatsapp import Whatsapp, formatar_numero
from .models import PagarMeOrder, PagarMeTransaction
from django.contrib import messages
from django.http import HttpResponse, HttpResponseNotFound
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import get_object_or_404

ENVIAR_MENSAGEM = Whatsapp()

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
        if not link_name or not link_value or not installments or not whatsapp:
            messages.error(request, "Todos os campos são obrigatórios.")
            return redirect("link:index")

        # Formatação do valor
        valor_formatado = (
            link_value.replace("R$", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
            .strip()
        )

        try:
            total_amount = int(float(valor_formatado) * 100)

            # Limpa o link anterior da sessão
            if 'generated_link' in request.session:
                del request.session['generated_link']

            order = PagarMeOrder.objects.create(
                total_amount=total_amount,
                max_installments=int(installments),
                customer_name=link_name,
                whatsapp=whatsapp,
            )

            api_order = PagarMeOrderApi(total_amount, int(installments), str(link_name))
            response = api_order.create_order()
            link = response.get('checkouts', [{}])[0].get('payment_url', '')

            # Tenta enviar a mensagem via WhatsApp
            try:
                NUMERO_ENVIO = formatar_numero(numero=whatsapp)
                ENVIAR_MENSAGEM.message_send_text(
                    NUMERO_ENVIO,
                    f'Olá, {link_name}! Seu link está disponível. Envie para seu cliente. Assim que recebermos o pagamento, avisaremos: {link}'
                )
            except Exception as sms_error:
                messages.warning(request, f"Mensagem não enviada: {str(sms_error)}")

            PagarMeTransaction.objects.create(
                order=order,
                transaction_id=response.get('id', ''),
                status=response.get('status', 'pending'),
                link=link
            )

            # Armazena o novo link gerado na sessão
            request.session['generated_link'] = link
            messages.success(request, "Link gerado com sucesso!")
            return redirect("link:index")  # Redireciona após sucesso

        except ValueError as e:
            messages.error(request, f"Erro de valor: {str(e)}")
            return redirect("link:index")
        except Exception as e:
            messages.error(request, f"Ocorreu um erro inesperado: {str(e)}")
            return redirect("link:index")

    messages.error(request, "Erro: Método não suportado.")
    return HttpResponse("Erro: Método não suportado.")  # Resposta para métodos não POST

def paid_transaction(request, transaction_id):
    """Processa a confirmação de um pagamento."""
    if request.method == "GET":
        try:
            transaction = PagarMeTransaction.objects.get(transaction_id=transaction_id)

            if transaction.status == 'pending':
                transaction.status = 'Pago'
                transaction.save()

                # Tenta enviar a mensagem de confirmação via WhatsApp
                try:
                    NUMERO_ENVIO = formatar_numero(numero=transaction.order.whatsapp)
                    valor_em_reais = transaction.order.total_amount / 100  # Converte para reais

                    ENVIAR_MENSAGEM.message_send_text(
                        NUMERO_ENVIO,
                        f'Olá! Seu pedido no valor de R${valor_em_reais:.2f} foi recebido e está marcado como PAGO. Obrigado pela sua compra!'
                    )
                except Exception as sms_error:
                    messages.warning(request, f"Mensagem de confirmação não enviada: {str(sms_error)}")
                return HttpResponse('teste já é pago.')
                #return render(request, 'link/sucess.html')
            else:
                return HttpResponse('Estado já é pago.')
            
        except ObjectDoesNotExist:
            return HttpResponseNotFound('Transação não encontrada.')
        
        except Exception as e:
            return HttpResponse(f'Ocorreu um erro: {str(e)}')
    
    return HttpResponse('Método de requisição inválido.')
