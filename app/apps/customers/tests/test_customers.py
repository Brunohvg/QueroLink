from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.tests.helpers import (
    make_boleto,
    make_seller,
    make_tenant,
    make_user,
)

from ..models import Customer, CustomerActivity
from ..services import record_activity, sync_customer


class CustomerServiceTests(APITestCase):
    def setUp(self):
        self.tenant = make_tenant()

    def test_sync_deduplicates_inside_tenant_by_document(self):
        first = sync_customer(
            self.tenant,
            name="Cliente A",
            document="529.982.247-25",
            document_type="CPF",
            email="a@example.com",
        )
        second = sync_customer(
            self.tenant,
            name="Cliente Atualizado",
            document="52998224725",
            document_type="CPF",
            phone="31999998888",
        )
        self.assertEqual(first.uuid, second.uuid)
        self.assertEqual(Customer.objects.count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.name, "Cliente Atualizado")

    def test_same_identity_is_isolated_by_tenant(self):
        other = make_tenant("Outra empresa")
        sync_customer(self.tenant, name="Cliente", email="same@example.com")
        sync_customer(other, name="Cliente", email="same@example.com")
        self.assertEqual(Customer.objects.count(), 2)

    def test_same_name_with_different_documents_stays_separate(self):
        sync_customer(
            self.tenant, name="Maria Silva", document="52998224725",
            document_type="CPF",
        )
        sync_customer(
            self.tenant, name="Maria Silva", document="11144477735",
            document_type="CPF",
        )
        self.assertEqual(Customer.objects.count(), 2)

    def test_activity_is_idempotent_for_same_source(self):
        customer = sync_customer(self.tenant, name="Cliente")
        source_uuid = customer.uuid
        for amount in (1000, 2000):
            record_activity(
                customer,
                source=CustomerActivity.Source.BOLETO,
                source_uuid=source_uuid,
                seller_name="Vendedor",
                amount_cents=amount,
                status="PENDENTE",
                occurred_at=timezone.now(),
            )
        self.assertEqual(customer.activities.count(), 1)
        self.assertEqual(customer.activities.get().amount_cents, 2000)

    def test_boleto_status_change_updates_customer_history(self):
        user, seller = make_seller(self.tenant, "history-seller")
        boleto = make_boleto(self.tenant, seller, user)
        from ..services import sync_boleto_customer

        customer = sync_boleto_customer(boleto)
        boleto.status = "PAGO"
        boleto.save(update_fields=["status"])
        self.assertEqual(customer.activities.get().status, "PAGO")


class CustomerAccessTests(APITestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.manager = make_user(self.tenant, "customer-manager", User.Role.MANAGER)
        self.seller_user, _seller = make_seller(self.tenant, "customer-seller")
        self.customer = sync_customer(
            self.tenant,
            name="Comprador Teste",
            email="buyer@example.com",
        )

    def test_manager_sees_customer_screen(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard:gestor_customers"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comprador Teste")

    def test_seller_has_no_screen_or_api_access(self):
        self.client.force_login(self.seller_user)
        screen = self.client.get(reverse("dashboard:gestor_customers"))
        self.assertEqual(screen.status_code, 302)
        self.client.force_authenticate(self.seller_user)
        api = self.client.get(reverse("api-customer-list"))
        self.assertEqual(api.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_cannot_see_other_tenant_customer(self):
        other = make_tenant("Tenant externo")
        external = sync_customer(other, name="Cliente Externo")
        self.client.force_authenticate(self.manager)
        response = self.client.get(reverse("api-customer-detail", args=[external.uuid]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_starter_sees_locked_screen_and_api_is_forbidden(self):
        self.tenant.plan = Tenant.Plan.STARTER
        self.tenant.save(update_fields=["plan"])
        self.client.force_login(self.manager)
        self.assertContains(
            self.client.get(reverse("dashboard:gestor_customers")),
            "disponivel no Pro",
        )
        self.client.force_authenticate(self.manager)
        self.assertEqual(
            self.client.get(reverse("api-customer-list")).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_api_list_and_detail_return_customer_data(self):
        self.client.force_authenticate(self.manager)
        list_resp = self.client.get(reverse("api-customer-list"))
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        detail_resp = self.client.get(
            reverse("api-customer-detail", args=[self.customer.uuid]),
        )
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_resp.data["name"], "Comprador Teste")
        self.assertNotIn("marketing_consent", detail_resp.data)
