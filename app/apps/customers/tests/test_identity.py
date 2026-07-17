from concurrent.futures import ThreadPoolExecutor
import uuid

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from app.apps.accounts.models import Tenant
from app.apps.customers.models import Customer, CustomerIdentityConflict
from app.apps.customers.services import resolve_customer


class CustomerIdentityTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Customer Tenant')

    def resolve(self, **overrides):
        data = {
            'name': 'Maria Silva',
            'email': '',
            'phone': '',
            'document': '',
            'document_type': '',
            'source': 'BOLETO',
            'source_uuid': uuid.uuid4(),
        }
        data.update(overrides)
        return resolve_customer(self.tenant, **data)

    def test_same_name_without_identifiers_does_not_merge(self):
        first = self.resolve()
        second = self.resolve()

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Customer.objects.count(), 2)

    def test_same_document_in_same_tenant_is_reused(self):
        first = self.resolve(document='529.982.247-25', document_type='CPF')
        second = self.resolve(
            name='Maria Atualizada', document='52998224725', document_type='CPF'
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Customer.objects.count(), 1)

    def test_personal_data_is_encrypted_at_rest(self):
        customer = self.resolve(
            email='maria@example.com',
            phone='11999999999',
            document='52998224725',
            document_type='CPF',
        )
        prepared_uuid = Customer._meta.pk.get_db_prep_value(
            customer.uuid, connection
        )

        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT name, email, phone, document '
                'FROM customers_customer WHERE uuid = %s',
                [prepared_uuid],
            )
            stored_values = cursor.fetchone()

        for plaintext, stored in zip(
            ('Maria Silva', 'maria@example.com', '11999999999', '52998224725'),
            stored_values,
        ):
            self.assertNotEqual(stored, plaintext)
            self.assertNotIn(plaintext, stored)

    def test_same_document_in_different_tenants_is_allowed(self):
        other_tenant = Tenant.objects.create(company_name='Other Tenant')
        first = self.resolve(document='52998224725', document_type='CPF')
        second = resolve_customer(
            other_tenant,
            name='Maria Silva',
            document='52998224725',
            document_type='CPF',
            source='BOLETO',
            source_uuid=uuid.uuid4(),
        )

        self.assertNotEqual(first.pk, second.pk)

    def test_shared_email_with_distinct_documents_does_not_merge(self):
        first = self.resolve(
            email='shared@example.com', document='52998224725', document_type='CPF'
        )
        second = self.resolve(
            email='shared@example.com', document='11222333000181', document_type='CNPJ'
        )

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Customer.objects.count(), 2)

    def test_conflicting_identifiers_are_recorded_without_merge(self):
        document_customer = self.resolve(
            email='first@example.com', document='52998224725', document_type='CPF'
        )
        email_customer = self.resolve(
            email='second@example.com', document='11222333000181', document_type='CNPJ'
        )

        selected = self.resolve(
            email='second@example.com', document='52998224725', document_type='CPF'
        )

        self.assertEqual(selected.pk, document_customer.pk)
        conflict = CustomerIdentityConflict.objects.get()
        self.assertEqual(conflict.selected_customer, document_customer)
        self.assertEqual(conflict.conflicting_customer, email_customer)
        self.assertEqual(conflict.identifier_type, 'EMAIL')

    def test_concurrent_same_document_does_not_duplicate(self):
        def create_customer(index):
            close_old_connections()
            try:
                tenant = Tenant.objects.get(pk=self.tenant.pk)
                return resolve_customer(
                    tenant,
                    name=f'Maria {index}',
                    document='52998224725',
                    document_type='CPF',
                    source='BOLETO',
                    source_uuid=uuid.uuid4(),
                ).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            customer_ids = list(pool.map(create_customer, range(2)))

        self.assertEqual(customer_ids[0], customer_ids[1])
        self.assertEqual(Customer.objects.count(), 1)
