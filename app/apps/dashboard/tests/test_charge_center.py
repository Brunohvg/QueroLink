from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.dashboard.charge_center import build_charge_center
from app.apps.orders.models import Order, PaymentLink
from app.apps.receivables.models import Boleto
from app.apps.sellers.models import Seller


class ChargeCenterTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Charge Center',
            plan=Tenant.Plan.PRO,
            receivables_enabled=False,
        )
        self.manager = User.objects.create_user(
            username='charge-manager',
            password='testpass',
            tenant=self.tenant,
            role=User.Role.MANAGER,
        )
        self.seller_user = User.objects.create_user(
            username='charge-seller',
            tenant=self.tenant,
            role=User.Role.SELLER,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Charge Seller',
            phone='11999999999',
        )
        self.boleto = self._create_boleto(
            self.tenant, self.seller, self.manager, 'center-1', 'Visible Payer',
        )
        self.order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Link Payer',
            customer_phone='11966666666',
            total_amount=25000,
        )
        PaymentLink.objects.create(
            order=self.order,
            gateway_url='https://pay.example/real-link',
            gateway_link_id='provider-link-id',
        )

    @staticmethod
    def _create_boleto(tenant, seller, user, key, payer_name):
        return Boleto.objects.create(
            tenant=tenant,
            seller=seller,
            created_by=user,
            payer_name=payer_name,
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_phone='11977777777',
            payer_zip_code='01310100',
            payer_street='Rua Teste',
            payer_number='1',
            payer_neighborhood='Centro',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=10000,
            due_date=timezone.localdate() + timezone.timedelta(days=5),
            status=Boleto.Status.PENDENTE,
            idempotency_key=key,
        )

    def test_history_remains_visible_when_creation_flag_is_disabled(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('dashboard:gestor_cobrancas'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_boletos'])
        boleto_rows = [
            row for row in response.context['cobrancas_json']
            if row['type'] == 'boleto'
        ]
        self.assertEqual(len(boleto_rows), 1)
        self.assertEqual(boleto_rows[0]['customer_name'], 'Visible Payer')

        link_rows = [
            row for row in response.context['cobrancas_json']
            if row['type'] == 'link'
        ]
        self.assertEqual(len(link_rows), 1)
        self.assertEqual(link_rows[0]['link_url'], 'https://pay.example/real-link')

    def test_mobile_charge_center_renders_for_seller(self):
        self.client.force_login(self.seller_user)

        response = self.client.get(reverse('dashboard:mobile_cobrancas'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['cobrancas_json']), 2)

    def test_query_service_lists_both_aggregates_in_three_queries(self):
        with self.assertNumQueries(3):
            charges, links, boletos = build_charge_center(self.tenant)
        self.assertEqual(len(charges), 2)
        self.assertEqual(len(links), 1)
        self.assertEqual(len(boletos), 1)

    def test_charge_center_does_not_leak_other_tenant_history(self):
        other_tenant = Tenant.objects.create(
            company_name='Other Charge Center',
            plan=Tenant.Plan.PRO,
            receivables_enabled=True,
        )
        other_manager = User.objects.create_user(
            username='other-charge-manager',
            tenant=other_tenant,
            role=User.Role.MANAGER,
        )
        other_seller_user = User.objects.create_user(
            username='other-charge-seller',
            tenant=other_tenant,
            role=User.Role.SELLER,
        )
        other_seller = Seller.objects.create(
            tenant=other_tenant,
            user=other_seller_user,
            name='Other Seller',
            phone='11888888888',
        )
        self._create_boleto(
            other_tenant, other_seller, other_manager,
            'other-center-1', 'Hidden Payer',
        )

        self.client.force_login(self.manager)
        response = self.client.get(reverse('dashboard:gestor_cobrancas'))
        names = {
            row['customer_name'] for row in response.context['cobrancas_json']
        }
        self.assertIn('Visible Payer', names)
        self.assertNotIn('Hidden Payer', names)
