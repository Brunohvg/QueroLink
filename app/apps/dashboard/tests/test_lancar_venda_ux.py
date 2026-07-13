"""PROMPT 45 - polimento de UX da tela Lancar Venda.

Testes de regressao (estrutura do template + comportamento salvar/editar).
Nenhuma regra financeira e alterada aqui.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale

User = get_user_model()


class LancarVendaUxTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='LV Co', slug='lv-co',
            default_commission_rate=Decimal('0.01'), is_active=True,
        )
        self.seller_user = User.objects.create_user(
            username='vend_lv', role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Vend LV',
            phone='55999997777', commission_rate=Decimal('0.01'),
        )
        self.client.force_login(self.seller_user)
        self.url = '/dashboard/mobile/lancar/'

    def _html(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode('utf-8')

    # 1 - teclado numerico e atributos de compatibilidade no campo Valor
    def test_value_field_numeric_keyboard_attrs(self):
        html = self._html()
        block = html.split('id="valor_display"', 1)[1].split('>', 1)[0]
        self.assertIn('inputmode="numeric"', block)
        self.assertIn('autocomplete="off"', block)
        self.assertIn('autocorrect="off"', block)
        self.assertIn('spellcheck="false"', block)
        self.assertIn('enterkeyhint="done"', block)

    # foco seleciona/posiciona e Enter tenta submeter
    def test_focus_and_enter_handlers_present(self):
        html = self._html()
        self.assertIn('onValorFocus()', html)
        self.assertIn('submitIfValid()', html)
        self.assertIn('scrollIntoView', html)

    # 8 - somente Valor, Data e Registrar (nenhum campo novo)
    def test_only_expected_fields(self):
        html = self._html()
        # unico input com name de dado: amount_cents (hidden) + sale_date
        self.assertIn('name="amount_cents"', html)
        self.assertIn('name="sale_date"', html)
        self.assertIn('type="date"', html)
        # nao introduzimos campos extras (ex.: notes/cliente/quantidade)
        self.assertNotIn('name="notes"', html)
        self.assertNotIn('name="customer', html)
        self.assertNotIn('name="quantity"', html)

    # salvar: POST valido cria a venda
    def test_save_creates_sale(self):
        today = timezone.localdate()
        resp = self.client.post(self.url, {
            'amount_cents': '9999999', 'sale_date': today.isoformat(),
        })
        self.assertEqual(resp.status_code, 200)
        sale = Sale.objects.get(seller=self.seller, sale_date=today)
        self.assertEqual(sale.amount, 9999999)
        self.assertEqual(sale.origin, Sale.Origin.MANUAL)
        self.assertEqual(sale.status, 'ATIVA')

    # editar: GET com ?date= de venda existente pre-preenche o valor
    def test_edit_prefills_existing(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=550000, sale_date=date(2026, 7, 1),
            created_by=self.seller_user,
        )
        resp = self.client.get(self.url + '?date=2026-07-01')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['existing_sale'].amount, 550000)
        self.assertIn('value="550000"', resp.content.decode('utf-8'))

    # esquecer um dia anterior: registra venda para data passada
    def test_forgot_previous_day(self):
        past = timezone.localdate() - timedelta(days=3)
        resp = self.client.post(self.url, {
            'amount_cents': '20000', 'sale_date': past.isoformat(),
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            Sale.objects.filter(seller=self.seller, sale_date=past).exists(),
        )

    # regra financeira intacta: salvar cria exatamente 1 venda com o valor
    def test_no_financial_rule_changed(self):
        today = timezone.localdate()
        before = Sale.objects.count()
        self.client.post(self.url, {
            'amount_cents': '5000000', 'sale_date': today.isoformat(),
        })
        self.assertEqual(Sale.objects.count(), before + 1)
        sale = Sale.objects.get(seller=self.seller, sale_date=today)
        self.assertEqual(sale.amount, 5000000)
