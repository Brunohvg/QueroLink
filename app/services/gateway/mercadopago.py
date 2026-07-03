import logging

import mercadopago
from django.conf import settings

logger = logging.getLogger(__name__)


class MercadoPagoError(Exception):
    pass


class MercadoPagoGateway:
    def __init__(self):
        access_token = getattr(settings, 'MP_ACCESS_TOKEN', '')
        self._configured = bool(access_token)
        if self._configured:
            self.sdk = mercadopago.SDK(access_token)
        else:
            self.sdk = None

    def _check(self):
        if not self._configured:
            raise MercadoPagoError(
                "MP_ACCESS_TOKEN nao configurado. "
                "Billing nao esta disponivel no momento."
            )

    def create_preapproval(self, reason, external_reference, payer_email,
                           amount, frequency=1, frequency_type='months',
                           back_url=None):
        self._check()
        if frequency not in (1, 12):
            raise MercadoPagoError(
                f"Frequencia invalida: {frequency}. "
                "Valores permitidos: 1 (mensal) ou 12 (anual)."
            )
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
        self._check()
        result = self.sdk.preapproval().get(preapproval_id)
        if result["status"] == 200:
            return result["response"]
        raise MercadoPagoError(
            f"Erro ao buscar preapproval {preapproval_id}: {result['status']}"
        )

    def cancel_preapproval(self, preapproval_id):
        self._check()
        result = self.sdk.preapproval().update(
            preapproval_id, {"status": "cancelled"},
        )
        if result["status"] == 200:
            return result["response"]
        raise MercadoPagoError(
            f"Erro ao cancelar preapproval {preapproval_id}: {result['status']}"
        )

    def get_payment(self, payment_id):
        self._check()
        result = self.sdk.payment().get(payment_id)
        if result["status"] == 200:
            return result["response"]
        raise MercadoPagoError(
            f"Erro ao buscar payment {payment_id}: {result['status']}"
        )
