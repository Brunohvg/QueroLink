import uuid

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from app.apps.accounts.models import Tenant, User
from app.apps.customers.models import Customer, CustomerActivity
from app.apps.customers.services import resolve_customer


class CustomerApiTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Customer API Tenant')
        self.other_tenant = Tenant.objects.create(company_name='Other API Tenant')
        self.manager = User.objects.create_user(
            username='customer-manager', tenant=self.tenant, role=User.Role.MANAGER
        )
        self.admin = User.objects.create_user(
            username='customer-admin', tenant=self.tenant, role=User.Role.ADMIN
        )
        self.seller = User.objects.create_user(
            username='customer-seller', tenant=self.tenant, role=User.Role.SELLER
        )
        self.customer = self.make_customer(self.tenant, 'Maria Silva')
        self.external = self.make_customer(self.other_tenant, 'External Customer')
        CustomerActivity.objects.create(
            tenant=self.tenant,
            customer=self.customer,
            source='BOLETO',
            source_uuid=uuid.uuid4(),
            seller_name='Seller',
            amount_cents=15000,
            status='PAGO',
            occurred_at=self.customer.created_at,
        )

    @staticmethod
    def make_customer(tenant, name, **overrides):
        data = {
            'name': name,
            'email': 'maria@example.com',
            'phone': '11988887777',
            'document': '52998224725',
            'document_type': 'CPF',
            'source': 'BOLETO',
            'source_uuid': uuid.uuid4(),
        }
        data.update(overrides)
        return resolve_customer(tenant, **data)

    def test_queryset_is_tenant_scoped(self):
        self.client.force_authenticate(self.manager)

        response = self.client.get(reverse('customer-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item['uuid'] for item in response.data['results']}
        self.assertIn(str(self.customer.uuid), ids)
        self.assertNotIn(str(self.external.uuid), ids)
        self.assertEqual(
            self.client.get(
                reverse('customer-detail', args=[self.external.uuid])
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_seller_has_no_access(self):
        self.client.force_authenticate(self.seller)

        response = self.client.get(reverse('customer-list'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_is_paginated(self):
        for index in range(26):
            self.make_customer(
                self.tenant,
                f'Customer {index}',
                email='',
                phone='',
                document='',
                document_type='',
            )
        self.client.force_authenticate(self.admin)

        response = self.client.get(reverse('customer-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 27)
        self.assertEqual(len(response.data['results']), 25)
        self.assertIsNotNone(response.data['next'])

    def test_sensitive_data_is_masked_and_hashes_are_absent(self):
        self.client.force_authenticate(self.manager)

        response = self.client.get(
            reverse('customer-detail', args=[self.customer.uuid])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['document_masked'], '***.***.***-25')
        self.assertEqual(response.data['phone_masked'], '(11) *****-7777')
        self.assertEqual(response.data['email_masked'], 'm***@example.com')
        serialized = str(response.data)
        self.assertNotIn('52998224725', serialized)
        self.assertNotIn('11988887777', serialized)
        self.assertNotIn('document_hash', response.data)
        self.assertNotIn('email_hash', response.data)
        self.assertNotIn('phone_hash', response.data)

    def test_filters_by_identity_and_activity(self):
        self.client.force_authenticate(self.manager)

        response = self.client.get(reverse('customer-list'), {
            'name': 'Maria Silva',
            'document': '529.982.247-25',
            'phone': '(11) 98888-7777',
            'activity': 'PAGO',
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['uuid'], str(self.customer.uuid))

    def test_consent_update_records_source_date_and_responsible(self):
        self.client.force_authenticate(self.manager)
        url = reverse('customer-detail', args=[self.customer.uuid])

        response = self.client.patch(url, {
            'marketing_consent': Customer.ConsentStatus.GRANTED,
            'marketing_consent_source': 'atendimento_whatsapp',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.customer.refresh_from_db()
        self.assertEqual(
            self.customer.marketing_consent, Customer.ConsentStatus.GRANTED
        )
        metadata = response.data['consent_metadata']['marketing']
        self.assertEqual(metadata['source'], 'atendimento_whatsapp')
        self.assertEqual(metadata['updated_by'], self.manager.username)
        self.assertIsNotNone(metadata['updated_at'])

    def test_consent_update_requires_source(self):
        self.client.force_authenticate(self.manager)

        response = self.client.patch(
            reverse('customer-detail', args=[self.customer.uuid]),
            {'operational_consent': Customer.ConsentStatus.GRANTED},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
