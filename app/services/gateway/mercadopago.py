import logging

import mercadopago
from django.conf import settings

logger = logging.getLogger(__name__)


class MercadoPagoError(Exception):
    pass


class MercadoPagoGateway:
    def __init__(self):
        access_token = getattr(settings, 'MP_ACCESS_TOKEN', '')
        if not access_token:
            raise MercadoPagoError(
                "MP_ACCESS_TOKEN nao configurado nas settings."
            )
        self.sdk = mercadopago.SDK(access_token)

    def create_preapproval(self, reason, external_reference, payer_email,
                           amount, frequency=1, frequency_type='months',
                           back_url=None):
        payload = {
            "reason": reason,
            "external_reference": str(external_reference),
            "payer_email": payer_email,
            "auto_recurring": {
                "frequency": frequency,
                "frequency_type": frequency_type,
                "transaction_amount": float(amount),
                "currency_id": "BRL",
            },
            "status": "pending",
        }
        if back_url:
            payload["back_url"] = back_url

        result = self.sdk.preapproval().create(payload)
        if result["status"] in (200, 201):
            return result["response"]
        raise MercadoPagoError(
            f"Erro ao criar preapproval no Mercado Pago: "
            f"{result.get('response', {}).get('message', result['status'])}"
        )

    def get_preapproval(self, preapproval_id):
        result = self.sdk.preapproval().get(preapproval_id)
        if result["status"] == 200:
            return result["response"]
        raise MercadoPagoError(
            f"Erro ao buscar preapproval {preapproval_id}: {result['status']}"
        )

    def cancel_preapproval(self, preapproval_id):
        result = self.sdk.preapproval().update(
            preapproval_id, {"status": "cancelled"},
        )
        if result["status"] == 200:
            return result["response"]
        raise MercadoPagoError(
            f"Erro ao cancelar preapproval {preapproval_id}: {result['status']}"
        )

    def get_payment(self, payment_id):
        result = self.sdk.payment().get(payment_id)
        if result["status"] == 200:
            return result["response"]
        raise MercadoPagoError(
            f"Erro ao buscar payment {payment_id}: {result['status']}"
        )
