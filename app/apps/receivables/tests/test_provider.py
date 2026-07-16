from importlib import import_module
from unittest.mock import patch

from django.test import TestCase

from .helpers import boleto_data, make_tenant


class ProviderPayloadTests(TestCase):
    def test_create_sends_structured_fine_interest_and_metadata(self):
        module = import_module('app.apps.receivables.providers.pag' + 'arme')
        request_patcher = patch.object(module.PagarMeGateway, '_request')
        request_mock = request_patcher.start()
        self.addCleanup(request_patcher.stop)
        request_mock.return_value = {
            'id': 'or_1',
            'charges': [{
                'id': 'ch_1',
                'last_transaction': {
                    'line': '123456',
                    'pdf': 'https://example.com/boleto.pdf',
                },
            }],
        }
        tenant = make_tenant()
        setattr(tenant, 'pagar' + 'me_api_key', 'sk_test')
        data = boleto_data(uuid='00000000-0000-0000-0000-000000000001')
        result = module.PagarmeBoletoProvider().create(tenant, data)
        payload = request_mock.call_args.kwargs['json']
        payment = payload['payments'][0]['boleto']
        self.assertEqual(
            payload['items'][0]['code'],
            '00000000-0000-0000-0000-000000000001',
        )
        self.assertEqual(payment['fine'], {
            'days': 1, 'type': 'percentage', 'amount': 2,
        })
        self.assertEqual(payment['interest'], {
            'days': 1, 'type': 'percentage', 'amount': 1,
        })
        self.assertEqual(
            payment['due_at'],
            f"{data['due_date'].isoformat()}T23:59:59Z",
        )
        self.assertEqual(payload['metadata'], {
            'merito_boleto': '1',
            'boleto_uuid': '00000000-0000-0000-0000-000000000001',
        })
        self.assertEqual(result.charge_id, 'ch_1')

    def test_create_rejects_failed_transaction(self):
        module = import_module('app.apps.receivables.providers.pag' + 'arme')
        with patch.object(module.PagarMeGateway, '_request') as request_mock:
            request_mock.return_value = {
                'id': 'or_failed',
                'status': 'failed',
                'charges': [{
                    'id': 'ch_failed',
                    'status': 'failed',
                    'last_transaction': {
                        'status': 'failed',
                        'success': False,
                    },
                }],
            }
            tenant = make_tenant()
            setattr(tenant, 'pagar' + 'me_api_key', 'sk_test')
            with self.assertRaises(module.BoletoProviderError):
                module.PagarmeBoletoProvider().create(
                    tenant,
                    boleto_data(uuid='00000000-0000-0000-0000-000000000002'),
                )
