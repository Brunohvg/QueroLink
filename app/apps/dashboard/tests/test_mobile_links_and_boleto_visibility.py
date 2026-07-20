"""Testes de regressao para mobile/links/ (500 NameError) e visibilidade de boleto."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.orders.models import Order, PaymentLink
from app.apps.receivables.models import Boleto
from app.apps.sellers.models import Seller


class MobileLinksRegressionTests(TestCase):
    """Cenários que reproduzem o erro 500 por NameError (Order nao importado)."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Mobile Links Tenant',
            plan='PRO',
            receivables_enabled=True,
            pagarme_api_key='sk_test_mobile_links',
        )
        self.seller_user = User.objects.create_user(
            username='ml-seller',
            password='testpass',
            tenant=self.tenant,
            role=User.Role.SELLER,
            is_active=True,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='ML Seller',
            phone='11999990001',
        )
        self.url = reverse('dashboard:mobile_links')

    def test_mobile_links_returns_200_with_existing_order(self):
        order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Cliente Link',
            customer_phone='11911111111',
            total_amount=5000,
        )
        PaymentLink.objects.create(
            order=order,
            gateway_url='https://pay.example/link-1',
            gateway_link_id='provider-link-1',
        )
        self.client.force_login(self.seller_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Cliente Link', content)

    def test_mobile_links_returns_200_without_orders(self):
        self.client.force_login(self.seller_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_mobile_links_other_tenant_data_not_visible(self):
        other_tenant = Tenant.objects.create(
            company_name='Other Links Tenant',
            plan='PRO',
            receivables_enabled=True,
            pagarme_api_key='sk_test_other',
        )
        other_user = User.objects.create_user(
            username='other-links-seller',
            password='testpass',
            tenant=other_tenant,
            role=User.Role.SELLER,
            is_active=True,
        )
        other_seller = Seller.objects.create(
            tenant=other_tenant,
            user=other_user,
            name='Other Seller',
            phone='11999990002',
        )
        Order.objects.create(
            tenant=other_tenant,
            seller=other_seller,
            customer_name='Should Not Appear',
            total_amount=10000,
        )
        self.client.force_login(self.seller_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertNotIn('Should Not Appear', content)

    def test_mobile_links_other_seller_data_not_visible(self):
        other_seller_user = User.objects.create_user(
            username='ml-other-seller',
            password='testpass',
            tenant=self.tenant,
            role=User.Role.SELLER,
            is_active=True,
        )
        other_seller = Seller.objects.create(
            tenant=self.tenant,
            user=other_seller_user,
            name='Other ML Seller',
            phone='11999990003',
        )
        Order.objects.create(
            tenant=self.tenant,
            seller=other_seller,
            customer_name='Other Seller Order',
            total_amount=20000,
        )
        self.client.force_login(self.seller_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertNotIn('Other Seller Order', content)

    def test_mobile_links_unauthenticated_redirects(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 301))
        self.assertIn('login', response['Location'])

    def test_mobile_links_user_without_seller_profile_redirects(self):
        no_seller_user = User.objects.create_user(
            username='ml-no-seller',
            password='testpass',
            tenant=self.tenant,
            role=User.Role.SELLER,
            is_active=True,
        )
        self.client.force_login(no_seller_user)
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 301))
        self.assertIn('mobile', response['Location'])


class DesktopBoletoVisibilityTests(TestCase):
    """Testes de visibilidade da opcao Gerar boleto no desktop."""

    def setUp(self):
        self.client = __import__('django.test').test.Client()

    def _make_tenant(self, **kwargs):
        defaults = {
            'company_name': 'Boleto Visible',
            'plan': 'PRO',
            'receivables_enabled': True,
            'pagarme_api_key': 'sk_test_boleto_visibility',
        }
        defaults.update(kwargs)
        return Tenant.objects.create(**defaults)

    def _make_manager(self, tenant):
        return User.objects.create_user(
            username=f'manager-{tenant.slug}',
            password='testpass',
            tenant=tenant,
            role=User.Role.MANAGER,
            is_active=True,
        )

    def test_gerar_boleto_appears_when_capability_is_true(self):
        tenant = self._make_tenant()
        manager = self._make_manager(tenant)
        self.client.force_login(manager)
        response = self.client.get(reverse('dashboard:gestor_cobrancas'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_create_boletos'])

    def test_gerar_boleto_hidden_when_receivables_disabled(self):
        tenant = self._make_tenant(receivables_enabled=False)
        manager = self._make_manager(tenant)
        self.client.force_login(manager)
        response = self.client.get(reverse('dashboard:gestor_cobrancas'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_boletos'])

    def test_gerar_boleto_hidden_when_plan_does_not_include_boletos(self):
        tenant = self._make_tenant(plan='STARTER')
        manager = self._make_manager(tenant)
        self.client.force_login(manager)
        response = self.client.get(reverse('dashboard:gestor_cobrancas'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_boletos'])

    def test_gerar_boleto_hidden_when_pagarme_not_configured(self):
        tenant = self._make_tenant(pagarme_api_key='')
        manager = self._make_manager(tenant)
        self.client.force_login(manager)
        response = self.client.get(reverse('dashboard:gestor_cobrancas'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_boletos'])

    def test_history_visible_when_creation_blocked(self):
        tenant = self._make_tenant(pagarme_api_key='')
        manager = self._make_manager(tenant)
        seller_user = User.objects.create_user(
            username='hist-seller',
            tenant=tenant,
            role=User.Role.SELLER,
            is_active=True,
        )
        seller = Seller.objects.create(
            tenant=tenant,
            user=seller_user,
            name='Hist Seller',
            phone='11999990010',
        )
        Boleto.objects.create(
            tenant=tenant,
            seller=seller,
            created_by=manager,
            payer_name='Historical Payer',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_phone='11977777777',
            payer_zip_code='01310100',
            payer_street='Rua Hist',
            payer_number='10',
            payer_neighborhood='Centro',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=15000,
            due_date=timezone.localdate() + timezone.timedelta(days=10),
            status=Boleto.Status.PENDENTE,
            idempotency_key='hist-key-1',
        )
        self.client.force_login(manager)
        response = self.client.get(reverse('dashboard:gestor_cobrancas'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_boletos'])
        boleto_rows = [
            r for r in response.context['cobrancas_json']
            if r['type'] == 'boleto'
        ]
        self.assertEqual(len(boleto_rows), 1)
        self.assertEqual(boleto_rows[0]['customer_name'], 'Historical Payer')

    def test_manager_can_create_boleto_desktop(self):
        tenant = self._make_tenant()
        manager = self._make_manager(tenant)
        self.client.force_login(manager)
        response = self.client.get(reverse('dashboard:gestor_cobrancas'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_create_boletos'])


class MobileBoletoVisibilityTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Mobile Boleto V',
            plan='PRO',
            receivables_enabled=True,
            pagarme_api_key='sk_test_mobile_boleto_v',
        )
        self.seller_user = User.objects.create_user(
            username='mbv-seller',
            password='testpass',
            tenant=self.tenant,
            role=User.Role.SELLER,
            is_active=True,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='MBV Seller',
            phone='11999990100',
        )

    def test_seller_with_boleto_capability_sees_emitir_boleto(self):
        self.client.force_login(self.seller_user)
        response = self.client.get(reverse('dashboard:mobile_cobrancas'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_create_boletos'])

    def test_seller_without_boleto_capability_does_not_see_emitir_boleto(self):
        self.tenant.receivables_enabled = False
        self.tenant.save(update_fields=['receivables_enabled'])
        self.client.force_login(self.seller_user)
        response = self.client.get(reverse('dashboard:mobile_cobrancas'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_boletos'])

    def test_mobile_boleto_new_page_returns_200_for_allowed_seller(self):
        self.client.force_login(self.seller_user)
        response = self.client.get(reverse('dashboard:mobile_boleto_new'))
        self.assertEqual(response.status_code, 200)

    def test_mobile_boleto_new_page_redirects_when_disabled(self):
        self.tenant.receivables_enabled = False
        self.tenant.save(update_fields=['receivables_enabled'])
        self.client.force_login(self.seller_user)
        response = self.client.get(reverse('dashboard:mobile_boleto_new'))
        self.assertIn(response.status_code, (302, 301))
        self.assertIn('mobile', response['Location'])

    def test_mobile_boletos_list_returns_200(self):
        self.client.force_login(self.seller_user)
        response = self.client.get(reverse('dashboard:mobile_boletos'))
        self.assertEqual(response.status_code, 200)

    def test_mobile_boletos_list_shows_existing_boleto(self):
        boleto = Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.seller_user,
            payer_name='MBV Payer',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_phone='11977777777',
            payer_zip_code='01310100',
            payer_street='Rua MBV',
            payer_number='20',
            payer_neighborhood='Centro',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=30000,
            due_date=timezone.localdate() + timezone.timedelta(days=15),
            status=Boleto.Status.PENDENTE,
            idempotency_key='mbv-key-1',
        )
        self.client.force_login(self.seller_user)
        response = self.client.get(reverse('dashboard:mobile_boletos'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(str(boleto.uuid), content)

    def test_mobile_boletos_list_other_tenant_not_leaked(self):
        other_tenant = Tenant.objects.create(
            company_name='Other MBV',
            plan='PRO',
            receivables_enabled=True,
            pagarme_api_key='sk_test_other_mbv',
        )
        other_user = User.objects.create_user(
            username='other-mbv-seller',
            password='testpass',
            tenant=other_tenant,
            role=User.Role.SELLER,
            is_active=True,
        )
        other_seller = Seller.objects.create(
            tenant=other_tenant,
            user=other_user,
            name='Other MBV Seller',
            phone='11999990101',
        )
        Boleto.objects.create(
            tenant=other_tenant,
            seller=other_seller,
            created_by=other_user,
            payer_name='Hidden Payer',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_phone='11977777778',
            payer_zip_code='01310100',
            payer_street='Rua Hidden',
            payer_number='1',
            payer_neighborhood='Centro',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=50000,
            due_date=timezone.localdate() + timezone.timedelta(days=30),
            status=Boleto.Status.PENDENTE,
            idempotency_key='other-mbv-key',
        )
        self.client.force_login(self.seller_user)
        response = self.client.get(reverse('dashboard:mobile_boletos'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertNotIn('Hidden Payer', content)


class PagarmeWithoutWhatsAppTests(TestCase):
    """Boleto disponivel com Pagar.me conectado e WhatsApp desconectado."""

    def test_boleto_available_when_whatsapp_disconnected(self):
        tenant = Tenant.objects.create(
            company_name='PagarMe Only',
            plan='PRO',
            receivables_enabled=True,
            pagarme_api_key='sk_test_pagarme_only',
        )
        self.assertFalse(tenant.whatsapp_configured)
        self.assertTrue(tenant.pagarme_configured)

        manager = User.objects.create_user(
            username='pm-only-manager',
            password='testpass',
            tenant=tenant,
            role=User.Role.MANAGER,
            is_active=True,
        )
        self.client = __import__('django.test').test.Client()
        self.client.force_login(manager)
        response = self.client.get(reverse('dashboard:gestor_cobrancas'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_create_boletos'])

    def test_seller_boleto_available_when_whatsapp_disconnected(self):
        tenant = Tenant.objects.create(
            company_name='PagarMe Only Seller',
            plan='PRO',
            receivables_enabled=True,
            pagarme_api_key='sk_test_pagarme_seller',
        )
        self.assertFalse(tenant.whatsapp_configured)
        self.assertTrue(tenant.pagarme_configured)

        seller_user = User.objects.create_user(
            username='pm-seller-only',
            password='testpass',
            tenant=tenant,
            role=User.Role.SELLER,
            is_active=True,
        )
        Seller.objects.create(
            tenant=tenant,
            user=seller_user,
            name='PM Only Seller',
            phone='11999990200',
        )
        self.client = __import__('django.test').test.Client()
        self.client.force_login(seller_user)
        response = self.client.get(reverse('dashboard:mobile_cobrancas'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_create_boletos'])


class SecurityTests(TestCase):
    def setUp(self):
        self.client = __import__('django.test').test.Client()
        self.tenant = Tenant.objects.create(
            company_name='Security T',
            plan='PRO',
            receivables_enabled=True,
            pagarme_api_key='sk_test_security',
        )
        self.seller_a = User.objects.create_user(
            username='sec-seller-a',
            password='testpass',
            tenant=self.tenant,
            role=User.Role.SELLER,
            is_active=True,
        )
        self.seller_a_profile = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_a,
            name='Seller A',
            phone='11999990300',
        )
        self.seller_b = User.objects.create_user(
            username='sec-seller-b',
            password='testpass',
            tenant=self.tenant,
            role=User.Role.SELLER,
            is_active=True,
        )
        self.seller_b_profile = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_b,
            name='Seller B',
            phone='11999990301',
        )

    def test_seller_cannot_see_other_seller_links(self):
        Order.objects.create(
            tenant=self.tenant,
            seller=self.seller_b_profile,
            customer_name='Seller B Link',
            total_amount=10000,
        )
        self.client.force_login(self.seller_a)
        response = self.client.get(reverse('dashboard:mobile_links'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertNotIn('Seller B Link', content)

    def test_seller_cannot_see_other_seller_boletos(self):
        Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller_b_profile,
            created_by=self.seller_b,
            payer_name='Seller B Boleto',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_phone='11977777777',
            payer_zip_code='01310100',
            payer_street='Rua B',
            payer_number='1',
            payer_neighborhood='Centro',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=20000,
            due_date=timezone.localdate() + timezone.timedelta(days=7),
            status=Boleto.Status.PENDENTE,
            idempotency_key='sec-b-key',
        )
        self.client.force_login(self.seller_a)
        response = self.client.get(reverse('dashboard:mobile_boletos'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertNotIn('Seller B Boleto', content)
