import logging
from zipfile import ZipFile
from io import BytesIO
from django.template.loader import render_to_string
from django.utils import timezone
from django.db.models import Sum as DSum
from weasyprint import HTML
from app.apps.commissions.services import (
    calculate_estimated_commission,
    calculate_estimated_commission_for_period,
    get_commission_rate,
    get_period_by_legacy_label,
    legacy_month_range,
)
from app.apps.sales.models import Sale as SModel
from app.apps.sellers.models import Seller as SellerM
from app.apps.commissions.models import CommissionPeriod, SellerCommission, CommissionAdjustment

logger = logging.getLogger(__name__)


def _fmt_br(val):
    return f'{val/100:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def build_accounting_zip(tenant, month_int, year_int):
    period = get_period_by_legacy_label(tenant, month_int, year_int)
    if period:
        start = period.start_date
        end = period.end_date
        competencia_label = period.display_label
    else:
        start, end = legacy_month_range(month_int, year_int)
        competencia_label = f'{month_int:02d}/{year_int}'

    buf = BytesIO()
    with ZipFile(buf, 'w') as zf:
        sales = SModel.objects.filter(
            tenant=tenant, origin__in=SModel.COMMISSION_ORIGINS,
            sale_date__gte=start, sale_date__lte=end,
        ).select_related('seller').order_by('sale_date', 'seller__name')

        csv1_lines = ['Data;Vendedor;CPF Vendedor;Valor (R$);Origem;Status;Observacao']
        for s in sales:
            cpf = s.seller.cpf_formatted or 'Nao informado'
            valor = f'{s.amount/100:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
            origin = 'Importação' if s.origin == SModel.Origin.IMPORTADA else 'Manual'
            status = 'Estornada' if s.status == 'ESTORNADA' else 'Ativa'
            csv1_lines.append(f'{s.sale_date.strftime("%d/%m/%Y")};{s.seller.name};{cpf};{valor};{origin};{status};{s.notes or ""}')
        zf.writestr(f'vendas_{month_int:02d}_{year_int}.csv', '\n'.join(csv1_lines).encode('utf-8-sig'))

        csv2_lines = ['Vendedor;CPF Vendedor;Total Vendido (R$);Taxa (%);Comissao Bruta (R$);Ajustes (R$);Comissao Liquida (R$);Status;Data Pagamento']
        for seller in SellerM.objects.filter(tenant=tenant, is_active=True):
            sc = SellerCommission.objects.filter(seller=seller, period=period).select_related('period').first() if period else None
            if sc:
                est_comm, est_total = calculate_estimated_commission_for_period(seller, period)
                comissao = sc.commission_amount
                total = sc.total_sold_amount if sc.total_sold_amount else est_total
                status_label = sc.get_status_display()
                payment_date = sc.payment_date.strftime('%d/%m/%Y') if sc.payment_date else ''
                ads = CommissionAdjustment.objects.filter(seller_commission=sc)
                total_adj = sum(a.difference for a in ads)
                liquida = (sc.paid_amount or sc.amount_due) + total_adj
            else:
                if period:
                    est_comm, est_total = calculate_estimated_commission_for_period(seller, period)
                else:
                    est_comm, est_total = calculate_estimated_commission(seller, month_int, year_int)
                total = est_total
                comissao = est_comm
                status_label = 'Estimativa'
                payment_date = ''
                total_adj = 0
                liquida = comissao

            cpf = seller.cpf_formatted or 'Nao informado'
            taxa = f'{float(get_commission_rate(seller))*100:.2f}'.replace('.', ',')
            csv2_lines.append(
                f'{seller.name};{cpf};'
                f'{_fmt_br(total)};{taxa};{_fmt_br(comissao)};{_fmt_br(total_adj)};{_fmt_br(liquida)};{status_label};{payment_date}'
            )
        zf.writestr(f'comissoes_{month_int:02d}_{year_int}.csv', '\n'.join(csv2_lines).encode('utf-8-sig'))

        total_sold_all = sum(s.amount for s in sales if s.status == 'ATIVA')
        total_comm_all = 0
        for seller in SellerM.objects.filter(tenant=tenant, is_active=True):
            if period:
                c, _ = calculate_estimated_commission_for_period(seller, period)
            else:
                c, _ = calculate_estimated_commission(seller, month_int, year_int)
            total_comm_all += c
        cnpj_val = 'Nao informado'
        try:
            cnpj_val = tenant.cnpj or 'Nao informado'
        except Exception:
            pass
        csv3 = (
            f'Empresa;CNPJ;Competencia;Periodo;Total Vendido;Total Comissoes;Qtd Vendedores\n'
            f'{tenant.company_name};{cnpj_val};{competencia_label};{start.strftime("%d/%m/%Y")} a {end.strftime("%d/%m/%Y")};{_fmt_br(total_sold_all)};'
            f'{_fmt_br(total_comm_all)};{SellerM.objects.filter(tenant=tenant, is_active=True).count()}'
        )
        zf.writestr(f'resumo_{month_int:02d}_{year_int}.csv', csv3.encode('utf-8-sig'))

        sellers_for_pdf = []
        total_sold_pdf = 0
        for seller in SellerM.objects.filter(tenant=tenant, is_active=True):
            if period:
                est_comm, est_total = calculate_estimated_commission_for_period(seller, period)
                sc = SellerCommission.objects.filter(seller=seller, period=period).select_related('period').first()
            else:
                est_comm, est_total = calculate_estimated_commission(seller, month_int, year_int)
                sc = None
            status_label = sc.get_status_display() if sc else 'Estimativa'
            commission_amount = sc.commission_amount if sc else est_comm
            comm_rate = float(sc.commission_rate if sc and sc.commission_rate else get_commission_rate(seller)) * 100
            sellers_for_pdf.append({
                'name': seller.name, 'total_sold': est_total,
                'commission': commission_amount, 'commission_rate': comm_rate,
                'status': status_label,
            })
            total_sold_pdf += est_total
        sellers_for_pdf.sort(key=lambda s: s['total_sold'], reverse=True)

        prev_period = None
        if period:
            prev_period = CommissionPeriod.objects.filter(
                tenant=tenant,
                end_date__lt=period.start_date,
            ).exclude(
                status=CommissionPeriod.Status.CANCELADA,
            ).order_by('-end_date').first()

        if prev_period:
            prev_total = SModel.objects.filter(
                tenant=tenant,
                origin__in=SModel.COMMISSION_ORIGINS,
                status='ATIVA',
                sale_date__gte=prev_period.start_date,
                sale_date__lte=prev_period.end_date,
            ).aggregate(t=DSum('amount'))['t'] or 0
        else:
            prev_total = 0
        variacao = round((total_sold_pdf - prev_total) / prev_total * 100) if prev_total > 0 else None

        pdf_html = render_to_string('reports/relatorio_mensal.html', {
            'tenant': tenant, 'competencia': competencia_label,
            'periodo': f'{start.strftime("%d/%m/%Y")} a {end.strftime("%d/%m/%Y")}',
            'data_geracao': timezone.now().strftime('%d/%m/%Y'),
            'sellers_data': sellers_for_pdf, 'total_sold': total_sold_pdf,
            'total_commissions': total_comm_all, 'total_aberta': 0, 'total_fechada': 0,
            'total_paga': 0, 'num_sellers': SellerM.objects.filter(tenant=tenant, is_active=True).count(),
            'prev_total': prev_total, 'variacao': variacao,
        })
        zf.writestr(f'resumo_{month_int:02d}_{year_int}.pdf', HTML(string=pdf_html).write_pdf())

        sellers_missing_cpf = [
            s for s in SellerM.objects.filter(tenant=tenant, is_active=True)
            if not s.cpf and SellerCommission.objects.filter(
                seller=s, period=period,
            ).exists()
        ]
        if sellers_missing_cpf:
            pend_lines = ['Vendedores sem CPF cadastrado — a folha pode exigir:']
            for s in sellers_missing_cpf:
                pend_lines.append(f'- {s.name}')
            zf.writestr('pendencias.txt', '\n'.join(pend_lines).encode('utf-8'))

    return buf.getvalue()
