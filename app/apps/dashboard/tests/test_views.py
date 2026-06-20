from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.urls import reverse
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller


class SellerCreateViewTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name="Bibelo", cnpj="11111111111111")
        self.user = User.objects.create_user(
            username="gestor", password="gestor123", role=User.Role.MANAGER, tenant=self.tenant
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_get_seller_create_page(self):
        response = self.client.get(reverse("dashboard:seller_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cadastrar Vendedor")

    def test_create_seller_success(self):
        response = self.client.post(
            reverse("dashboard:seller_create"),
            {"name": "Maria Silva", "phone": "(31) 99999-9999"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vendedor cadastrado com sucesso")
        self.assertContains(response, "Maria Silva")

        seller = Seller.objects.get(name="Maria Silva")
        self.assertEqual(seller.tenant, self.tenant)
        self.assertIsNotNone(seller.user)
        self.assertEqual(seller.user.role, User.Role.SELLER)
        self.assertAlmostEqual(float(seller.commission_rate), float(seller.tenant.default_commission_rate))

    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_seller_created_even_when_whatsapp_fails(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.send_message.side_effect = Exception("WhatsApp API Error")
        mock_client_class.return_value = mock_client

        response = self.client.post(
            reverse("dashboard:seller_create"),
            {"name": "Jose Alves", "phone": "(31) 98888-8888"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vendedor cadastrado com sucesso")
        self.assertContains(response, "Jose Alves")
        self.assertContains(response, "falhou")

        seller = Seller.objects.get(name="Jose Alves")
        self.assertIsNotNone(seller)
        self.assertIsNotNone(seller.user)

    def test_duplicate_name_gets_unique_username(self):
        self.client.post(
            reverse("dashboard:seller_create"),
            {"name": "Maria Silva", "phone": "(31) 91111-1111"},
        )
        response = self.client.post(
            reverse("dashboard:seller_create"),
            {"name": "Maria Silva", "phone": "(31) 92222-2222"},
        )

        self.assertEqual(response.status_code, 200)
        sellers = Seller.objects.filter(name="Maria Silva")
        self.assertEqual(sellers.count(), 2)
        usernames = [s.user.username for s in sellers]
        self.assertIn("maria-silva", usernames)
        self.assertIn("maria-silva-2", usernames)

    def test_missing_name_shows_error(self):
        response = self.client.post(
            reverse("dashboard:seller_create"),
            {"phone": "(31) 99999-9999"},
        )
        self.assertContains(response, "Nome")

    def test_missing_phone_shows_error(self):
        response = self.client.post(
            reverse("dashboard:seller_create"),
            {"name": "Sem Telefone"},
        )
        self.assertContains(response, "Telefone")
