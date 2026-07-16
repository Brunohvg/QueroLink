from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch
from tempfile import TemporaryDirectory

from app.apps.sales.models import Sale

from ..models import Boleto
from .helpers import make_boleto, make_seller, make_tenant


class BoletoLaunchTests(TestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.user, self.seller = make_seller(self.tenant)
        self.client.force_login(self.user)
        self.boleto = make_boleto(self.tenant, self.seller, self.user)
        self.boleto.status = Boleto.Status.PAGO
        self.boleto.paid_amount_cents = 15700
        self.boleto.paid_at = timezone.now()
        self.boleto.save(update_fields=['status', 'paid_amount_cents', 'paid_at'])

    def test_get_prefills_paid_value_and_payment_date(self):
        response = self.client.get(
            reverse('dashboard:mobile_lancar_venda'),
            {'boleto': self.boleto.uuid},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="15700"')
        self.assertContains(response, timezone.localdate().isoformat())

    def test_launch_links_sale_and_second_attempt_is_blocked(self):
        payload = {
            'boleto_uuid': str(self.boleto.uuid),
            'amount_cents': 15700,
            'sale_date': timezone.localdate().isoformat(),
            'notes': f'Boleto {self.boleto.uuid} - Cliente Teste',
        }
        first = self.client.post(reverse('dashboard:mobile_lancar_venda'), payload)
        self.assertEqual(first.status_code, 200)
        self.boleto.refresh_from_db()
        self.assertIsNotNone(self.boleto.launched_sale_id)
        self.assertEqual(Sale.objects.filter(seller=self.seller).count(), 1)

        second = self.client.post(reverse('dashboard:mobile_lancar_venda'), payload)
        self.assertContains(second, 'Este boleto ja foi lancado como venda.')
        self.assertEqual(Sale.objects.filter(seller=self.seller).count(), 1)

    def test_starter_mobile_shows_locked_state(self):
        self.tenant.plan = 'STARTER'
        self.tenant.save(update_fields=['plan'])
        response = self.client.get(reverse('dashboard:mobile_boletos'))
        self.assertContains(response, 'Boletos disponiveis no Pro')

    def test_manager_detail_shows_gateway_ids_and_pdf(self):
        from app.apps.accounts.models import User
        from .helpers import make_user
        manager = make_user(self.tenant, 'detail-manager', User.Role.MANAGER)
        self.boleto.gateway_order_id = 'or_detail'
        self.boleto.gateway_charge_id = 'ch_detail'
        self.boleto.boleto_url = 'https://example.com/boleto.pdf'
        self.boleto.save(update_fields=[
            'gateway_order_id', 'gateway_charge_id', 'boleto_url',
        ])
        self.client.force_login(manager)
        response = self.client.get(reverse(
            'dashboard:gestor_boleto_detalhe', args=[self.boleto.uuid],
        ))
        self.assertContains(response, 'or_detail')
        self.assertContains(response, 'ch_detail')
        self.assertContains(response, 'Baixar PDF')

    def test_manager_list_filters_by_payer_name(self):
        from app.apps.accounts.models import User
        from .helpers import make_boleto, make_user
        manager = make_user(self.tenant, 'filter-manager', User.Role.MANAGER)
        make_boleto(
            self.tenant, self.seller, self.user,
            payer_name='Cliente Diferente',
            gateway_charge_id='ch_filter',
            gateway_order_id='or_filter',
        )
        self.client.force_login(manager)
        response = self.client.get(
            reverse('dashboard:gestor_boletos'), {'search': 'Diferente'},
        )
        self.assertContains(response, 'Cliente Diferente')
        self.assertNotContains(response, 'Cliente Teste')

    def test_only_same_tenant_manager_can_download_invoice(self):
        from app.apps.accounts.models import User
        from .helpers import make_user
        manager = make_user(self.tenant, 'invoice-manager', User.Role.MANAGER)
        other_tenant = make_tenant('Other invoice tenant')
        other_manager = make_user(other_tenant, 'other-invoice-manager', User.Role.MANAGER)
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.boleto.invoice_pdf.save(
                'invoice.pdf',
                SimpleUploadedFile('invoice.pdf', b'%PDF-1.4 test', 'application/pdf'),
            )
            url = reverse(
                'dashboard:gestor_boleto_nota_fiscal_download',
                args=[self.boleto.uuid, 'pdf'],
            )
            self.client.force_login(manager)
            self.assertEqual(self.client.get(url).status_code, 200)
            self.client.force_login(other_manager)
            self.assertEqual(self.client.get(url).status_code, 404)

    @patch('app.apps.receivables.tasks.send_boleto_email.delay')
    def test_manager_uploads_invoice_and_requests_email(self, delay_mock):
        from app.apps.accounts.models import User
        from .helpers import make_user
        manager = make_user(self.tenant, 'upload-manager', User.Role.MANAGER)
        self.client.force_login(manager)
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                reverse(
                    'dashboard:gestor_boleto_nota_fiscal',
                    args=[self.boleto.uuid],
                ),
                {
                    'invoice_pdf': SimpleUploadedFile(
                        'nota.pdf', b'%PDF-1.4 test', 'application/pdf',
                    ),
                    'send_email': '1',
                },
            )
            self.assertEqual(response.status_code, 302)
            self.boleto.refresh_from_db()
            self.assertTrue(self.boleto.invoice_pdf.name.endswith('.pdf'))
            delay_mock.assert_called_once_with(str(self.boleto.uuid), 'invoice', True)

    def test_invoice_rejects_file_with_fake_pdf_content(self):
        from app.apps.accounts.models import User
        from .helpers import make_user
        manager = make_user(self.tenant, 'invalid-invoice-manager', User.Role.MANAGER)
        self.client.force_login(manager)
        response = self.client.post(
            reverse(
                'dashboard:gestor_boleto_nota_fiscal', args=[self.boleto.uuid],
            ),
            {'invoice_pdf': SimpleUploadedFile(
                'nota.pdf', b'not a pdf', 'application/pdf',
            )},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'nao e um PDF valido', status_code=400)

    @patch('app.apps.freight.services.ViaCepClient.get_cep_info')
    def test_cep_lookup_returns_address(self, lookup_mock):
        from app.apps.freight.services import CepInfo
        lookup_mock.return_value = CepInfo(
            cep='30110000', street='Rua Teste', neighborhood='Centro',
            city='Belo Horizonte', state='MG',
        )
        response = self.client.get(reverse(
            'dashboard:boleto_cep_lookup', args=['30110000'],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['payer_city'], 'Belo Horizonte')
