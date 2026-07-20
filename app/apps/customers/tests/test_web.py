import uuid

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.customers.models import CustomerActivity
from app.apps.customers.services import resolve_customer


@override_settings(CUSTOMER_LEDGER_UI_ENABLED=True)
class CustomerWebTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Customer Web Tenant')
        self.other_tenant = Tenant.objects.create(company_name='Other Web Tenant')
        self.manager = User.objects.create_user(
            username='customer-web-manager', tenant=self.tenant,
            role=User.Role.MANAGER,
        )
        self.financial = User.objects.create_user(
            username='customer-web-financial', tenant=self.tenant,
            role=User.Role.FINANCEIRO,
        )
        self.seller = User.objects.create_user(
            username='customer-web-seller', tenant=self.tenant,
            role=User.Role.SELLER,
        )
        self.customer = self._customer(
            self.tenant, 'Maria Silva', '52998224725', 'maria@example.com'
        )
        self.external = self._customer(
            self.other_tenant, 'Cliente Externo', '11222333000181', ''
        )
        self._activity(self.customer, 'PENDENTE', 15000, 'Ana')
        self._activity(self.customer, 'PAGO', 25000, 'Ana')

    @staticmethod
    def _customer(tenant, name, document, email):
        return resolve_customer(
            tenant, name=name, email=email, phone='11988887777',
            document=document,
            document_type='CPF' if len(document) == 11 else 'CNPJ',
            source='BOLETO', source_uuid=uuid.uuid4(),
        )

    def _activity(self, customer, status, amount, seller_name):
        return CustomerActivity.objects.create(
            tenant=customer.tenant, customer=customer, source='BOLETO',
            source_uuid=uuid.uuid4(), seller_name=seller_name,
            amount_cents=amount, status=status, occurred_at=timezone.now(),
        )

    def test_manager_list_shows_masked_identity_and_financial_summary(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse('dashboard:customer_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Maria Silva')
        self.assertContains(response, '***.***.***-25')
        self.assertNotContains(response, '52998224725')
        row = response.context['rows'][0]
        self.assertEqual(row['customer'].charge_count, 2)
        self.assertEqual(row['customer'].total_charged_cents, 40000)
        self.assertEqual(row['customer'].total_paid_cents, 25000)
        self.assertEqual(row['customer'].total_open_cents, 15000)

    def test_filters_do_not_multiply_aggregated_values(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse('dashboard:customer_list'), {
            'seller': 'Ana', 'state': 'open',
        })

        row = response.context['rows'][0]
        self.assertEqual(row['customer'].charge_count, 2)
        self.assertEqual(row['customer'].total_charged_cents, 40000)

    def test_detail_is_tenant_scoped(self):
        self.client.force_login(self.manager)

        own = self.client.get(reverse(
            'dashboard:customer_detail', args=[self.customer.uuid]
        ))
        external = self.client.get(reverse(
            'dashboard:customer_detail', args=[self.external.uuid]
        ))

        self.assertEqual(own.status_code, 200)
        self.assertEqual(external.status_code, 404)
        self.assertEqual(len(own.context['activities']), 2)

    def test_financial_can_consult_but_seller_cannot(self):
        url = reverse('dashboard:customer_list')
        self.client.force_login(self.financial)
        self.assertEqual(self.client.get(url).status_code, 200)

        self.client.force_login(self.seller)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:home'))

    @override_settings(CUSTOMER_LEDGER_UI_ENABLED=False)
    def test_feature_flag_keeps_screen_unavailable(self):
        self.client.force_login(self.manager)

        response = self.client.get(reverse('dashboard:customer_list'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:home'))
