import json

from django.test import TestCase
from django.urls import reverse

from app.apps.accounts.models import Tenant, User
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
