import logging
from zipfile import ZipFile
from io import BytesIO
from django.template.loader import render_to_string
from django.utils import timezone
from django.db.models import Sum as DSum
from weasyprint import HTML
from app.apps.commissions.services import calculate_estimated_commission, get_commission_rate
from app.apps.sales.models import Sale as SModel
from app.apps.sellers.models import Seller as SellerM
from app.apps.commissions.models import SellerCommission, CommissionAdjustment

logger = logging.getLogger(__name__)


def _fmt_br(val):
    return f'{val/100:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def build_accounting_zip(tenant, month_int, year_int):
    buf = BytesIO()
    with ZipFile(buf, 'w') as zf:
        sales = SModel.objects.filter(
            tenant=tenant, sale_date__year=year_int, sale_date__month=month_int,
        ).select_related('seller').order_by('sale_date', 'seller__name')

        csv1_lines = ['Data;Vendedor;CPF Vendedor;Valor (R$);Origem;Status;Observacao']
        for s in sales:
            cpf = getattr(s.seller, 'cpf', '') or 'Nao informado'
            valor = f'{s.amount/100:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
            origin = 'Link' if s.origin == SModel.Origin.LINK else 'Manual'
            status = 'Estornada' if s.status == 'ESTORNADA' else 'Ativa'
            csv1_lines.append(f'{s.sale_date.strftime("%d/%m/%Y")};{s.seller.name};{cpf};{valor};{origin};{status};{s.notes or ""}')
        zf.writestr(f'vendas_{month_int:02d}_{year_int}.csv', '\n'.join(csv1_lines).encode('utf-8-sig'))

        csv2_lines = ['Vendedor;CPF Vendedor;Total Vendido (R$);Taxa (%);Comissao Bruta (R$);Ajustes (R$);Comissao Liquida (R$);Status;Data Pagamento']
        for seller in SellerM.objects.filter(tenant=tenant, is_active=True):
            sc = SellerCommission.objects.filter(
                seller=seller, period__month=month_int, period__year=year_int,
            ).select_related('period').first()
            if sc:
                est_comm, est_total = calculate_estimated_commission(seller, month_int, year_int)
                comissao = sc.commission_amount
                total = sc.total_sold_amount if sc.total_sold_amount else est_total
                status_label = sc.get_status_display()
                payment_date = sc.payment_date.strftime('%d/%m/%Y') if sc.payment_date else ''
                ads = CommissionAdjustment.objects.filter(seller_commission=sc)
                total_adj = sum(a.difference for a in ads)
                liquida = (sc.paid_amount or sc.amount_due) + total_adj
            else:
                est_comm, est_total = calculate_estimated_commission(seller, month_int, year_int)
                total = est_total
                comissao = est_comm
                status_label = 'Estimativa'
                payment_date = ''
                total_adj = 0
                liquida = comissao

            cpf = getattr(seller, 'cpf', '') or 'Nao informado'
            taxa = f'{float(get_commission_rate(seller))*100:.2f}'.replace('.', ',')
            csv2_lines.append(
                f'{seller.name};{cpf};'
                f'{_fmt_br(total)};{taxa};{_fmt_br(comissao)};{_fmt_br(total_adj)};{_fmt_br(liquida)};{status_label};{payment_date}'
            )
        zf.writestr(f'comissoes_{month_int:02d}_{year_int}.csv', '\n'.join(csv2_lines).encode('utf-8-sig'))

        total_sold_all = sum(s.amount for s in sales if s.status == 'ATIVA')
        total_comm_all = 0
        for seller in SellerM.objects.filter(tenant=tenant, is_active=True):
            c, _ = calculate_estimated_commission(seller, month_int, year_int)
            total_comm_all += c
        cnpj_val = 'Nao informado'
        try:
            cnpj_val = tenant.cnpj or 'Nao informado'
        except Exception:
            pass
        csv3 = (
            f'Empresa;CNPJ;Competencia;Total Vendido;Total Comissoes;Qtd Vendedores\n'
            f'{tenant.company_name};{cnpj_val};{month_int:02d}/{year_int};{_fmt_br(total_sold_all)};'
            f'{_fmt_br(total_comm_all)};{SellerM.objects.filter(tenant=tenant, is_active=True).count()}'
        )
        zf.writestr(f'resumo_{month_int:02d}_{year_int}.csv', csv3.encode('utf-8-sig'))

        sellers_for_pdf = []
        total_sold_pdf = 0
        for seller in SellerM.objects.filter(tenant=tenant, is_active=True):
            est_comm, est_total = calculate_estimated_commission(seller, month_int, year_int)
            sc = SellerCommission.objects.filter(
                seller=seller, period__month=month_int, period__year=year_int,
            ).select_related('period').first()
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

        prev_month = month_int - 1
        prev_year = year_int
        if prev_month == 0:
            prev_month = 12
            prev_year -= 1
        prev_total = SModel.objects.filter(
            tenant=tenant, status='ATIVA', sale_date__month=prev_month, sale_date__year=prev_year,
        ).aggregate(t=DSum('amount'))['t'] or 0
        variacao = round((total_sold_pdf - prev_total) / prev_total * 100) if prev_total > 0 else None

        pdf_html = render_to_string('reports/relatorio_mensal.html', {
            'tenant': tenant, 'competencia': f'{month_int:02d}/{year_int}',
            'data_geracao': timezone.now().strftime('%d/%m/%Y'),
            'sellers_data': sellers_for_pdf, 'total_sold': total_sold_pdf,
            'total_commissions': total_comm_all, 'total_aberta': 0, 'total_fechada': 0,
            'total_paga': 0, 'num_sellers': SellerM.objects.filter(tenant=tenant, is_active=True).count(),
            'prev_total': prev_total, 'variacao': variacao,
        })
        zf.writestr(f'resumo_{month_int:02d}_{year_int}.pdf', HTML(string=pdf_html).write_pdf())

    return buf.getvalue()
