import io
from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.db.models import Sum
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from app.apps.accounts.models import Tenant, User
from app.apps.audit.models import AuditLog
from app.apps.commissions.day_status import get_period_day_statuses
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.sales.models import Sale, SaleChangeLog, SaleImportBatch
from app.apps.sales.services import (
    _preview_cache_key,
    classify_import_rows,
    normalize_headers,
)
from app.apps.sellers.models import Seller, SellerDayJustification


class SalesImportTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='Empresa Import', slug='empresa-import',
            default_commission_rate=Decimal('0.05'),
        )
        self.other_tenant = Tenant.objects.create(
            company_name='Outra Import', slug='outra-import',
            default_commission_rate=Decimal('0.05'),
        )
        self.manager = User.objects.create_user(
            username='manager_import', password='pass123',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='carlos_id', password='pass123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name='Carlos Silva', phone='11911111111',
            user=self.seller_user,
        )
        self.other_seller_user = User.objects.create_user(
            username='outsider_id', password='pass123',
            role=User.Role.SELLER, tenant=self.other_tenant,
        )
        self.other_seller = Seller.objects.create(
            tenant=self.other_tenant, name='Carlos Silva', phone='11922222222',
            user=self.other_seller_user,
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
            status=CommissionPeriod.Status.ABERTA,
        )
        self.sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=Decimal('0.05'),
            status=SellerCommission.Status.ABERTA,
        )

    def _client(self, user=None):
        client = APIClient()
        client.force_authenticate(user=user or self.manager)
        return client

    def _upload(self, content, filename='import.csv', user=None):
        payload = content.encode('utf-8') if isinstance(content, str) else content
        file_obj = io.BytesIO(payload)
        file_obj.name = filename
        return self._client(user).post(
            reverse('api-sale-import-preview'), {'file': file_obj},
            format='multipart',
        )

    def _csv(self, value='500,00', seller='Carlos Silva', day='15/06/2026'):
        return f'data;vendedor;valor\n{day};{seller};{value}'

    def _confirm(self, preview, *, justifications=None, sellers=None, force=False,
                 user=None):
        return self._client(user).post(reverse('api-sale-import-confirm'), {
            'batch_uuid': preview.data['batch_uuid'],
            'force_reimport': force,
            'decisions': {
                'confirmed_justifications': justifications or [],
                'seller_choices': sellers or {},
            },
        }, format='json')

    def _preview_row(self, value='500,00', seller='Carlos Silva', day='15/06/2026'):
        response = self._upload(self._csv(value, seller, day))
        self.assertEqual(response.status_code, 200, response.data)
        return response, response.data['results'][0]

    def _xlsx(self, rows):
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        for row in rows:
            sheet.append(row)
        output = io.BytesIO()
        workbook.save(output)
        return output.getvalue()

    # 1-9: formatos e valores monetarios.
    def test_01_csv_com_venda_valida(self):
        _, row = self._preview_row()
        self.assertEqual(row['classification'], 'SALE')

    def test_02_xlsx_com_venda_valida(self):
        response = self._upload(self._xlsx([
            ['data', 'vendedor', 'valor'],
            ['15/06/2026', 'Carlos Silva', '500,00'],
        ]), 'import.xlsx')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'][0]['classification'], 'SALE')

    def test_03_csv_utf8_com_bom(self):
        response = self._upload(('\ufeff' + self._csv()).encode('utf-8'))
        self.assertEqual(response.status_code, 200)

    def test_04_valor_real_com_milhar(self):
        _, row = self._preview_row('R$ 9.436,72')
        self.assertEqual(row['normalized_amount'], 943672)

    def test_05_valor_com_virgula(self):
        _, row = self._preview_row('9436,72')
        self.assertEqual(row['normalized_amount'], 943672)

    def test_06_valor_com_ponto(self):
        _, row = self._preview_row('9436.72')
        self.assertEqual(row['normalized_amount'], 943672)

    def test_07_valor_zero_rejeitado(self):
        _, row = self._preview_row('0')
        self.assertEqual(row['classification'], 'INVALID')

    def test_08_valor_negativo_rejeitado(self):
        _, row = self._preview_row('-10,00')
        self.assertEqual(row['classification'], 'INVALID')

    def test_09_teto_financeiro_preservado(self):
        _, valid = self._preview_row('100000,00')
        _, invalid = self._preview_row('100000,01')
        self.assertEqual(valid['classification'], 'SALE')
        self.assertEqual(invalid['classification'], 'INVALID')

    # 10-19: motivos, vazio e texto desconhecido.
    def _assert_reason(self, raw, expected):
        _, row = self._preview_row(raw)
        self.assertEqual(row['classification'], 'JUSTIFICATION_SUGGESTION')
        self.assertEqual(row['normalized_reason'], expected)

    def test_10_falta(self): self._assert_reason('FALTA', 'FALTA')
    def test_11_atestado(self): self._assert_reason('ATESTADO', 'ATESTADO')
    def test_12_ferias(self): self._assert_reason('FERIAS', 'FERIAS')
    def test_13_ferias_com_acento(self): self._assert_reason('FÉRIAS', 'FERIAS')
    def test_14_folga(self): self._assert_reason('FOLGA', 'FOLGA')
    def test_15_afastamento(self): self._assert_reason('AFASTAMENTO', 'AFASTAMENTO')
    def test_16_feriado(self): self._assert_reason('FERIADO', 'FERIADO')
    def test_17_sem_expediente(self): self._assert_reason('SEM_EXPEDIENTE', 'SEM_EXPEDIENTE')

    def test_18_celula_vazia(self):
        _, row = self._preview_row('   ')
        self.assertEqual(row['classification'], 'EMPTY')

    def test_19_texto_desconhecido(self):
        _, row = self._preview_row('não veio')
        self.assertEqual(row['classification'], 'INVALID')

    # 20-30: somente leitura, criacao e efeitos operacionais/financeiros.
    def test_20_preview_nao_cria_sale(self):
        before = Sale.objects.count()
        self._preview_row()
        self.assertEqual(Sale.objects.count(), before)

    def test_21_preview_nao_cria_justificativa(self):
        before = SellerDayJustification.objects.count()
        self._preview_row('FALTA')
        self.assertEqual(SellerDayJustification.objects.count(), before)

    def test_22_preview_nao_cria_auditlog_de_dominio(self):
        before = AuditLog.objects.count()
        self._preview_row('FALTA')
        self.assertEqual(AuditLog.objects.count(), before)

    def test_23_confirmacao_cria_venda(self):
        preview, _ = self._preview_row()
        response = self._confirm(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Sale.objects.filter(origin=Sale.Origin.IMPORTADA).count(), 1)

    def test_24_confirmacao_cria_justificativa(self):
        preview, row = self._preview_row('FALTA')
        response = self._confirm(preview, justifications=[row['row_number']])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(SellerDayJustification.objects.count(), 1)

    def test_25_justificativa_nao_altera_comissao(self):
        before = (self.sc.status, self.sc.commission_rate)
        preview, row = self._preview_row('FALTA')
        self._confirm(preview, justifications=[row['row_number']])
        self.sc.refresh_from_db()
        self.assertEqual((self.sc.status, self.sc.commission_rate), before)

    def test_26_justificativa_nao_altera_ranking_financeiro(self):
        before = Sale.objects.filter(tenant=self.tenant).aggregate(total=Sum('amount'))['total']
        preview, row = self._preview_row('FALTA')
        self._confirm(preview, justifications=[row['row_number']])
        after = Sale.objects.filter(tenant=self.tenant).aggregate(total=Sum('amount'))['total']
        self.assertEqual(after, before)

    def test_27_justificativa_reduz_pendencia(self):
        before = get_period_day_statuses(
            self.tenant, self.seller, self.period, reference_date=date(2026, 6, 16),
            sc=self.sc,
        )['summary']
        preview, row = self._preview_row('FALTA')
        self._confirm(preview, justifications=[row['row_number']])
        after = get_period_day_statuses(
            self.tenant, self.seller, self.period, reference_date=date(2026, 6, 16),
            sc=self.sc,
        )['summary']
        self.assertEqual(after['justificados'], before['justificados'] + 1)
        self.assertEqual(after['pendentes'], before['pendentes'] - 1)

    def test_28_venda_aumenta_total_financeiro(self):
        preview, _ = self._preview_row('500,00')
        response = self._confirm(preview)
        self.assertEqual(response.data['total_amount'], 50000)
        self.assertEqual(Sale.objects.aggregate(total=Sum('amount'))['total'], 50000)

    def test_29_venda_cria_changelog(self):
        preview, _ = self._preview_row()
        self._confirm(preview)
        self.assertEqual(SaleChangeLog.objects.filter(
            action=SaleChangeLog.Action.CREATE_IMPORT,
        ).count(), 1)

    def test_30_justificativa_cria_auditlog(self):
        preview, row = self._preview_row('ATESTADO')
        self._confirm(preview, justifications=[row['row_number']])
        self.assertTrue(AuditLog.objects.filter(action='day_justification.created').exists())

    # 31-36: competencia por range e integridade.
    def _set_period_status(self, status):
        self.period.status = status
        self.period.save(update_fields=['status'])

    def test_31_competencia_fechada_bloqueia(self):
        self._set_period_status(CommissionPeriod.Status.FECHADA)
        _, row = self._preview_row()
        self.assertEqual(row['classification'], 'BLOCKED')

    def test_32_competencia_paga_bloqueia(self):
        self._set_period_status(CommissionPeriod.Status.PAGA)
        _, row = self._preview_row()
        self.assertEqual(row['classification'], 'BLOCKED')

    def test_33_competencia_cancelada_bloqueia(self):
        self._set_period_status(CommissionPeriod.Status.CANCELADA)
        _, row = self._preview_row()
        self.assertEqual(row['classification'], 'BLOCKED')

    def test_34_competencia_reaberta_permite(self):
        self._set_period_status(CommissionPeriod.Status.FECHADA)
        self._set_period_status(CommissionPeriod.Status.ABERTA)
        self.sc.status = SellerCommission.Status.ABERTA
        self.sc.save(update_fields=['status'])
        _, row = self._preview_row()
        self.assertEqual(row['classification'], 'SALE')

    def test_35_ausencia_de_competencia(self):
        _, row = self._preview_row(day='15/08/2026')
        self.assertEqual(row['classification'], 'BLOCKED')

    def test_36_sobreposicao_de_competencia(self):
        CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 10), end_date=date(2026, 7, 10),
            status=CommissionPeriod.Status.ABERTA,
        )
        _, row = self._preview_row()
        self.assertEqual(row['classification'], 'CONFLICT')

    # 37-42: resolucao de vendedor limitada ao tenant.
    def test_37_vendedor_por_uuid(self):
        csv = (
            'data;vendedor;seller_uuid;valor\n'
            f'15/06/2026;;{self.seller.uuid};500,00'
        )
        response = self._upload(csv)
        self.assertEqual(response.data['results'][0]['seller_uuid'], str(self.seller.uuid))

    def test_38_vendedor_por_identificador(self):
        _, row = self._preview_row(seller='carlos_id')
        self.assertEqual(row['match_type'], 'username')

    def test_39_vendedor_por_nome_exato(self):
        _, row = self._preview_row(seller='cArLoS sIlVa')
        self.assertEqual(row['seller_uuid'], str(self.seller.uuid))

    def test_40_vendedor_ambiguo(self):
        second_user = User.objects.create_user(
            username='carlos_second', role=User.Role.SELLER, tenant=self.tenant,
        )
        Seller.objects.create(
            tenant=self.tenant, name='Carlos Souza', phone='11933333333',
            user=second_user,
        )
        _, row = self._preview_row(seller='Carlos')
        self.assertEqual(row['classification'], 'CONFLICT')
        self.assertTrue(row['requires_manual_action'])

    def test_41_vendedor_inexistente(self):
        _, row = self._preview_row(seller='Inexistente')
        self.assertEqual(row['classification'], 'INVALID')

    def test_42_vendedor_de_outro_tenant(self):
        csv = (
            'data;vendedor;seller_uuid;valor\n'
            f'15/06/2026;;{self.other_seller.uuid};500,00'
        )
        response = self._upload(csv)
        self.assertIsNone(response.data['results'][0]['seller_uuid'])
        self.assertEqual(response.data['results'][0]['classification'], 'INVALID')

    # 43-49: conflitos, duplicidade e hash.
    def test_43_justificativa_com_venda_existente(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=10000, sale_date=date(2026, 6, 15), created_by=self.manager,
        )
        _, row = self._preview_row('FALTA')
        self.assertEqual(row['classification'], 'CONFLICT')

    def test_44_venda_com_justificativa_existente(self):
        SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 6, 15),
            reason='FALTA', created_by=self.manager,
        )
        _, row = self._preview_row('500,00')
        self.assertEqual(row['classification'], 'CONFLICT')

    def test_45_justificativa_duplicada(self):
        SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 6, 15),
            reason='FALTA', created_by=self.manager,
        )
        _, row = self._preview_row('FALTA')
        self.assertEqual(row['classification'], 'DUPLICATE')

    def test_46_justificativa_com_motivo_diferente(self):
        SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 6, 15),
            reason='ATESTADO', created_by=self.manager,
        )
        _, row = self._preview_row('FALTA')
        self.assertEqual(row['classification'], 'CONFLICT')

    def test_47_venda_duplicada(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.IMPORTADA,
            amount=50000, sale_date=date(2026, 6, 15), created_by=self.manager,
        )
        _, row = self._preview_row()
        self.assertEqual(row['classification'], 'DUPLICATE')

    def test_48_reimportacao_do_mesmo_hash(self):
        first, _ = self._preview_row()
        self.assertEqual(self._confirm(first).status_code, 200)
        second, _ = self._preview_row()
        self.assertTrue(second.data['already_imported'])
        self.assertEqual(self._confirm(second).status_code, 409)

    def test_49_reimportacao_confirmada_nao_duplica_linha(self):
        first, _ = self._preview_row()
        self._confirm(first)
        second, _ = self._preview_row()
        response = self._confirm(second, force=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['created'], 0)
        self.assertEqual(response.data['duplicates'], 1)

    # 50-58: arquivos, fatal, isolamento e permissao.
    def test_50_arquivo_vazio(self):
        self.assertEqual(self._upload(b'').status_code, 400)

    def test_51_arquivo_maior_que_limite(self):
        self.assertEqual(self._upload(b'x' * (5 * 1024 * 1024 + 1)).status_code, 400)

    def test_52_mais_de_500_linhas(self):
        lines = ['data;vendedor;valor'] + [
            f'15/06/2026;Carlos Silva;{index},00' for index in range(1, 502)
        ]
        self.assertEqual(self._upload('\n'.join(lines)).status_code, 400)

    def test_53_csv_invalido(self):
        response = self._upload('data;vendedor;valor\n"15/06/2026;Carlos Silva;500,00')
        self.assertEqual(response.status_code, 400)

    def test_54_xlsx_invalido(self):
        self.assertEqual(self._upload(b'not-xlsx', 'bad.xlsx').status_code, 400)

    def test_55_extensao_invalida(self):
        self.assertEqual(self._upload(self._csv(), 'import.xlsm').status_code, 400)

    def test_56_rollback_em_erro_fatal(self):
        preview, _ = self._preview_row()
        key = _preview_cache_key(preview.data['batch_uuid'])
        payload = cache.get(key)
        payload['file_hash'] = '0' * 64
        cache.set(key, payload, 1800)
        response = self._confirm(preview)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(SellerDayJustification.objects.count(), 0)

    def test_57_isolamento_multi_tenant_na_confirmacao(self):
        preview, _ = self._preview_row()
        other_manager = User.objects.create_user(
            username='other_manager', role=User.Role.MANAGER,
            tenant=self.other_tenant,
        )
        response = self._confirm(preview, user=other_manager)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Sale.objects.count(), 0)

    def test_58_seller_recebe_403(self):
        response = self._upload(self._csv(), user=self.seller_user)
        self.assertEqual(response.status_code, 403)

    # 59-65: datas, revalidacao, invariantes, migrations e queries.
    def test_59_datas_29_e_30_nao_se_deslocam(self):
        response = self._upload(
            'data;vendedor;valor\n29/06/2026;Carlos Silva;500,00\n30/06/2026;Carlos Silva;600,00'
        )
        self.assertEqual(
            [row['date'] for row in response.data['results']],
            ['2026-06-29', '2026-06-30'],
        )

    def test_60_falta_nao_recebe_valor_da_linha_seguinte(self):
        response = self._upload(
            'data;vendedor;valor\n29/06/2026;Carlos Silva;FALTA\n30/06/2026;Carlos Silva;9436,72'
        )
        first, second = response.data['results']
        self.assertIsNone(first['normalized_amount'])
        self.assertEqual(first['normalized_reason'], 'FALTA')
        self.assertEqual(second['normalized_amount'], 943672)

    def test_61_confirmacao_revalida_competencia(self):
        preview, _ = self._preview_row()
        self._set_period_status(CommissionPeriod.Status.FECHADA)
        response = self._confirm(preview)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['created'], 0)
        self.assertEqual(response.data['conflicts'], 1)

    def test_62_mudanca_entre_preview_e_confirmacao_detectada(self):
        preview, _ = self._preview_row()
        SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 6, 15),
            reason='FALTA', created_by=self.manager,
        )
        response = self._confirm(preview)
        self.assertEqual(response.data['created'], 0)
        self.assertEqual(response.data['conflicts'], 1)

    def test_63_nenhuma_sale_de_zero(self):
        preview, _ = self._preview_row('0,00')
        response = self._confirm(preview)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Sale.objects.filter(amount=0).exists())

    def test_64_batch_existente_sem_migration_nova(self):
        preview, _ = self._preview_row()
        batch = SaleImportBatch.objects.get(uuid=preview.data['batch_uuid'])
        self.assertEqual(batch.status, 'PENDING')
        self.assertEqual(batch.file_hash, preview.data['file_hash'])

    def _query_count(self, size):
        rows = [
            ['15/06/2026', 'Carlos Silva', f'{index + 1},00']
            for index in range(size)
        ]
        headers = normalize_headers(['data', 'vendedor', 'valor'])
        with CaptureQueriesContext(connection) as queries:
            results = classify_import_rows(self.tenant, rows, headers)
        self.assertEqual(len(results), size)
        return len(queries)

    def test_65_nenhuma_query_n_mais_um_por_linha(self):
        counts = [self._query_count(size) for size in (10, 100, 500)]
        self.assertEqual(counts[0], counts[1])
        self.assertEqual(counts[1], counts[2])
        self.assertLessEqual(counts[2], 5)
