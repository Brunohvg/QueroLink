import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from app.apps.accounts.models import Tenant, User
from app.apps.freight.services import FreightOption
from app.apps.sellers.models import Seller


class FreightConfigTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Loja Teste',
            slug='loja-teste',
            is_active=True,
        )
        self.admin_user = User.objects.create_user(
            username='admin', password='pass',
            role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='seller', password='pass',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Vendedor',
            phone='11999999999',
            is_active=True,
        )

    def test_save_freight_presets(self):
        self.client.login(username='admin', password='pass')
        response = self.client.post(
            reverse('dashboard:gestor_configuracoes'),
            data={
                'store_cep': '01001-000',
                'freight_presets_json': json.dumps([
                    {'name': 'Envelope', 'weight_grams': 100},
                    {'name': 'Caixa M', 'weight_grams': 1000},
                ]),
                'motoboy_enabled': '1',
                'motoboy_price_per_km_cents': '3.50',
                'motoboy_min_price_cents': '12.00',
                'motoboy_max_km': '20',
                'working_weekdays': ['0', '1', '2', '3', '4'],
                'skip_national_holidays': '1',
                'pagarme_api_key': '',
                'whatsapp_instance_id': '',
                'link_expires_in': '1200',
                'ranking_visible_to_sellers': '0',
                'accountant_email': '',
                'accountant_auto_send': '0',
            },
        )
        self.assertEqual(response.status_code, 302)
        tenant = Tenant.objects.get(pk=self.tenant.pk)
        self.assertEqual(tenant.store_cep, '01001-000')
        self.assertEqual(tenant.freight_presets, [
            {'name': 'Envelope', 'weight_grams': 100},
            {'name': 'Caixa M', 'weight_grams': 1000},
        ])
        self.assertTrue(tenant.motoboy_enabled)
        self.assertEqual(tenant.motoboy_price_per_km_cents, 350)
        self.assertEqual(tenant.motoboy_min_price_cents, 1200)
        self.assertEqual(tenant.motoboy_max_km, 20)

    def test_mobile_frete_route_renders_presets(self):
        self.client.login(username='seller', password='pass')
        response = self.client.get(reverse('dashboard:mobile_frete'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'freight-presets')
        self.assertContains(response, 'Calcular frete')

    def test_gestor_configuracoes_renders_main_setting_groups(self):
        self.client.login(username='admin', password='pass')
        response = self.client.get(reverse('dashboard:gestor_configuracoes'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Billing')
        self.assertContains(response, 'WhatsApp')
        self.assertContains(response, 'Dados da loja')
        self.assertContains(response, 'Correios/Frete')
        self.assertContains(response, 'Billing e segurança')
        self.assertContains(response, 'name="store_cep"')
        self.assertContains(response, 'name="correios_usuario"')
        self.assertContains(response, 'name="pagarme_api_key"')
        self.assertContains(response, 'name="whatsapp_instance_id"')

    def test_saving_other_section_does_not_clear_correios_credentials(self):
        self.tenant.correios_usuario = 'user-cws'
        self.tenant.correios_codigo_acesso = 'secret-cws'
        self.tenant.correios_contrato = '0012345678'
        self.tenant.correios_cartao = '0098765432'
        self.tenant.save()

        self.client.login(username='admin', password='pass')
        response = self.client.post(
            reverse('dashboard:gestor_configuracoes'),
            data={
                'pagarme_api_key': '',
                'pagarme_webhook_username': '',
                'pagarme_webhook_password': '',
                'link_expires_in': '1200',
                'pix_enabled': '1',
            },
        )

        self.assertEqual(response.status_code, 302)
        tenant = Tenant.objects.get(pk=self.tenant.pk)
        self.assertEqual(tenant.correios_usuario, 'user-cws')
        self.assertEqual(tenant.correios_codigo_acesso, 'secret-cws')
        self.assertEqual(tenant.correios_contrato, '0012345678')
        self.assertEqual(tenant.correios_cartao, '0098765432')

    def test_correios_contract_and_card_keep_leading_zeroes(self):
        self.tenant.correios_codigo_acesso = 'secret-cws'
        self.tenant.save()

        self.client.login(username='admin', password='pass')
        response = self.client.post(
            reverse('dashboard:gestor_configuracoes'),
            data={
                'store_cep': '01001-000',
                'freight_adjustment_percent': '0',
                'freight_presets_json': json.dumps([
                    {'name': 'Envelope', 'weight_grams': 100},
                ]),
                'correios_usuario': 'user-cws',
                'correios_codigo_acesso': '••••••••',
                'correios_contrato': '0012345678',
                'correios_cartao': '0098765432',
            },
        )

        self.assertEqual(response.status_code, 302)
        tenant = Tenant.objects.get(pk=self.tenant.pk)
        self.assertEqual(tenant.correios_usuario, 'user-cws')
        self.assertEqual(tenant.correios_codigo_acesso, 'secret-cws')
        self.assertEqual(tenant.correios_contrato, '0012345678')
        self.assertEqual(tenant.correios_cartao, '0098765432')

    def test_new_package_is_saved_and_available_on_mobile(self):
        self.client.login(username='admin', password='pass')
        response = self.client.post(
            reverse('dashboard:gestor_configuracoes'),
            data={
                'store_cep': '01001-000',
                'freight_adjustment_percent': '0',
                'freight_presets_json': json.dumps([
                    {'name': 'Envelope', 'weight_grams': 100},
                    {'name': 'Caixa M', 'weight_grams': 1000},
                    {'name': 'Caixa GG', 'weight_grams': 5000},
                ]),
            },
        )

        self.assertEqual(response.status_code, 302)
        tenant = Tenant.objects.get(pk=self.tenant.pk)
        self.assertEqual(len(tenant.freight_presets), 3)
        self.assertEqual(tenant.freight_presets[2], {'name': 'Caixa GG', 'weight_grams': 5000})

        self.client.logout()
        self.client.login(username='seller', password='pass')
        mobile_response = self.client.get(reverse('dashboard:mobile_frete'))
        self.assertContains(mobile_response, 'Envelope')
        self.assertContains(mobile_response, 'Caixa M')
        self.assertContains(mobile_response, 'Caixa GG')

    @patch('app.apps.freight.correios_cws.CorreiosPricingClient.calculate_batch')
    @patch('app.apps.freight.correios_cws.CorreiosAuthClient.get_token')
    def test_cws_test_uses_correios_fields_after_redesign(self, mock_get_token, mock_calculate_batch):
        mock_get_token.return_value = {'token': 'jwt-fake'}
        mock_calculate_batch.return_value = [
            FreightOption('03220', 'SEDEX', 3500, 2, official=True),
        ]

        self.client.login(username='admin', password='pass')
        response = self.client.post(
            reverse('dashboard:gestor_configuracoes'),
            data={
                'test_cws': '1',
                'store_cep': '01001-000',
                'freight_adjustment_percent': '0',
                'freight_presets_json': json.dumps([
                    {'name': 'Envelope', 'weight_grams': 100},
                ]),
                'correios_usuario': 'user-cws',
                'correios_codigo_acesso': 'secret-cws',
                'correios_contrato': '0012345678',
                'correios_cartao': '0098765432',
            },
        )

        self.assertEqual(response.status_code, 302)
        tenant = Tenant.objects.get(pk=self.tenant.pk)
        self.assertEqual(tenant.correios_usuario, 'user-cws')
        self.assertEqual(tenant.correios_codigo_acesso, 'secret-cws')
        self.assertEqual(tenant.correios_contrato, '0012345678')
        self.assertEqual(tenant.correios_cartao, '0098765432')
        mock_get_token.assert_called_once()
        mock_calculate_batch.assert_called_once_with('01001000', '01001000', 300)
