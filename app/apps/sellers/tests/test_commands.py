from io import StringIO
from django.test import TestCase
from django.core.management import call_command, CommandError
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller


class ResetSellerPasswordTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name="Bibelo", cnpj="55555555555555")
        self.user = User.objects.create_user(
            username="vendedor_senha", password="senha_antiga", role=User.Role.SELLER,
            tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name="Vendedor Senha", phone="4444", user=self.user,
        )

    def test_reset_password_generates_valid_password(self):
        old_password_hash = self.user.password

        out = StringIO()
        call_command(
            "reset_seller_password",
            str(self.seller.uuid),
            "--tenant", self.tenant.slug,
            stdout=out,
        )

        self.user.refresh_from_db()
        self.assertNotEqual(self.user.password, old_password_hash)

        output = out.getvalue()
        self.assertIn(self.user.username, output)

    def test_reset_password_allows_login(self):
        out = StringIO()
        call_command(
            "reset_seller_password",
            str(self.seller.uuid),
            "--tenant", self.tenant.slug,
            stdout=out,
        )

        self.user.refresh_from_db()
        self.assertNotEqual(self.user.password, 'senha_antiga')
        self.assertTrue(self.user.check_password('senha_antiga') is False)

    def test_nonexistent_seller_raises_error(self):
        with self.assertRaises(CommandError):
            call_command(
                "reset_seller_password",
                "00000000-0000-0000-0000-000000000000",
                "--tenant", self.tenant.slug,
            )

    def test_reset_password_output_contains_username(self):
        out = StringIO()
        call_command(
            "reset_seller_password",
            str(self.seller.uuid),
            "--tenant", self.tenant.slug,
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn(self.user.username, output)

    def test_wrong_tenant_raises_error(self):
        with self.assertRaises(CommandError):
            call_command(
                "reset_seller_password",
                str(self.seller.uuid),
                "--tenant", "nonexistent-tenant",
            )
