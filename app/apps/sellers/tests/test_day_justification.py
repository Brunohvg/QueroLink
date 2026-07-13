"""PROMPT 47 - testes do model e servico de SellerDayJustification (LOTE 8)."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.db.models import Sum
from django.test import TestCase
from django.core.exceptions import ValidationError as DjangoValidationError

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller, SellerDayJustification
from app.apps.sellers.services import (
    create_day_justification, update_day_justification,
    delete_day_justification, JustificationError,
)
from app.apps.sales.models import Sale
from app.apps.audit.models import AuditLog


class SellerDayJustificationModelServiceTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='JD Co', slug='jd-co',
            default_commission_rate=Decimal('0.01'),
        )
        self.admin = User.objects.create_user(
            username='adminjd', role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='seljd', role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Seller JD',
            phone='55999990001', commission_rate=Decimal('0.01'),
        )
        self.today = date(2026, 7, 12)

    # 1 - criacao valida
    def test_create_valid(self):
        j = create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.ATESTADO,
            notes='aprovado', user=self.admin,
        )
        self.assertEqual(j.reason, 'ATESTADO')
        self.assertEqual(j.notes, 'aprovado')
        self.assertEqual(j.created_by, self.admin)
        self.assertEqual(j.tenant, self.tenant)
        self.assertEqual(j.seller, self.seller)

    # 2 - atualizacao de reason
    def test_update_reason(self):
        j = create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.ATESTADO,
            user=self.admin,
        )
        updated = update_day_justification(
            justification=j, user=self.admin,
            reason=SellerDayJustification.Reason.FALTA,
        )
        self.assertEqual(updated.reason, 'FALTA')

    # 3 - atualizacao de notes
    def test_update_notes(self):
        j = create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.FOLGA,
            user=self.admin,
        )
        updated = update_day_justification(
            justification=j, user=self.admin, notes='folga aprovada',
        )
        self.assertEqual(updated.notes, 'folga aprovada')

    # 4 - remocao
    def test_delete(self):
        j = create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.FERIAS,
            user=self.admin,
        )
        delete_day_justification(justification=j, user=self.admin)
        self.assertFalse(
            SellerDayJustification.objects.filter(pk=j.pk).exists(),
        )

    # 5 - unicidade tenant + seller + date
    def test_unique_constraint(self):
        create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.ATESTADO,
            user=self.admin,
        )
        with self.assertRaises(JustificationError):
            create_day_justification(
                tenant=self.tenant, seller=self.seller, date=self.today,
                reason=SellerDayJustification.Reason.FALTA,
                user=self.admin,
            )

    # 6 - mesmo seller/data em tenants diferentes nao colide
    def test_same_seller_date_diff_tenant(self):
        other = Tenant.objects.create(
            company_name='Other JD', slug='other-jd',
            default_commission_rate=Decimal('0.01'),
        )
        other_admin = User.objects.create_user(
            username='otherjd', role=User.Role.ADMIN, tenant=other,
        )
        other_seller = Seller.objects.create(
            tenant=other, user=other_admin, name='Other Seller',
            phone='55999990002', commission_rate=Decimal('0.01'),
        )
        j1 = create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.ATESTADO,
            user=self.admin,
        )
        j2 = create_day_justification(
            tenant=other, seller=other_seller, date=self.today,
            reason=SellerDayJustification.Reason.FALTA,
            user=other_admin,
        )
        self.assertEqual(j1.tenant, self.tenant)
        self.assertEqual(j2.tenant, other)

    # 7 - seller de outro tenant rejeitado
    def test_seller_other_tenant_rejected(self):
        other = Tenant.objects.create(
            company_name='Other2', slug='other2',
            default_commission_rate=Decimal('0.01'),
        )
        other_seller = Seller.objects.create(
            tenant=other, user=User.objects.create_user(
                username='os', role=User.Role.SELLER, tenant=other,
            ),
            name='OS', phone='55999990003', commission_rate=Decimal('0.01'),
        )
        with self.assertRaises(JustificationError):
            create_day_justification(
                tenant=self.tenant, seller=other_seller, date=self.today,
                reason=SellerDayJustification.Reason.ATESTADO,
                user=self.admin,
            )

    # 8 - reason invalido
    def test_invalid_reason(self):
        j = SellerDayJustification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason='INVALIDO', created_by=self.admin, updated_by=self.admin,
        )
        with self.assertRaises(DjangoValidationError):
            j.full_clean(exclude=['created_by', 'updated_by'])

    # 9 - data invalida
    def test_invalid_date(self):
        with self.assertRaises(JustificationError):
            create_day_justification(
                tenant=self.tenant, seller=self.seller, date='not-a-date',
                reason=SellerDayJustification.Reason.FALTA,
                user=self.admin,
            )

    # 10 - notes acima do limite (max 255)
    def test_notes_too_long(self):
        j = SellerDayJustification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.OUTRO,
            notes='x' * 300, created_by=self.admin, updated_by=self.admin,
        )
        with self.assertRaises(DjangoValidationError):
            j.full_clean(exclude=['created_by', 'updated_by'])

    # 11 - auditoria de criacao
    def test_audit_creation(self):
        before = AuditLog.objects.count()
        create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.FERIADO,
            user=self.admin,
        )
        self.assertEqual(AuditLog.objects.count(), before + 1)
        log = AuditLog.objects.last()
        self.assertEqual(log.action, 'day_justification.created')
        self.assertIn('reason', log.changes)

    # 12 - auditoria de alteracao com old/new
    def test_audit_update(self):
        j = create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.FERIADO,
            user=self.admin,
        )
        before = AuditLog.objects.count()
        update_day_justification(
            justification=j, user=self.admin,
            reason=SellerDayJustification.Reason.FALTA,
        )
        self.assertEqual(AuditLog.objects.count(), before + 1)
        log = AuditLog.objects.last()
        self.assertEqual(log.action, 'day_justification.updated')
        self.assertEqual(log.changes['reason']['old'], 'FERIADO')
        self.assertEqual(log.changes['reason']['new'], 'FALTA')

    # 13 - auditoria de remocao
    def test_audit_deletion(self):
        j = create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.FERIAS,
            user=self.admin,
        )
        before = AuditLog.objects.count()
        delete_day_justification(justification=j, user=self.admin)
        self.assertEqual(AuditLog.objects.count(), before + 1)
        log = AuditLog.objects.last()
        self.assertEqual(log.action, 'day_justification.deleted')

    # 14 - rollback em erro (forca falha no audit dentro do atomic)
    def test_rollback_on_error(self):
        with patch('app.apps.sellers.services.log_action') as mock_log:
            mock_log.side_effect = RuntimeError('falha forçada')
            with self.assertRaises(RuntimeError):
                create_day_justification(
                    tenant=self.tenant, seller=self.seller,
                    date=self.today,
                    reason=SellerDayJustification.Reason.FALTA,
                    user=self.admin,
                )
        self.assertFalse(
            SellerDayJustification.objects.filter(
                tenant=self.tenant, seller=self.seller, date=self.today,
            ).exists(),
        )

    # 15 - nenhuma Sale criada
    def test_no_sale_created(self):
        before = Sale.objects.count()
        create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.ATESTADO,
            user=self.admin,
        )
        self.assertEqual(Sale.objects.count(), before)

    # 16 - nenhuma Sale alterada
    def test_no_sale_altered(self):
        sale = Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=100000, sale_date=self.today, created_by=self.seller_user,
        )
        create_day_justification(
            tenant=self.tenant, seller=self.seller,
            date=date(2026, 7, 13),
            reason=SellerDayJustification.Reason.ATESTADO,
            user=self.admin,
        )
        sale.refresh_from_db()
        self.assertEqual(sale.amount, 100000)
        self.assertEqual(sale.status, 'ATIVA')

    # 17 - nenhuma comissao alterada
    def test_no_commission_altered(self):
        from app.apps.commissions.models import CommissionPeriod, SellerCommission
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=date(2026, 6, 21), end_date=date(2026, 7, 20),
            label='Julho/2026',
        )
        sc = SellerCommission.objects.create(
            period=period, seller=self.seller,
            commission_rate=Decimal('0.01'), status=SellerCommission.Status.ABERTA,
        )
        create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.ATESTADO,
            user=self.admin,
        )
        sc.refresh_from_db()
        self.assertEqual(sc.status, SellerCommission.Status.ABERTA)
        self.assertEqual(sc.commission_rate, Decimal('0.01'))

    # 18 - nenhum total financeiro alterado (valor total de Sales intocado)
    def test_no_financial_total_altered(self):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=Sale.Origin.MANUAL,
            amount=500000, sale_date=date(2026, 7, 10),
            created_by=self.seller_user,
        )
        total_before = Sale.objects.filter(
            tenant=self.tenant, status='ATIVA',
        ).aggregate(t=Sum('amount'))['t'] or 0
        create_day_justification(
            tenant=self.tenant, seller=self.seller, date=self.today,
            reason=SellerDayJustification.Reason.ATESTADO,
            user=self.admin,
        )
        total_after = Sale.objects.filter(
            tenant=self.tenant, status='ATIVA',
        ).aggregate(t=Sum('amount'))['t'] or 0
        self.assertEqual(total_before, total_after)
