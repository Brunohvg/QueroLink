import logging

import mercadopago
from django.conf import settings

logger = logging.getLogger(__name__)


class MercadoPagoError(Exception):
    def __init__(self, message, operation='', status_code=None, retryable=True):
        self.operation = operation
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


def _retryable_status(status_code):
    if status_code is None:
        return True
    return status_code == 429 or status_code >= 500


class MercadoPagoGateway:
    def __init__(self):
        access_token = getattr(settings, 'MP_ACCESS_TOKEN', '')
        self._configured = bool(access_token)
        if self._configured:
            self.sdk = mercadopago.SDK(access_token)
        else:
            self.sdk = None

    def _check(self, operation='mercadopago'):
        if not self._configured:
            raise MercadoPagoError(
                "MP_ACCESS_TOKEN nao configurado. "
                "Billing nao esta disponivel no momento.",
                operation=operation,
                status_code=None,
                retryable=False,
            )

    def _raise_response_error(self, operation, result):
        status_code = result.get('status')
        response = result.get('response') or {}
        message = response.get('message') or status_code or 'erro desconhecido'
        retryable = _retryable_status(status_code)
        raise MercadoPagoError(
            f"Erro Mercado Pago em {operation}: {message}",
            operation=operation,
            status_code=status_code,
            retryable=retryable,
        )

    def create_preapproval(self, reason, external_reference, payer_email,
                           amount, frequency=1, frequency_type='months',
                           back_url=None):
        self._check('create_preapproval')
        if frequency not in (1, 12):
            raise MercadoPagoError(
                f"Frequencia invalida: {frequency}. "
                "Valores permitidos: 1 (mensal) ou 12 (anual).",
                operation='create_preapproval',
                retryable=False,
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

        try:
            result = self.sdk.preapproval().create(payload)
        except Exception as e:
            raise MercadoPagoError(
                f"Erro indeterminado no Mercado Pago em create_preapproval: {e}",
                operation='create_preapproval',
                retryable=True,
            ) from e
        if result["status"] in (200, 201):
            return result["response"]
        self._raise_response_error('create_preapproval', result)

    def get_preapproval(self, preapproval_id):
        self._check('get_preapproval')
        try:
            result = self.sdk.preapproval().get(preapproval_id)
        except Exception as e:
            raise MercadoPagoError(
                f"Erro indeterminado no Mercado Pago em get_preapproval: {e}",
                operation='get_preapproval',
                retryable=True,
            ) from e
        if result["status"] == 200:
            return result["response"]
        self._raise_response_error('get_preapproval', result)

    def cancel_preapproval(self, preapproval_id):
        self._check('cancel_preapproval')
        try:
            result = self.sdk.preapproval().update(
                preapproval_id, {"status": "cancelled"},
            )
        except Exception as e:
            raise MercadoPagoError(
                f"Erro indeterminado no Mercado Pago em cancel_preapproval: {e}",
                operation='cancel_preapproval',
                retryable=True,
            ) from e
        if result["status"] == 200:
            return result["response"]
        self._raise_response_error('cancel_preapproval', result)

    def get_payment(self, payment_id):
        self._check('get_payment')
        try:
            result = self.sdk.payment().get(payment_id)
        except Exception as e:
            raise MercadoPagoError(
                f"Erro indeterminado no Mercado Pago em get_payment: {e}",
                operation='get_payment',
                retryable=True,
            ) from e
        if result["status"] == 200:
            return result["response"]
        self._raise_response_error('get_payment', result)
