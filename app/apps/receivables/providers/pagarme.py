from app.services.gateway.pagar_me import PagarMeGateway

from .base import BoletoProvider, ProviderResult


class PagarmeBoletoProvider(BoletoProvider):
    def create(self, tenant, boleto_data) -> ProviderResult:
        gateway = PagarMeGateway(api_key=tenant.pagarme_api_key)
        instructions = (boleto_data.get('instructions') or '')[:256]
        payload = {
            'code': str(boleto_data['uuid']),
            'items': [{
                'amount': boleto_data['amount_cents'],
                'description': f"Boleto - {boleto_data['payer_name']}",
                'quantity': 1,
            }],
            'customer': {
                'name': boleto_data['payer_name'],
                'email': boleto_data.get('payer_email') or None,
                'document': boleto_data['payer_document'],
                'type': 'individual' if boleto_data['payer_document_type'] == 'CPF' else 'company',
                'phones': {
                    'mobile_phone': self._phone(boleto_data['payer_phone']),
                },
                'address': self._address(boleto_data),
            },
            'payments': [{
                'payment_method': 'boleto',
                'boleto': {
                    'due_at': f"{boleto_data['due_date'].isoformat()}T23:59:59Z",
                    'instructions': instructions,
                    'fine': {
                        'days': 1,
                        'type': 'percentage',
                        'amount': 2,
                    },
                    'interest': {
                        'days': 1,
                        'type': 'percentage',
                        'amount': 1,
                    },
                },
            }],
            'metadata': {
                'merito_boleto': '1',
                'boleto_uuid': str(boleto_data['uuid']),
            },
        }
        response = gateway._request('POST', gateway.api_url_orders, json=payload)
        charges = response.get('charges') or []
        charge = charges[0] if charges else {}
        transaction = charge.get('last_transaction') or {}
        return ProviderResult(
            gateway='PAGARME',
            order_id=str(response.get('id') or ''),
            charge_id=str(charge.get('id') or ''),
            barcode=str(
                transaction.get('line')
                or transaction.get('barcode')
                or charge.get('barcode')
                or ''
            ),
            url=str(
                transaction.get('pdf')
                or transaction.get('url')
                or charge.get('invoice_url')
                or ''
            ),
            pdf_password=str(transaction.get('pdf_password') or ''),
        )

    def cancel(self, tenant, gateway_charge_id) -> bool:
        gateway = PagarMeGateway(api_key=tenant.pagarme_api_key)
        gateway.cancel_charge(gateway_charge_id)
        return True

    def match_webhook_charge(self, event_payload) -> str | None:
        if not isinstance(event_payload, dict):
            return None
        data = event_payload.get('data') or {}
        if not isinstance(data, dict):
            return None
        charge = data
        if str(event_payload.get('type') or '').startswith('order.'):
            charges = data.get('charges') or []
            charge = charges[0] if charges else {}
        order = charge.get('order') or data.get('order') or {}
        metadata = order.get('metadata') or data.get('metadata') or {}
        is_boleto = (
            str(metadata.get('merito_boleto') or '') == '1'
            or charge.get('payment_method') == 'boleto'
        )
        return str(charge.get('id') or '') if is_boleto else None

    @staticmethod
    def _phone(raw_phone):
        digits = ''.join(filter(str.isdigit, raw_phone or ''))
        return {
            'country_code': '55',
            'area_code': digits[-11:-9],
            'number': digits[-9:],
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
