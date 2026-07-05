from django.test import TestCase, Client
from django.urls import reverse
from django.core.cache import cache
from django.core import mail
from app.apps.accounts.models import Tenant, User


class PasswordResetEmailTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

        self.tenant = Tenant.objects.create(
            company_name='Loja Reset',
            cnpj='11222333000181',
        )
        self.user = User.objects.create_user(
            username='reset@teste.com.br',
            email='reset@teste.com.br',
            password='Senha@12345678',
            role=User.Role.ADMIN,
            tenant=self.tenant,
        )

        self.url = reverse('password_reset')

    def test_password_reset_sends_email_with_both_parts(self):
        response = self.client.post(self.url, {'email': 'reset@teste.com.br'})
        self.assertEqual(response.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)

        message = mail.outbox[0]

        self.assertEqual(
            message.subject,
            'Redefinicao de senha - Merito by Vidalys',
        )

        self.assertIn('Ola,', message.body)
        self.assertIn(
            'Recebemos um pedido para redefinir a senha da sua conta no Merito (usuario: reset@teste.com.br).',
            message.body,
        )
        self.assertNotIn('<html', message.body.lower())

        self.assertGreater(len(message.alternatives), 0)
        html_content, content_type = message.alternatives[0]
        self.assertEqual(content_type, 'text/html')

        self.assertIn('Redefinir minha senha', html_content)
        self.assertIn('/dashboard/redefinir-senha/', html_content)
        self.assertIn('M&eacute;rito by Vidalys', html_content)
