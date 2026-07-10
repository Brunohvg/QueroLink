from datetime import date
from decimal import Decimal
from unittest.mock import patch
import io

from django.test import TestCase
from django.urls import reverse
from django.core.cache import cache
from rest_framework.test import APIClient

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale, SaleChangeLog, SaleImportBatch
from app.apps.commissions.models import CommissionPeriod, SellerCommission


class SalesImportTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Empresa SI', slug='empresa-si',
            default_commission_rate=Decimal('0.05'),
        )
        self.tenant2 = Tenant.objects.create(
            company_name='Outra SI', slug='outra-si',
            default_commission_rate=Decimal('0.05'),
        )
        self.manager = User.objects.create_user(
            username='manager_si', password='pass123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='seller_si', password='pass123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller_user2 = User.objects.create_user(
            username='seller2_si', password='pass123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Carlos Silva', phone='11911111111',
            user=self.seller_user,
        )
        self.seller2 = Seller.objects.create(
            tenant=self.tenant, name='Ana Souza', phone='11922222222',
            user=self.seller_user2,
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
            start_date='2026-06-01', end_date='2026-06-30',
            status=CommissionPeriod.Status.ABERTA,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=Decimal('0.05'),
            status=SellerCommission.Status.ABERTA,
        )

    def _auth(self, user):
        client = APIClient()
        resp = client.post(reverse('api-login'), {
            'username': user.username, 'password': 'pass123',
        }, format='json')
        self.assertIn('access', resp.data)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {resp.data["access"]}')
        return client

    def _csv_content(self, lines):
        return '\n'.join(lines)

    def _upload_preview(self, client, filename, content):
        file_obj = io.BytesIO(content.encode('utf-8') if isinstance(content, str) else content)
        file_obj.name = filename
        return client.post(
            reverse('api-sale-import-preview'),
            {'file': file_obj},
            format='multipart',
        )

    def _confirm(self, client, rows, filename, file_hash, force=False):
        return client.post(reverse('api-sale-import-confirm'), {
            'rows': rows, 'filename': filename,
            'file_hash': file_hash,
            'force_reimport': force,
        }, format='json')

    def test_preview_csv_valid(self):
        csv = self._csv_content([
            'data;vendedor;valor;observacao',
            '15/06/2026;Carlos Silva;1500,00;Venda teste',
            '16/06/2026;Ana Souza;2300.50;Outra venda',
        ])
        client = self._auth(self.manager)
        resp = self._upload_preview(client, 'test.csv', csv)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['total_rows'], 2)
        self.assertEqual(resp.data['ok_count'], 2)
        self.assertFalse(resp.data['already_imported'])

    def test_preview_latin1_csv_accepted(self):
        csv = self._csv_content([
            'data;vendedor;valor;observacao',
            '15/06/2026;Carlos Silva;1500,00;Venda Importação',
            '16/06/2026;Ana Souza;2300.50;Observação com acento',
        ])
        client = self._auth(self.manager)
        resp = self._upload_preview(
            client, 'latin.csv', csv.encode('cp1252'),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['total_rows'], 2)
        self.assertEqual(resp.data['ok_count'], 2)

    def test_preview_br_date_accepted(self):
        csv = self._csv_content([
            'data;vendedor;valor',
            '20/06/2026;Carlos Silva;500,00',
        ])
        client = self._auth(self.manager)
        resp = self._upload_preview(client, 'test.csv', csv)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'][0]['sale_date'], '2026-06-20')

    def test_preview_comma_value_accepted(self):
        csv = self._csv_content([
            'data;vendedor;valor',
            '10/06/2026;Carlos Silva;1.234,56',
        ])
        client = self._auth(self.manager)
        resp = self._upload_preview(client, 'test.csv', csv)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'][0]['amount_cents'], 123456)

    def test_seller_by_exact_name(self):
        csv = self._csv_content([
            'data;vendedor;valor',
            '10/06/2026;Carlos Silva;500,00',
        ])
        client = self._auth(self.manager)
        resp = self._upload_preview(client, 'test.csv', csv)
        self.assertEqual(resp.data['results'][0]['status'], 'ok')
        self.assertEqual(resp.data['results'][0]['resolved_seller_name'], 'Carlos Silva')

    def test_seller_not_found_with_suggestions(self):
        csv = self._csv_content([
            'data;vendedor;valor',
            '10/06/2026;Pedro Alves;500,00',
        ])
        client = self._auth(self.manager)
        resp = self._upload_preview(client, 'test.csv', csv)
        self.assertEqual(resp.data['results'][0]['status'], 'error')
        self.assertIn('nao encontrado', resp.data['results'][0]['message'].lower())

    def test_seller_ambiguous_requires_selection(self):
        Seller.objects.create(
            tenant=self.tenant, name='Carlos Almeida', phone='11933333333',
            user=User.objects.create_user(
                username='carlos_a', password='x',
                role=User.Role.SELLER, tenant=self.tenant,
            ),
        )
        csv = self._csv_content([
            'data;vendedor;valor',
            '10/06/2026;Carlos;500,00',
        ])
        client = self._auth(self.manager)
        resp = self._upload_preview(client, 'test.csv', csv)
        self.assertEqual(resp.data['results'][0]['status'], 'needs_selection')
        self.assertTrue(len(resp.data['results'][0]['suggestions']) == 2)

    def test_duplicate_reported(self):
        csv = self._csv_content([
            'data;vendedor;valor',
            '10/06/2026;Carlos Silva;500,00',
        ])
        client = self._auth(self.manager)
        resp = self._upload_preview(client, 'test.csv', csv)
        # Create the sale directly
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.IMPORTADA, amount=50000,
            sale_date='2026-06-10', created_by=self.manager,
        )
        resp = self._upload_preview(client, 'test.csv', csv)
        self.assertEqual(resp.data['results'][0]['status'], 'duplicate')

    def test_closed_period_returns_error(self):
        self.sc.status = SellerCommission.Status.FECHADA
        self.sc.save()
        csv = self._csv_content([
            'data;vendedor;valor',
            '10/06/2026;Carlos Silva;500,00',
        ])
        client = self._auth(self.manager)
        resp = self._upload_preview(client, 'test.csv', csv)
        self.assertEqual(resp.data['results'][0]['status'], 'error')

    def test_confirm_creates_sales_with_import_origin(self):
        csv = self._csv_content([
            'data;vendedor;valor;observacao',
            '10/06/2026;Carlos Silva;500,00;Venda 1',
            '11/06/2026;Carlos Silva;600,00;Venda 2',
        ])
        client = self._auth(self.manager)
        preview = self._upload_preview(client, 'test.csv', csv)
        file_hash = preview.data['file_hash']

        rows = [
            {
                'row_number': r['row_number'],
                'resolved_seller_uuid': r['resolved_seller_uuid'],
                'amount_cents': r['amount_cents'],
                'sale_date': r['sale_date'],
                'notes': r['notes'],
            }
            for r in preview.data['results'] if r['status'] == 'ok'
        ]
        resp = self._confirm(client, rows, 'test.csv', file_hash)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['created'], 2)

        sales = Sale.objects.filter(origin=Sale.Origin.IMPORTADA)
        self.assertEqual(sales.count(), 2)

    def test_confirm_creates_sale_change_logs(self):
        csv = self._csv_content([
            'data;vendedor;valor',
            '10/06/2026;Carlos Silva;500,00',
        ])
        client = self._auth(self.manager)
        preview = self._upload_preview(client, 'test2.csv', csv)

        rows = [{
            'row_number': preview.data['results'][0]['row_number'],
            'resolved_seller_uuid': preview.data['results'][0]['resolved_seller_uuid'],
            'amount_cents': preview.data['results'][0]['amount_cents'],
            'sale_date': preview.data['results'][0]['sale_date'],
            'notes': '',
        }]
        self._confirm(client, rows, 'test2.csv', preview.data['file_hash'])

        logs = SaleChangeLog.objects.filter(action=SaleChangeLog.Action.CREATE_IMPORT)
        self.assertEqual(logs.count(), 1)

    def test_confirm_created_by_is_manager(self):
        csv = self._csv_content([
            'data;vendedor;valor',
            '10/06/2026;Carlos Silva;500,00',
        ])
        client = self._auth(self.manager)
        preview = self._upload_preview(client, 'test3.csv', csv)

        rows = [{
            'row_number': preview.data['results'][0]['row_number'],
            'resolved_seller_uuid': preview.data['results'][0]['resolved_seller_uuid'],
            'amount_cents': 50000,
            'sale_date': '2026-06-10',
            'notes': '',
        }]
        self._confirm(client, rows, 'test3.csv', preview.data['file_hash'])

        sale = Sale.objects.filter(origin=Sale.Origin.IMPORTADA).first()
        self.assertEqual(sale.created_by, self.manager)

    def test_501_rows_returns_400(self):
        lines = ['data;vendedor;valor']
        for i in range(501):
            lines.append(f'01/06/2026;Carlos Silva;{i+1},00')
        content = '\n'.join(lines)
        client = self._auth(self.manager)
        resp = self._upload_preview(client, 'huge.csv', content)
        self.assertEqual(resp.status_code, 400)

    def test_seller_receives_403(self):
        client = self._auth(self.seller_user)
        resp = client.post(reverse('api-sale-import-preview'), {}, format='multipart')
        self.assertEqual(resp.status_code, 403)

    def test_same_file_reimport_requires_force(self):
        csv = self._csv_content([
            'data;vendedor;valor',
            '10/06/2026;Carlos Silva;500,00',
        ])
        client = self._auth(self.manager)
        preview = self._upload_preview(client, 'dup.csv', csv)
        self.assertFalse(preview.data['already_imported'])

        rows = [{
            'row_number': 1,
            'resolved_seller_uuid': str(self.seller.uuid),
            'amount_cents': 50000,
            'sale_date': '2026-06-10',
            'notes': '',
        }]
        self._confirm(client, rows, 'dup.csv', preview.data['file_hash'])

        preview2 = self._upload_preview(client, 'dup.csv', csv)
        self.assertTrue(preview2.data['already_imported'])

        resp = self._confirm(client, rows, 'dup.csv', preview.data['file_hash'])
        self.assertEqual(resp.status_code, 409)

        resp_force = self._confirm(
            client, rows, 'dup.csv', preview.data['file_hash'], force=True,
        )
        self.assertEqual(resp_force.status_code, 200)

    def test_import_batch_created(self):
        csv = self._csv_content([
            'data;vendedor;valor',
            '10/06/2026;Carlos Silva;500,00',
        ])
        client = self._auth(self.manager)
        preview = self._upload_preview(client, 'batch.csv', csv)

        rows = [{
            'row_number': 1,
            'resolved_seller_uuid': str(self.seller.uuid),
            'amount_cents': 50000,
            'sale_date': '2026-06-10',
            'notes': '',
        }]
        self._confirm(client, rows, 'batch.csv', preview.data['file_hash'])

        batch = SaleImportBatch.objects.first()
        self.assertIsNotNone(batch)
        self.assertEqual(batch.tenant, self.tenant)
        self.assertEqual(batch.filename, 'batch.csv')
        self.assertEqual(batch.status, 'IMPORTED')
        self.assertEqual(batch.created_count, 1)

    def test_preview_xlsx_valid(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest('openpyxl nao instalado neste ambiente')

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['data', 'vendedor', 'valor', 'observacao'])
        ws.append(['15/06/2026', 'Carlos Silva', '1500,00', 'Venda teste'])
        ws.append(['16/06/2026', 'Ana Souza', '2300.50', 'Outra venda'])
        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()

        client = self._auth(self.manager)
        resp = self._upload_preview(client, 'test.xlsx', content)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['total_rows'], 2)
        self.assertEqual(resp.data['ok_count'], 2)

