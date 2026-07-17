from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import Boleto
from app.apps.sellers.models import Seller


class GestorBoletoViewsTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.tenant = Tenant.objects.create(
            company_name='Gestor Tenant',
            plan='PRO',
            receivables_enabled=True,
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

    def test_list_page_redirects_when_disabled(self):
        self.tenant.receivables_enabled = False
        self.tenant.save(update_fields=['receivables_enabled'])
        self._login()
        response = self.client.get(reverse('dashboard:gestor_boletos'))
        self.assertIn(response.status_code, (302,))

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
