from datetime import datetime

from django.conf import settings

from app.services.gateway.pagar_me import (
    PagarMeError,
    PagarMeGateway,
    PagarMeTransientError,
)

from .base import (
    BoletoProvider,
    ProviderDefinitiveError,
    ProviderInconclusiveError,
    ProviderResult,
    ProviderStatus,
    ProviderTransientError,
    ProviderWebhookEvent,
)


class PagarmeBoletoProvider(BoletoProvider):
    provider_name = 'PAGARME'

    def create(self, tenant, boleto_data, idempotency_key) -> ProviderResult:
        gateway = self._gateway(tenant)
        payload = self._create_payload(boleto_data, idempotency_key)
        try:
            response = gateway.create_boleto_order(payload, idempotency_key)
        except PagarMeTransientError as exc:
            raise ProviderInconclusiveError(
                'Estado remoto inconclusivo. Aguarde a reconciliacao.'
            ) from exc
        except PagarMeError as exc:
            raise ProviderDefinitiveError('Emissao recusada pelo provider.') from exc
        return self._result_from_order(response)

    def request_cancel(self, tenant, charge_id) -> ProviderResult:
        try:
            response = self._gateway(tenant).cancel_boleto_charge(charge_id)
        except PagarMeTransientError as exc:
            raise ProviderTransientError('Cancelamento temporariamente indisponivel.') from exc
        except PagarMeError as exc:
            raise ProviderDefinitiveError('Cancelamento recusado pelo provider.') from exc
        return ProviderResult(
            provider=self.provider_name,
            order_id=str((response.get('order') or {}).get('id') or ''),
            charge_id=str(response.get('id') or charge_id),
            status=self._normalize_status(response.get('status') or 'canceled'),
        )

    def retrieve_status(
        self, tenant, *, order_id='', charge_id='', local_code=''
    ) -> ProviderResult:
        gateway = self._gateway(tenant)
        try:
            if charge_id:
                charge = gateway.get_boleto_charge(charge_id)
                return self._result_from_charge(charge)
            if order_id:
                return self._result_from_order(gateway.get_boleto_order(order_id))
            if local_code:
                order = gateway.find_order_by_code(local_code)
                if order:
                    return self._result_from_order(order)
                return ProviderResult(
                    provider=self.provider_name,
                    order_id='',
                    charge_id='',
                    status=ProviderStatus.UNKNOWN,
                )
        except PagarMeTransientError as exc:
            raise ProviderTransientError('Consulta temporariamente indisponivel.') from exc
        except PagarMeError as exc:
            raise ProviderDefinitiveError('Consulta recusada pelo provider.') from exc
        raise ProviderDefinitiveError('Informe uma referencia para consulta.')

    def parse_webhook(self, payload) -> ProviderWebhookEvent:
        if not isinstance(payload, dict):
            raise ProviderDefinitiveError('Payload de webhook invalido.')
        event_type = str(payload.get('type') or '')
        data = payload.get('data') or {}
        if not isinstance(data, dict):
            raise ProviderDefinitiveError('Payload de webhook invalido.')
        charge = data
        if event_type.startswith('order.'):
            charges = data.get('charges') or []
            charge = charges[0] if charges else {}
        metadata = data.get('metadata') or {}
        charge_metadata = charge.get('metadata') or {}
        order = charge.get('order') or {}
        order_metadata = order.get('metadata') or {}
        aggregate_uuid = (
            metadata.get('boleto_uuid')
            or charge_metadata.get('boleto_uuid')
            or order_metadata.get('boleto_uuid')
            or data.get('code')
            or order.get('code')
            or ''
        )
        paid_at = charge.get('paid_at') or data.get('paid_at')
        if isinstance(paid_at, str):
            try:
                paid_at = datetime.fromisoformat(paid_at.replace('Z', '+00:00'))
            except ValueError:
                paid_at = None
        return ProviderWebhookEvent(
            event_type=event_type,
            aggregate_uuid=str(aggregate_uuid),
            order_id=str(data.get('id') or (charge.get('order') or {}).get('id') or ''),
            charge_id=str(charge.get('id') or ''),
            status=self._normalize_status(charge.get('status') or data.get('status')),
            paid_amount_cents=charge.get('paid_amount') or charge.get('amount'),
            paid_at=paid_at,
        )

    def _create_payload(self, data, idempotency_key):
        boleto_uuid = str(data['uuid'])
        boleto_options = {
            'due_at': f"{data['due_date'].isoformat()}T23:59:59Z",
            'instructions': (data.get('instructions') or '')[:256],
        }
        if getattr(settings, 'PAGARME_BOLETO_FINE_ENABLED', False):
            boleto_options['fine'] = {
                'days': 1,
                'type': 'percentage',
                'amount': getattr(settings, 'PAGARME_BOLETO_FINE_PERCENT', 2),
            }
        if getattr(settings, 'PAGARME_BOLETO_INTEREST_ENABLED', False):
            boleto_options['interest'] = {
                'days': 1,
                'type': 'percentage',
                'amount': getattr(settings, 'PAGARME_BOLETO_INTEREST_PERCENT', 1),
            }
        return {
            'code': boleto_uuid,
            'items': [{
                'amount': data['amount_cents'],
                'code': boleto_uuid,
                'description': 'Cobranca por boleto',
                'quantity': 1,
            }],
            'customer': {
                'name': data['payer_name'],
                'email': data.get('payer_email') or None,
                'document': data['payer_document'],
                'type': (
                    'individual' if data['payer_document_type'] == 'CPF' else 'company'
                ),
                'phones': {'mobile_phone': self._phone(data['payer_phone'])},
                'address': self._address(data),
            },
            'payments': [{
                'payment_method': 'boleto',
                'boleto': boleto_options,
            }],
            'metadata': {
                'merito_boleto': '1',
                'boleto_uuid': boleto_uuid,
                'idempotency_key': str(idempotency_key),
            },
        }

    def _result_from_order(self, response):
        if not isinstance(response, dict):
            raise ProviderDefinitiveError('Resposta invalida do provider.')
        charges = response.get('charges') or []
        charge = charges[0] if charges else {}
        order_id = str(response.get('id') or '')
        charge_id = str(charge.get('id') or '')
        transaction = charge.get('last_transaction') or {}
        if not order_id or not charge_id:
            raise ProviderDefinitiveError(
                'Provider nao retornou os identificadores da cobranca.'
            )
        if (
            response.get('status') == 'failed'
            or charge.get('status') == 'failed'
            or transaction.get('success') is False
            or transaction.get('status') in ('failed', 'with_error')
        ):
            raise ProviderDefinitiveError('Provider recusou a emissao do boleto.')
        return ProviderResult(
            provider=self.provider_name,
            order_id=order_id,
            charge_id=charge_id,
            status=self._normalize_status(charge.get('status') or response.get('status')),
            barcode=str(transaction.get('line') or transaction.get('barcode') or ''),
            url=str(transaction.get('pdf') or transaction.get('url') or ''),
        )

    def _result_from_charge(self, charge):
        if not isinstance(charge, dict) or not charge.get('id'):
            raise ProviderDefinitiveError('Resposta invalida do provider.')
        transaction = charge.get('last_transaction') or {}
        return ProviderResult(
            provider=self.provider_name,
            order_id=str((charge.get('order') or {}).get('id') or ''),
            charge_id=str(charge['id']),
            status=self._normalize_status(charge.get('status')),
            barcode=str(transaction.get('line') or transaction.get('barcode') or ''),
            url=str(transaction.get('pdf') or transaction.get('url') or ''),
        )

    @staticmethod
    def _gateway(tenant):
        try:
            return PagarMeGateway(api_key=tenant.pagarme_api_key)
        except PagarMeError as exc:
            raise ProviderDefinitiveError('Provider nao configurado.') from exc

    @staticmethod
    def _phone(raw_phone):
        digits = ''.join(filter(str.isdigit, raw_phone or ''))
        return {
            'country_code': '55',
            'area_code': digits[:2],
            'number': digits[2:],
        }

    @staticmethod
    def _address(data):
        return {
            'zip_code': data['payer_zip_code'],
            'line_1': (
                f"{data['payer_number']}, {data['payer_street']}, "
                f"{data['payer_neighborhood']}"
            ),
            'line_2': data.get('payer_complement') or '',
            'city': data['payer_city'],
            'state': data['payer_state'],
            'country': 'BR',
        }

    @staticmethod
    def _normalize_status(status):
        return {
            'pending': ProviderStatus.PENDING,
            'paid': ProviderStatus.PAID,
            'overdue': ProviderStatus.OVERDUE,
            'canceled': ProviderStatus.CANCELED,
            'cancelled': ProviderStatus.CANCELED,
            'refunded': ProviderStatus.REFUNDED,
            'failed': ProviderStatus.FAILED,
        }.get(str(status or '').lower(), ProviderStatus.UNKNOWN)
