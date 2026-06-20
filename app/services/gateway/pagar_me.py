import requests
from django.conf import settings

class PagarMeGateway:
    """
    Cliente genérico para comunicação com a API do Pagar.me
    Suporta tenant isolation (passagem de api_key do Tenant).
    """
    def __init__(self, api_key=None):
        self.api_key = api_key or getattr(settings, 'API_KEY_PAGAR_ME', None)
        self.api_url_links = "https://api.pagar.me/core/v5/paymentlinks"
        self.api_url_orders = "https://api.pagar.me/core/v5/orders"

    def _get_headers(self):
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Basic {self.api_key}"
        }

    def create_payment_link(self, total_amount, max_installments, name, free_installments, interest_rate=2, order_code=None):
        payload = {
            "is_building": False,
            "payment_settings": {
                "credit_card_settings": {
                    "installments_setup": {
                        "interest_type": "simple",
                        "max_installments": max_installments,
                        "amount": total_amount,
                        "interest_rate": interest_rate,
                        "free_installments": free_installments,
                    },
                    "operation_type": "auth_and_capture"
                },
                "accepted_payment_methods": ["credit_card"]
            },
            "cart_settings": { 
                "items": [
                    {
                        "amount": total_amount,
                        "name": "Vendas",
                        "description": "Pedido de pagamento",
                        "default_quantity": 1
                    }
                ]
            },
            "name": name,
            "type": "order",
            "expires_in": 1200,
            "max_paid_sessions": 1,
        }

        if order_code:
            payload["order_code"] = order_code

        response = requests.post(self.api_url_links, json=payload, headers=self._get_headers())
        response.raise_for_status()
        return response.json()
