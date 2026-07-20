from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from app.apps.accounts.fields import compute_hash
from app.apps.accounts.models import Tenant, User
from app.apps.customers.models import Customer
from app.apps.receivables.models import Boleto
from app.apps.receivables.throttles import CnpjLookupThrottle
from app.apps.sellers.models import Seller


class GestorBoletoViewsTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.tenant = Tenant.objects.create(
            company_name='Gestor Tenant',
            plan='PRO',
            receivables_enabled=True,
            pagarme_api_key='sk_test_receivables',
        )
        self.manager = User.objects.create_user(
            username='gestor-manager',
            tenant=self.tenant,
            role=User.Role.MANAGER,
            password='testpass',
        )
        self.seller_user = User.objects.create_user(
            username='gestor-seller',
            tenant=self.tenant,
            role=User.Role.SELLER,
            password='testpass',
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Test Seller',
            phone='11999999999',
        )
        self.boleto = Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.manager,
            payer_name='Test Payer',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='payer@test.com',
            payer_phone='11977777777',
            payer_zip_code='01310100',
            payer_street='Rua Teste',
            payer_number='100',
            payer_neighborhood='Centro',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=50000,
            due_date=timezone.localdate() + timezone.timedelta(days=30),
            status=Boleto.Status.PENDENTE,
        )

    def _login(self, username='gestor-manager', password='testpass'):
        self.client.login(username=username, password=password)

    # ── List page ────────────────────────────────────────────

    def test_list_page_loads_for_manager(self):
        self._login()
        response = self.client.get(reverse('dashboard:gestor_boletos'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, 'dashboard/gestor/boletos/list.html'
        )

    def test_list_page_redirects_seller(self):
        self._login('gestor-seller', 'testpass')
        response = self.client.get(reverse('dashboard:gestor_boletos'))
        self.assertNotEqual(response.status_code, 200)

    def test_list_page_keeps_history_when_disabled(self):
        self.tenant.receivables_enabled = False
        self.tenant.save(update_fields=['receivables_enabled'])
        self._login()
        response = self.client.get(reverse('dashboard:gestor_boletos'))
        self.assertEqual(response.status_code, 200)

    # ── Detail page ──────────────────────────────────────────

    def test_detail_page_loads(self):
        self._login()
        response = self.client.get(
            reverse(
                'dashboard:gestor_boleto_detalhe',
                args=[self.boleto.uuid],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, 'dashboard/gestor/boletos/detail.html'
        )

    def test_detail_404_for_wrong_tenant(self):
        other_tenant = Tenant.objects.create(
            company_name='Other Tenant',
            plan='PRO',
            receivables_enabled=True,
        )
        other_manager = User.objects.create_user(
            username='other-manager',
            tenant=other_tenant,
            role=User.Role.MANAGER,
            password='testpass',
        )
        self._login('other-manager', 'testpass')
        response = self.client.get(
            reverse(
                'dashboard:gestor_boleto_detalhe',
                args=[self.boleto.uuid],
            )
        )
        self.assertEqual(response.status_code, 404)

    # ── New page ─────────────────────────────────────────────

    def test_new_page_loads(self):
        self._login()
        response = self.client.get(reverse('dashboard:gestor_boleto_new'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, 'dashboard/gestor/boletos/new.html'
        )
        self.assertContains(response, 'Revise antes de emitir')
        self.assertContains(response, 'Confirmar e emitir')

    def test_mobile_new_page_requires_review_before_submit(self):
        self._login('gestor-seller', 'testpass')
        response = self.client.get(reverse('dashboard:mobile_boleto_new'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Revise antes de emitir')
        self.assertContains(response, 'if (!this.reviewing)')

    @patch('app.apps.receivables.views.lookup_cnpj')
    def test_cnpj_autocomplete_returns_creation_fields(self, mock_lookup):
        mock_lookup.return_value = {
            'payer_name': 'Empresa Teste',
            'payer_zip_code': '01310100',
            'payer_street': 'Avenida Paulista',
            'payer_neighborhood': 'Bela Vista',
            'payer_city': 'Sao Paulo',
            'payer_state': 'SP',
        }
        self._login()

        response = self.client.get(reverse(
            'dashboard:api_cnpj_lookup', args=['11222333000181'],
        ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['payer_name'], 'Empresa Teste')
        self.assertEqual(response.json()['payer_zip_code'], '01310100')

    @patch('app.apps.receivables.views.lookup_cep')
    def test_cep_autocomplete_returns_creation_fields(self, mock_lookup):
        mock_lookup.return_value = {
            'payer_zip_code': '01310100',
            'payer_street': 'Avenida Paulista',
            'payer_neighborhood': 'Bela Vista',
            'payer_city': 'Sao Paulo',
            'payer_state': 'SP',
        }
        self._login('gestor-seller', 'testpass')

        response = self.client.get(reverse(
            'dashboard:api_cep_lookup', args=['01310100'],
        ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['payer_street'], 'Avenida Paulista')
        self.assertEqual(response.json()['payer_state'], 'SP')

    def test_customer_lookup_is_exact_and_tenant_scoped(self):
        customer = Customer.objects.create(
            tenant=self.tenant,
            name='Cliente Existente',
            document='52998224725',
            document_type='CPF',
            document_hash=compute_hash('52998224725'),
            email='cliente@example.com',
            phone='11988887777',
        )
        other_tenant = Tenant.objects.create(company_name='Customer Other')
        Customer.objects.create(
            tenant=other_tenant,
            name='Outro Tenant',
            document='11222333000181',
            document_type='CNPJ',
            document_hash=compute_hash('11222333000181'),
        )
        self._login()

        found = self.client.get(reverse(
            'dashboard:api_boleto_customer_lookup', args=['52998224725'],
        ))
        isolated = self.client.get(reverse(
            'dashboard:api_boleto_customer_lookup', args=['11222333000181'],
        ))

        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()['customer_uuid'], str(customer.uuid))
        self.assertEqual(found.json()['payer_name'], 'Cliente Existente')
        self.assertEqual(isolated.json(), {'found': False})

    @patch.object(CnpjLookupThrottle, 'get_rate', return_value='1/minute')
    @patch('app.apps.receivables.views.lookup_cnpj')
    def test_cnpj_lookup_has_specific_throttle(self, mock_lookup, _rate):
        cache.clear()
        mock_lookup.return_value = {'payer_name': 'Empresa Teste'}
        self._login()
        url = reverse('dashboard:api_cnpj_lookup', args=['11222333000181'])

        first = self.client.get(url)
        second = self.client.get(url)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
