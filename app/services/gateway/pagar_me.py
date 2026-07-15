"""Pagar.me gateway service."""

import base64
import logging
import unicodedata

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


def _normalize_api_key(key):
    """Aceita chave raw (sk_xxx) ou base64 (c2tf...) e retorna sempre raw."""
    key = (key or '').strip()
    if not key:
        return key
    if key.startswith('sk_'):
        return key
    try:
        decoded = base64.b64decode(key).decode('utf-8')
        if decoded.startswith('sk_'):
            return decoded[:-1] if decoded.endswith(':') else decoded
    except ValueError:
        pass
    return key


class PagarMeError(Exception):
    pass


class PagarMeGateway:
    """
    Cliente para API do Pagar.me com suporte a multitenancy.
    """

    def __init__(self, api_key=None):
        self.api_key = api_key
        if not self.api_key:
            raise PagarMeError(
                "Chave de API do Pagar.me nao configurada. "
                "Cada lojista deve configurar sua chave na pagina de configuracoes."
            )
        base = "https://api.pagar.me/core/v5"
        self.api_url_links = f"{base}/paymentlinks"
        self.api_url_orders = f"{base}/orders"
        self.api_url_charges = f"{base}/charges"
        self.timeout = getattr(settings, 'PAGARME_TIMEOUT', DEFAULT_TIMEOUT)

    def _sanitize_text(self, text):
        nfd = unicodedata.normalize('NFD', text)
        ascii_text = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')
        return ascii_text.encode('ascii', errors='ignore').decode('ascii')

    def _get_headers(self):
        key = _normalize_api_key(self.api_key)
        encoded = base64.b64encode(f"{key}:".encode()).decode()
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Basic {encoded}",
        }

    def _request(self, method, url, **kwargs):
        try:
            response = requests.request(
                method, url, headers=self._get_headers(),
                timeout=self.timeout, **kwargs,
            )
            if response.status_code == 204:
                return {}
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            raise PagarMeError("Timeout ao comunicar com Pagar.me. Tente novamente.")
        except requests.exceptions.RequestException as e:
            raise PagarMeError(f"Erro na comunicacao com Pagar.me: {e}")
        except ValueError:
            raise PagarMeError("Resposta invalida do Pagar.me.")

    def create_payment_link(
        self, total_amount, max_installments, name,
        free_installments, interest_rate=2,
        success_url=None, order_code=None,
        expires_in=1200, pix_enabled=True, pix_expires_in=1800,
    ):
        accepted_methods = ["credit_card"]
        if pix_enabled:
            accepted_methods.append("pix")

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
                "accepted_payment_methods": accepted_methods,
            },
            "cart_settings": {
                "items": [{
                    "amount": total_amount,
                    "name": "Vendas",
                    "description": "Pedido de pagamento",
                    "default_quantity": 1,
                }]
            },
            "name": name,
            "type": "order",
            "expires_in": expires_in,
            "max_paid_sessions": 1,
            "layout_settings": {
                "primary_color": "#1263FF",
            },
        }
        if pix_enabled:
            payload["payment_settings"]["pix_settings"] = {
                "expires_in": pix_expires_in,
            }
        if order_code:
            payload["order_code"] = order_code
        if success_url:
            payload.setdefault("flow_settings", {})["success_url"] = success_url
        return self._request("POST", self.api_url_links, json=payload)

    def get_charge(self, charge_id):
        """Fetch charge details from Pagar.me."""
        logger.info("Pagar.me get_charge: charge_id=%s", charge_id)
        return self._request("GET", f"{self.api_url_charges}/{charge_id}")

    def get_order(self, order_id):
        """Fetch order details from Pagar.me."""
        logger.info("Pagar.me get_order: order_id=%s", order_id)
        return self._request("GET", f"{self.api_url_orders}/{order_id}")

    def cancel_charge(self, charge_id):
        """Cancel/refund a charge (full refund)."""
        logger.info("Pagar.me cancel_charge: charge_id=%s", charge_id)
        return self._request("POST", f"{self.api_url_charges}/{charge_id}/cancel")

    def partial_cancel_charge(self, charge_id, amount):
        """Partial refund of a charge."""
        logger.info("Pagar.me partial_cancel: charge_id=%s amount=%d", charge_id, amount)
        return self._request(
            "POST",
            f"{self.api_url_charges}/{charge_id}/cancel",
            json={"amount": amount},
        )

    def cancel_payment_link(self, link_id):
        """Cancel a payment link on Pagar.me (PATCH, returns 204)."""
        logger.info("Pagar.me cancel_payment_link: link_id=%s", link_id)
        return self._request("PATCH", f"{self.api_url_links}/{link_id}/cancel")

    def find_order_by_code(self, code):
        """Busca order no Pagar.me pelo campo order_code.

        GET /core/v5/orders?code={code}
        Retorna o primeiro resultado de ``data`` ou None.
        """
        logger.info("Pagar.me find_order_by_code: code=%s", code)
        result = self._request("GET", f"{self.api_url_orders}?code={code}")
        data = result.get("data", [])
        return data[0] if data else None
