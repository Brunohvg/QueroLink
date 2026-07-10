from django.db import transaction
from django.utils import timezone
from app.apps.sales.models import Sale, SaleChangeLog
from app.apps.commissions.services import (
    resolve_period_for_date,
    ensure_seller_commission,
)
from app.apps.commissions.models import SellerCommission


EDITABLE_FIELDS = {'amount', 'sale_date', 'notes'}


def _validate_period(sale, ref_date):
    period = resolve_period_for_date(sale.tenant, ref_date)
    if not period:
        return True, None
    sc = SellerCommission.objects.filter(
        period=period, seller=sale.seller,
    ).first()
    if not sc:
        return True, None
    if not sc.is_editable:
        return False, (
            f'A comissão de {sale.seller.name} já foi fechada '
            f'ou paga nesta competência.'
        )
    return True, None


def update_sale_as_manager(sale, user, data, reason):
    if sale.status == 'ESTORNADA':
        raise ValueError('Venda estornada não pode ser alterada.')

    if not reason or len(reason.strip()) < 5:
        raise ValueError('Motivo é obrigatório e deve ter no mínimo 5 caracteres.')

    can, err = _validate_period(sale, sale.sale_date)
    if not can:
        raise ValueError(err)

    new_date = data.get('sale_date', sale.sale_date)
    if new_date != sale.sale_date:
        can, err = _validate_period(sale, new_date)
        if not can:
            raise ValueError(err)

    field_changes = {}
    for field in EDITABLE_FIELDS:
        if field in data:
            new_value = data[field]
            old_value = getattr(sale, field)
            if new_value != old_value:
                if isinstance(old_value, (int, float)):
                    field_changes[field] = {
                        'old': old_value,
                        'new': new_value,
                    }
                elif field == 'sale_date':
                    field_changes[field] = {
                        'old': str(old_value),
                        'new': str(new_value),
                    }
                else:
                    field_changes[field] = {
                        'old': str(old_value) if old_value else '',
                        'new': str(new_value) if new_value else '',
                    }

    if not field_changes:
        return sale, False

    with transaction.atomic():
        for field in EDITABLE_FIELDS:
            if field in data:
                setattr(sale, field, data[field])
        sale.updated_by = user
        sale.save()

        SaleChangeLog.objects.create(
            sale=sale,
            tenant=sale.tenant,
            action=SaleChangeLog.Action.UPDATE,
            changed_by=user,
            field_changes=field_changes,
            reason=reason.strip(),
        )

        if 'sale_date' in field_changes or 'amount' in field_changes:
            ensure_seller_commission(sale.seller, sale.sale_date)

    return sale, True


MAX_IMPORT_ROWS = 500


def _parse_br_number(value):
    if not value:
        return None
    s = str(value).strip().replace('"', '').replace("'", '')
    if ',' in s or ('.' in s and s.count('.') > 1):
        s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_date(value):
    if not value:
        return None
    s = str(value).strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            from datetime import datetime
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _resolve_seller(tenant, name_str):
    from app.apps.sellers.models import Seller
    name = str(name_str or '').strip()
    if not name:
        return None, None

    try:
        from uuid import UUID
        uid = UUID(name)
        seller = Seller.objects.filter(uuid=uid, tenant=tenant).first()
        if seller:
            return seller, 'uuid'
    except (ValueError, AttributeError):
        pass

    seller = Seller.objects.filter(tenant=tenant, name__iexact=name).first()
    if seller:
        return seller, 'name_exact'

    sellers = list(Seller.objects.filter(
        tenant=tenant, name__icontains=name,
    )[:5])
    if len(sellers) == 1:
        return sellers[0], 'name_partial'
    if len(sellers) > 1:
        return sellers, 'ambiguous'

    return None, None


def _parse_csv(content):
    import csv
    import io
    delim = ','
    first_line = content.split('\n')[0] if '\n' in content else content
    if first_line.count(';') > first_line.count(','):
        delim = ';'
    reader = csv.reader(io.StringIO(content), delimiter=delim)
    rows = []
    for row in reader:
        if row:
            rows.append(row)
    return rows


def _parse_xlsx(content_bytes):
    try:
        import io
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content_bytes), read_only=True)
        ws = wb.active
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append([str(c) if c is not None else '' for c in row])
        return rows
    except ImportError:
        return None


def normalize_headers(headers):
    mapping = {}
    for i, h in enumerate(headers):
        key = str(h).strip().lower()
        if key in ('data', 'date', 'dt'):
            mapping['date'] = i
        elif key in ('vendedor', 'vendedora', 'seller', 'nome'):
            mapping['seller'] = i
        elif key in ('valor', 'value', 'total', 'amount'):
            mapping['amount'] = i
        elif key in ('observacao', 'obs', 'observações', 'observacoes', 'notes'):
            mapping['notes'] = i
        elif key in ('seller_uuid',):
            mapping['seller_uuid'] = i
        elif key in ('codigo_vendedor', 'codigo', 'code', 'id'):
            mapping['codigo_vendedor'] = i
    return mapping


def preview_import_rows(tenant, rows, header_map):
    results = []
    for idx, row in enumerate(rows):
        result = {
            'row_number': idx + 1,
            'status': 'ok',
            'message': '',
            'resolved_seller': None,
            'resolved_seller_uuid': None,
            'resolved_seller_name': None,
            'match_type': None,
            'suggestions': [],
            'sale_date': None,
            'amount_cents': None,
            'notes': '',
            'period_label': None,
        }
        date_idx = header_map.get('date')
        seller_idx = header_map.get('seller')
        seller_uuid_idx = header_map.get('seller_uuid')
        codigo_idx = header_map.get('codigo_vendedor')
        amount_idx = header_map.get('amount')
        notes_idx = header_map.get('notes')

        if len(row) < max(header_map.values()) + 1:
            result['status'] = 'error'
            result['message'] = 'Linha incompleta'
            results.append(result)
            continue

        date_str = row[date_idx] if date_idx is not None and date_idx < len(row) else None
        amount_str = row[amount_idx] if amount_idx is not None and amount_idx < len(row) else None

        parsed_date = _parse_date(date_str)
        if not parsed_date:
            result['status'] = 'error'
            result['message'] = f'Data invalida: {date_str}'
            results.append(result)
            continue
        result['sale_date'] = parsed_date.isoformat()

        parsed_amount = _parse_br_number(amount_str)
        if parsed_amount is None or parsed_amount <= 0:
            result['status'] = 'error'
            result['message'] = f'Valor invalido: {amount_str}'
            results.append(result)
            continue
        result['amount_cents'] = int(round(parsed_amount * 100))

        result['notes'] = str(row[notes_idx] or '') if notes_idx is not None and notes_idx < len(row) else ''

        seller_name = row[seller_idx] if seller_idx is not None and seller_idx < len(row) else ''
        seller_resolved = None

        if seller_uuid_idx is not None and seller_uuid_idx < len(row):
            from uuid import UUID as _UUID
            from app.apps.sellers.models import Seller as _Seller
            uid_str = row[seller_uuid_idx]
            if uid_str:
                try:
                    seller_resolved = _Seller.objects.filter(
                        tenant=tenant, uuid=_UUID(uid_str),
                    ).first()
                    if seller_resolved:
                        result['match_type'] = 'uuid'
                except (ValueError, AttributeError):
                    pass

        if not seller_resolved and codigo_idx is not None and codigo_idx < len(row):
            seller_resolved = None
            result['message'] = 'codigo_vendedor nao implementado'

        if not seller_resolved and seller_name:
            resolved, match_type = _resolve_seller(tenant, seller_name)
            if match_type == 'ambiguous':
                result['status'] = 'needs_selection'
                result['message'] = 'Multiplos vendedores encontrados'
                result['suggestions'] = [
                    {'uuid': str(s.uuid), 'name': s.name}
                    for s in resolved
                ]
                results.append(result)
                continue
            elif match_type and resolved:
                seller_resolved = resolved
                result['match_type'] = match_type

        if not seller_resolved:
            result['status'] = 'error'
            if seller_name:
                from app.apps.sellers.models import Seller as _Seller
                suggestions = list(_Seller.objects.filter(
                    tenant=tenant, name__icontains=seller_name,
                )[:5])
                if suggestions:
                    result['suggestions'] = [
                        {'uuid': str(s.uuid), 'name': s.name}
                        for s in suggestions
                    ]
                    result['message'] = f'Vendedor nao encontrado: {seller_name}. Sugestoes disponiveis.'
                else:
                    result['message'] = f'Vendedor nao encontrado: {seller_name}'
            else:
                result['message'] = 'Vendedor nao informado'
            results.append(result)
            continue

        result['resolved_seller'] = str(seller_resolved.uuid)
        result['resolved_seller_uuid'] = str(seller_resolved.uuid)
        result['resolved_seller_name'] = seller_resolved.name

        from app.apps.sales.models import Sale as _Sale
        exists = _Sale.objects.filter(
            tenant=tenant,
            seller=seller_resolved,
            sale_date=parsed_date,
            amount=result['amount_cents'],
        ).exists()
        if exists:
            result['status'] = 'duplicate'
            result['message'] = 'Venda duplicada (mesmo vendedor, data e valor)'
            results.append(result)
            continue

        from app.apps.commissions.services import resolve_period_for_date as _rpd
        period = _rpd(tenant, parsed_date)
        if period:
            result['period_label'] = period.display_label or period.label or f'{period.month:02d}/{period.year}'
            from app.apps.commissions.models import (
                SellerCommission as _SC,
            )
            sc = _SC.objects.filter(
                period=period, seller=seller_resolved,
            ).first()
            if sc and not sc.is_editable:
                result['status'] = 'error'
                result['message'] = (
                    f'A competencia {result["period_label"]} esta fechada '
                    f'ou paga para {seller_resolved.name}'
                )
                results.append(result)
                continue

        results.append(result)

    return results


def import_sales(tenant, user, confirmed_rows, filename, file_hash):
    from app.apps.sales.models import SaleImportBatch
    from app.apps.sellers.models import Seller as _Seller

    batch = SaleImportBatch.objects.create(
        tenant=tenant,
        filename=filename,
        file_hash=file_hash,
        uploaded_by=user,
        status='PENDING',
        total_rows=len(confirmed_rows),
    )

    created = 0
    duplicates = 0
    errors = []

    with transaction.atomic():
        for item in confirmed_rows:
            try:
                seller_uuid = item.get('resolved_seller_uuid')
                amount_cents = int(item.get('amount_cents', 0))
                sale_date_str = item.get('sale_date')
                notes = str(item.get('notes', '') or '')[:255]

                from datetime import date as _date
                sale_date = _date.fromisoformat(sale_date_str)

                seller = _Seller.objects.select_for_update().get(
                    uuid=seller_uuid, tenant=tenant,
                )

                exists = Sale.objects.filter(
                    tenant=tenant, seller=seller,
                    sale_date=sale_date, amount=amount_cents,
                ).exists()
                if exists:
                    duplicates += 1
                    continue

                sale = Sale.objects.create(
                    tenant=tenant,
                    seller=seller,
                    origin=Sale.Origin.IMPORTADA,
                    amount=amount_cents,
                    sale_date=sale_date,
                    notes=notes,
                    created_by=user,
                )

                SaleChangeLog.objects.create(
                    sale=sale,
                    tenant=tenant,
                    action=SaleChangeLog.Action.CREATE_IMPORT,
                    changed_by=user,
                    field_changes={
                        'import_batch': str(batch.uuid),
                        'filename': filename,
                        'amount': amount_cents,
                        'sale_date': str(sale_date),
                    },
                    reason=f'Importado via planilha: {filename}',
                )

                from app.apps.commissions.services import ensure_seller_commission
                ensure_seller_commission(seller, sale_date)
                created += 1
            except Exception as e:
                errors.append({
                    'row_number': item.get('row_number'),
                    'error': str(e),
                })

        if errors and not created:
            transaction.set_rollback(True)
            batch.status = 'REJECTED'
            batch.error_count = len(errors)
            batch.save()
            raise ValueError('Nenhuma venda importada. Todos os registros falharam.')

        batch.status = 'IMPORTED'
        batch.created_count = created
        batch.duplicate_count = duplicates
        batch.error_count = len(errors)
        batch.save()

    return {
        'batch_uuid': str(batch.uuid),
        'created': created,
        'duplicates': duplicates,
        'errors': errors,
        'total': len(confirmed_rows),
    }
