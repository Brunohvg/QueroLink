import logging
import unicodedata

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


class PagarMeError(Exception):
    pass


class PagarMeGateway:
    """
    Cliente generico para comunicacao com a API do Pagar.me
    Suporta tenant isolation (passagem de api_key do Tenant).
    """

    def __init__(self, api_key=None):
        self.api_key = api_key or getattr(settings, 'API_KEY_PAGAR_ME', None)
        if not self.api_key:
            raise PagarMeError(
                "API_KEY_PAGAR_ME nao configurada. "
                "Verifique as variaveis de ambiente."
            )
        self.api_url_links = getattr(
            settings, 'PAGARME_API_URL_LINKS',
            "https://api.pagar.me/core/v5/paymentlinks",
        )
        self.api_url_orders = getattr(
            settings, 'PAGARME_API_URL_ORDERS',
            "https://api.pagar.me/core/v5/orders",
        )
        self.timeout = getattr(
            settings, 'PAGARME_TIMEOUT', DEFAULT_TIMEOUT,
        )

    def _sanitize_text(self, text):
        nfd = unicodedata.normalize('NFD', text)
        ascii_text = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')
        return ascii_text.encode('ascii', errors='ignore').decode('ascii')

    def _get_headers(self):
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Basic {self.api_key}",
        }

    def create_payment_link(
        self, total_amount, max_installments, name,
        free_installments, interest_rate=2,
        order_code=None, success_url=None,
    ):
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
                    "operation_type": "auth_and_capture",
                },
                "accepted_payment_methods": ["credit_card"],
            },
            "cart_settings": {
                "items": [
                    {
                        "amount": total_amount,
                        "name": "Vendas",
                        "description": "Pedido de pagamento",
                        "default_quantity": 1,
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

        if success_url:
            if "flow_settings" not in payload:
                payload["flow_settings"] = {}
            payload["flow_settings"]["success_url"] = success_url

        logger.info("Pagar.me create_payment_link: order_code=%s", order_code)

        try:
            response = requests.post(
                self.api_url_links, json=payload, headers=self._get_headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.error("Pagar.me timeout on create_payment_link (order_code=%s)", order_code)
            raise PagarMeError("Timeout ao comunicar com Pagar.me. Tente novamente.")
        except requests.exceptions.RequestException as e:
            logger.error("Pagar.me request error: %s", e)
            raise PagarMeError(f"Erro na comunicacao com Pagar.me: {e}")
        except ValueError:
            logger.error("Pagar.me response is not valid JSON")
            raise PagarMeError("Resposta invalida do Pagar.me.")
