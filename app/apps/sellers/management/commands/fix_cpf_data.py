from django.core.management.base import BaseCommand
from django.db import transaction
from app.apps.sellers.models import Seller
from app.apps.sellers.validators import normalize_and_validate_cpf


class Command(BaseCommand):
    help = 'Normaliza CPFs e remove duplicatas por tenant (--apply para gravar).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Aplica as alteracoes no banco. Sem esta flag, apenas relata (dry-run).',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        mode = 'APLICANDO' if apply else 'DRY-RUN (sem gravacao)'
        self.stdout.write(f'=== fix_cpf_data — {mode} ===\n')

        sellers = Seller.objects.all().order_by('tenant', 'created_at')
        seen = {}
        normalized = 0
        cleared_duplicates = 0
        cleared_invalid = 0

        for seller in sellers:
            if not seller.cpf:
                continue

            try:
                digits = normalize_and_validate_cpf(seller.cpf)
            except ValueError:
                if seller.cpf == ''.join(filter(str.isdigit, seller.cpf)) and len(seller.cpf) == 11:
                    cl = seller.cpf
                else:
                    cl = seller.cpf if len(str(seller.cpf)) <= 20 else str(seller.cpf)[:17] + '...'
                self.stdout.write(
                    self.style.WARNING(
                        f'INVALIDO  pk={seller.uuid}  cpf={cl}'
                    )
                )
                if apply:
                    seller.cpf = None
                    seller.save(update_fields=['cpf'])
                cleared_invalid += 1
                continue

            key = (seller.tenant_id, digits)
            if key in seen:
                self.stdout.write(
                    self.style.WARNING(
                        f'DUPLICADO pk={seller.uuid}  cpf={digits}'
                    )
                )
                if apply:
                    seller.cpf = None
                    seller.save(update_fields=['cpf'])
                cleared_duplicates += 1
                continue

            seen[key] = seller.pk

            if digits != seller.cpf:
                self.stdout.write(
                    f'NORMALIZA pk={seller.uuid}  "{seller.cpf}" -> "{digits}"'
                )
                if apply:
                    seller.cpf = digits
                    seller.save(update_fields=['cpf'])
                normalized += 1

        self.stdout.write(
            f'\n=== RESUMO ===\n'
            f'  Normalizacoes: {normalized}\n'
            f'  CPFs invalidos zerados: {cleared_invalid}\n'
            f'  Duplicatas zeradas: {cleared_duplicates}\n'
            f'  Total processados: {sellers.count()}\n'
        )
        if not apply:
            self.stdout.write(
                self.style.SUCCESS(
                    'Dry-run concluido. Use --apply para gravar as alteracoes.'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('Alteracoes aplicadas com sucesso.')
            )
