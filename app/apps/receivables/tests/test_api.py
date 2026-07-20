from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import Boleto
from app.apps.sellers.models import Seller


class ReceivablesAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(
            company_name='API Tenant',
            plan='PRO',
            receivables_enabled=True,
            pagarme_api_key='sk_test_receivables',
        )
        self.manager = User.objects.create_user(
            username='api-manager',
            tenant=self.tenant,
            role=User.Role.MANAGER,
            password='testpass',
        )
        self.financial = User.objects.create_user(
            username='api-financial',
            tenant=self.tenant,
            role=User.Role.FINANCEIRO,
        )
        seller_user = User.objects.create_user(
            username='api-seller',
            tenant=self.tenant,
            role=User.Role.SELLER,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=seller_user,
            name='Seller One',
            phone='11999999999',
        )
        self.other_seller_user = User.objects.create_user(
            username='api-seller2',
            tenant=self.tenant,
            role=User.Role.SELLER,
        )
        self.other_seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.other_seller_user,
            name='Seller Two',
            phone='11988888888',
        )
        self.boleto = Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.manager,
            payer_name='Test Payer',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='payer@example.com',
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
            provider='PAGARME',
            idempotency_key='api-test-key-1',
        )
        self.other_boleto = Boleto.objects.create(
            tenant=self.tenant,
            seller=self.other_seller,
            created_by=self.manager,
            payer_name='Other Payer',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='other@example.com',
            payer_phone='11966666666',
            payer_zip_code='01310101',
            payer_street='Rua Outro',
            payer_number='200',
            payer_neighborhood='Centro',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=30000,
            due_date=timezone.localdate() + timezone.timedelta(days=30),
            status=Boleto.Status.PENDENTE,
            provider='PAGARME',
            idempotency_key='api-test-key-2',
        )

    def _login(self, user):
        self.client.force_authenticate(user=user)

    def _url(self, name, *args):
        return reverse(f'receivables:{name}', args=args)

    # ── Feature flag ─────────────────────────────────────────

    def test_feature_disabled_does_not_hide_history(self):
        self.tenant.receivables_enabled = False
        self.tenant.save(update_fields=['receivables_enabled'])
        self._login(self.manager)
        response = self.client.get(self._url('boleto-list-create'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)

    def test_provider_missing_does_not_hide_history_but_blocks_creation(self):
        self.tenant.pagarme_api_key = ''
        self.tenant.save(update_fields=['pagarme_api_key'])
        self._login(self.manager)

        history = self.client.get(self._url('boleto-list-create'))
        creation = self.client.post(
            self._url('boleto-list-create'), {}, format='json',
            HTTP_X_IDEMPOTENCY_KEY='provider-missing',
        )

        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertEqual(creation.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(creation.data['code'], 'issuance_unavailable')

    # ── List boletos ─────────────────────────────────────────

    def test_manager_can_list_all_boletos(self):
        self._login(self.manager)
        response = self.client.get(self._url('boleto-list-create'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)

    def test_seller_can_only_list_own_boletos(self):
        self._login(self.seller.user)
        response = self.client.get(self._url('boleto-list-create'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for b in response.data['results']:
            self.assertEqual(b['seller_name'], 'Seller One')

    def test_list_filters_by_status(self):
        self._login(self.manager)
        response = self.client.get(
            self._url('boleto-list-create'),
            {'status': 'PENDENTE'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)

    def test_other_tenant_cannot_access(self):
        other_tenant = Tenant.objects.create(
            company_name='Other Tenant',
            plan='PRO',
            receivables_enabled=True,
        )
        other_user = User.objects.create_user(
            username='other-manager',
            tenant=other_tenant,
            role=User.Role.MANAGER,
        )
        self._login(other_user)
        response = self.client.get(self._url('boleto-list-create'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 0)

    # ── Boleto detail ────────────────────────────────────────

    def test_boleto_detail_returns_correct_data(self):
        self._login(self.manager)
        response = self.client.get(
            self._url('boleto-detail-cancel', self.boleto.uuid)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['amount_cents'], self.boleto.amount_cents
        )
        self.assertEqual(response.data['status'], 'PENDENTE')
        self.assertIn('digitable_line', response.data)
        self.assertIn('barcode', response.data)
        self.assertIn('boleto_url', response.data)

    def test_seller_can_view_own_boleto_detail(self):
        self._login(self.seller.user)
        response = self.client.get(
            self._url('boleto-detail-cancel', self.boleto.uuid)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_seller_cannot_view_other_boleto_detail(self):
        self._login(self.seller.user)
        response = self.client.get(
            self._url('boleto-detail-cancel', self.other_boleto.uuid)
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── Boleto stats ─────────────────────────────────────────

    def test_stats_returns_aggregated_data(self):
        self._login(self.manager)
        response = self.client.get(self._url('boleto-stats'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('total_a_receber', response.data)
        self.assertIn('vencidos', response.data)
        self.assertIn('pagos_periodo', response.data)

    def test_stats_denied_for_seller(self):
        self._login(self.seller.user)
        response = self.client.get(self._url('boleto-stats'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_seller_cannot_access_financial_allocations(self):
        self._login(self.seller.user)
        response = self.client.get(self._url('allocation-list-create'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.post(
            self._url('allocation-list-create'),
            {'boleto_uuid': str(self.boleto.uuid)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_financial_can_list_allocations_but_cannot_issue_boleto(self):
        self._login(self.financial)
        response = self.client.get(self._url('allocation-list-create'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.post(
            self._url('boleto-list-create'),
            {},
            format='json',
            HTTP_X_IDEMPOTENCY_KEY='financial-cannot-create',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Cancel boleto ────────────────────────────────────────

    @patch('app.apps.receivables.api.cancel_boleto')
    def test_cancel_boleto_success(self, mock_cancel):
        mock_cancel.return_value = self.boleto
        self._login(self.manager)
        response = self.client.post(
            self._url('boleto-detail-cancel', self.boleto.uuid),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_cancel.assert_called_once()

    def test_cancel_non_pendente_boleto_fails(self):
        Boleto.objects.filter(pk=self.boleto.pk).update(
            status=Boleto.Status.CANCELADO
        )
        self.boleto.refresh_from_db()
        self._login(self.manager)
        response = self.client.post(
            self._url('boleto-detail-cancel', self.boleto.uuid),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Create boleto ────────────────────────────────────────

    @patch('app.apps.receivables.api.create_boleto')
    def test_create_boleto_success(self, mock_create):
        mock_create.return_value = self.boleto
        self._login(self.manager)
        data = {
            'seller_uuid': str(self.seller.uuid),
            'payer_name': 'New Payer',
            'payer_document': '52998224725',
            'payer_document_type': 'CPF',
            'payer_phone': '11988887777',
            'payer_zip_code': '01310100',
            'payer_street': 'Rua Nova',
            'payer_number': '50',
            'payer_neighborhood': 'Centro',
            'payer_city': 'Sao Paulo',
            'payer_state': 'SP',
            'amount_cents': 100000,
            'due_date': (
                timezone.localdate() + timezone.timedelta(days=30)
            ).isoformat(),
        }
        response = self.client.post(
            self._url('boleto-list-create'),
            data,
            format='json',
            HTTP_X_IDEMPOTENCY_KEY='test-key-123',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @patch('app.apps.receivables.api.create_boleto')
    def test_seller_cannot_select_another_seller(self, mock_create):
        self._login(self.seller.user)
        data = {
            'seller_uuid': str(self.other_seller.uuid),
            'payer_name': 'New Payer',
            'payer_document': '52998224725',
            'payer_document_type': 'CPF',
            'payer_phone': '11988887777',
            'payer_zip_code': '01310100',
            'payer_street': 'Rua Nova',
            'payer_number': '50',
            'payer_neighborhood': 'Centro',
            'payer_city': 'Sao Paulo',
            'payer_state': 'SP',
            'amount_cents': 100000,
            'due_date': (
                timezone.localdate() + timezone.timedelta(days=30)
            ).isoformat(),
        }

        response = self.client.post(
            self._url('boleto-list-create'),
            data,
            format='json',
            HTTP_X_IDEMPOTENCY_KEY='seller-authority-test',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_create.assert_not_called()

    def test_create_boleto_unauthorized_when_disabled(self):
        self.tenant.receivables_enabled = False
        self.tenant.save(update_fields=['receivables_enabled'])
        self._login(self.manager)
        response = self.client.post(
            self._url('boleto-list-create'),
            {'seller_uuid': str(self.seller.uuid)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Tenant isolation ─────────────────────────────────────

    def test_tenant_isolation_on_list(self):
        other_tenant = Tenant.objects.create(
            company_name='Isolation Tenant',
            plan='PRO',
            receivables_enabled=True,
        )
        other_user = User.objects.create_user(
            username='iso-manager',
            tenant=other_tenant,
            role=User.Role.MANAGER,
        )
        self._login(other_user)
        response = self.client.get(self._url('boleto-list-create'))
        self.assertEqual(len(response.data['results']), 0)
