import requests
import os
from decouple import config


class PagarMeOrder:
    """
    Classe para criar e gerenciar pedidos na API do Pagar.me.

    A classe permite a criação de pedidos com opções de parcelamento,
    utilizando a API do Pagar.me para processar pagamentos.

    Attributes:
        api_url (str): URL da API do Pagar.me para criação de pedidos.
        api_key (str): Chave da API carregada do ambiente.
        total_amount (int): Valor total do pedido em centavos.
        max_installments (int): Número máximo de parcelas permitidas.
        customer_name (str): Nome do cliente associado ao pedido.
    """

    def __init__(self, total_amount, max_installments, customer_name):
        """
        Inicializa a classe PagarMeOrder.

        Args:
            total_amount (int): Valor total do pedido em centavos.
            max_installments (int): Número máximo de parcelas permitidas.
            customer_name (str): Nome do cliente associado ao pedido.
        """
        self.api_url = "https://api.pagar.me/core/v5/orders"
        self.api_key = config("API_KEY_PAGAR_ME")  # Carregar a chave de API do ambiente
        self.total_amount = total_amount
        self.max_installments = max_installments
        self.customer_name = customer_name

    def generate_installments(self):
        """
        Gera uma lista de parcelas para o pedido.

        Returns:
            list: Uma lista de dicionários, cada um representando uma parcela
                  com o número da parcela e o valor correspondente.
        """
        return [{"number": i, "total": self.total_amount} for i in range(1, self.max_installments + 1)]

    def create_order(self):
        """
        Cria um pedido na API do Pagar.me e retorna a resposta.

        Returns:
            dict: Um dicionário contendo a resposta da API após a tentativa
                  de criação do pedido.
        """
        installments = self.generate_installments()

        payload = {
            "customer": {"name": self.customer_name},
            "items": [
                {
                    "amount": self.total_amount,
                    "description": "Link de pagamento da loja Lojas Bibelô",
                    "code": "1",
                    "quantity": 1
                }
            ],
            "payments": [
                {
                    "checkout": {
                        "expires_in": 1000,
                        "accepted_payment_methods": ["credit_card"],
                        "success_url": "https://www.bibelo.com.br/",
                        "customer_editable": True,
                        "billing_address_editable": False,
                        "credit_card": {
                            "installments": installments
                        }
                    },
                    "payment_method": "checkout"
                }
            ]
        }

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Basic {self.api_key}"
        }

        response = requests.post(self.api_url, json=payload, headers=headers)
        
        return response.json()

# Exemplo de uso da classe
if __name__ == "__main__":
    total_amount = 20000  # Valor em centavos (R$ 1.600,00)
    max_installments = 3
    customer_name = "Campo_Obrigatorio"

    order = PagarMeOrder(total_amount, max_installments, customer_name)
    response = order.create_order()
    print(response)