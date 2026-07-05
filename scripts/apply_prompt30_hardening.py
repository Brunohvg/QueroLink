from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    file_path = ROOT / path
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly 1 match, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"patched {path}")


def write_new(path: str, content: str) -> None:
    file_path = ROOT / path
    if file_path.exists():
        raise SystemExit(f"{path}: file already exists")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    print(f"created {path}")


write_new(
    "app/apps/accounts/plans.py",
    '''from app.apps.accounts.models import Tenant\n\n\nOFFERED_PLAN_CODES = (\n    Tenant.Plan.STARTER,\n    Tenant.Plan.PRO,\n    Tenant.Plan.BUSINESS,\n)\n\n\ndef offered_plan_choices():\n    labels = dict(Tenant.Plan.choices)\n    return [(code, labels[code]) for code in OFFERED_PLAN_CODES]\n''',
)

replace_once(
    "app/apps/accounts/forms.py",
    "from app.apps.accounts.models import Tenant, User\n",
    "from app.apps.accounts.models import Tenant, User\nfrom app.apps.accounts.plans import offered_plan_choices\n",
)
replace_once(
    "app/apps/accounts/forms.py",
    "        choices=Tenant.Plan.choices,\n",
    "        choices=offered_plan_choices(),\n",
)

replace_once(
    "app/apps/billing/serializers.py",
    "from app.apps.accounts.models import Tenant\n",
    "from app.apps.accounts.models import Tenant\nfrom app.apps.accounts.plans import offered_plan_choices\n",
)
replace_once(
    "app/apps/billing/serializers.py",
    "    plan = serializers.ChoiceField(choices=Tenant.Plan.choices)\n",
    "    plan = serializers.ChoiceField(choices=offered_plan_choices())\n",
)
replace_once(
    "app/apps/billing/serializers.py",
    "    payment_method = serializers.ChoiceField(\n        choices=[('credit_card', 'Cartao'), ('boleto', 'Boleto')],\n        default='boleto',\n    )\n\n",
    "",
)

replace_once(
    "app/apps/billing/models.py",
    "    gateway_customer_id = models.CharField(\n        max_length=100, blank=True, null=True,\n    )\n",
    "    gateway_customer_id = models.CharField(\n        max_length=100, blank=True, null=True,\n    )\n    pending_cancel_gateway_subscription_id = models.CharField(\n        max_length=100, blank=True, null=True, db_index=True,\n        help_text='Assinatura remota anterior aguardando cancelamento/reconciliacao',\n    )\n",
)
replace_once(
    "app/apps/billing/models.py",
    "            models.Index(fields=['gateway_subscription_id']),\n",
    "            models.Index(fields=['gateway_subscription_id']),\n            models.Index(fields=['pending_cancel_gateway_subscription_id']),\n",
)

replace_once(
    "app/apps/billing/views.py",
    "from django.core.cache import cache\n",
    "from django.core.cache import cache\nfrom django.db import transaction\n",
)
replace_once(
    "app/apps/billing/views.py",
    "from app.apps.accounts.models import Tenant\n",
    "from app.apps.accounts.models import Tenant\nfrom app.apps.accounts.plans import OFFERED_PLAN_CODES\n",
)
replace_once(
    "app/apps/billing/views.py",
    "        for key in ['STARTER', 'PRO', 'ENTERPRISE']:\n",
    "        for key in OFFERED_PLAN_CODES:\n",
)
replace_once(
    "app/apps/billing/views.py",
    "        payment_method = serializer.validated_data['payment_method']\n\n",
    "",
)
old_upgrade = '''        sub, created = Subscription.objects.get_or_create(\n            tenant=tenant,\n            defaults={\n                'plan': plan,\n                'billing_cycle': billing_cycle,\n                'amount': amount,\n            },\n        )\n\n        if sub.gateway_subscription_id:\n            try:\n                gateway = MercadoPagoGateway()\n                gateway.cancel_preapproval(sub.gateway_subscription_id)\n            except (MercadoPagoError, Exception) as e:\n                logger.warning(\n                    "Erro ao cancelar subscription anterior %s: %s",\n                    sub.gateway_subscription_id, e,\n                )\n\n        try:\n            gateway = MercadoPagoGateway()\n            back_url = request.build_absolute_uri(\n                reverse('dashboard:assinatura'),\n            )\n            frequency = 12 if billing_cycle == 'YEARLY' else 1\n            result = gateway.create_preapproval(\n                reason=f"Plano {dict(Tenant.Plan.choices)[plan]} - Mérito by Vidalys",\n                external_reference=str(tenant.uuid),\n                payer_email=tenant.billing_email,\n                amount=amount / 100,\n                frequency=frequency,\n                frequency_type='months',\n                back_url=back_url,\n            )\n        except (MercadoPagoError, Exception) as e:\n            logger.exception("Erro ao criar preapproval no Mercado Pago")\n            return Response(\n                {'error': f'Erro ao criar assinatura: {e}'},\n                status=status.HTTP_502_BAD_GATEWAY,\n            )\n\n        sub.plan = plan\n        sub.billing_cycle = billing_cycle\n        sub.amount = amount\n        sub.gateway_subscription_id = result.get('id')\n        sub.status = Subscription.Status.PENDING\n        sub.save()\n\n        cache.delete(f'tenant_operational:{tenant.uuid}')\n\n        init_point = result.get('init_point', '')\n        return Response({\n            'subscription_id': sub.uuid,\n            'gateway_id': result.get('id'),\n            'init_point': init_point,\n            'status': sub.status,\n        })\n'''
new_upgrade = '''        sub, _ = Subscription.objects.get_or_create(\n            tenant=tenant,\n            defaults={\n                'plan': plan,\n                'billing_cycle': billing_cycle,\n                'amount': amount,\n            },\n        )\n        old_gateway_id = sub.gateway_subscription_id\n\n        try:\n            gateway = MercadoPagoGateway()\n            back_url = request.build_absolute_uri(\n                reverse('dashboard:assinatura'),\n            )\n            frequency = 12 if billing_cycle == 'YEARLY' else 1\n            result = gateway.create_preapproval(\n                reason=f"Plano {dict(Tenant.Plan.choices)[plan]} - Mérito by Vidalys",\n                external_reference=str(tenant.uuid),\n                payer_email=tenant.billing_email,\n                amount=amount / 100,\n                frequency=frequency,\n                frequency_type='months',\n                back_url=back_url,\n            )\n        except Exception as e:\n            logger.exception("Erro ao criar preapproval no Mercado Pago")\n            return Response(\n                {'error': f'Erro ao criar assinatura: {e}'},\n                status=status.HTTP_502_BAD_GATEWAY,\n            )\n\n        new_gateway_id = (result.get('id') or '').strip()\n        init_point = (result.get('init_point') or '').strip()\n        if not new_gateway_id:\n            logger.error("Mercado Pago retornou preapproval sem id para tenant %s", tenant.uuid)\n            return Response(\n                {'error': 'Resposta invalida do provedor de cobranca.'},\n                status=status.HTTP_502_BAD_GATEWAY,\n            )\n\n        with transaction.atomic():\n            sub = Subscription.objects.select_for_update().get(tenant=tenant)\n            sub.plan = plan\n            sub.billing_cycle = billing_cycle\n            sub.amount = amount\n            sub.gateway_subscription_id = new_gateway_id\n            sub.pending_cancel_gateway_subscription_id = (\n                old_gateway_id if old_gateway_id and old_gateway_id != new_gateway_id else None\n            )\n            sub.status = Subscription.Status.PENDING\n            sub.save()\n\n        cleanup_pending = False\n        if sub.pending_cancel_gateway_subscription_id:\n            try:\n                gateway.cancel_preapproval(sub.pending_cancel_gateway_subscription_id)\n            except Exception:\n                cleanup_pending = True\n                logger.exception(\n                    "Nova assinatura %s criada, mas falhou cancelamento da anterior %s",\n                    new_gateway_id, sub.pending_cancel_gateway_subscription_id,\n                )\n            else:\n                sub.pending_cancel_gateway_subscription_id = None\n                sub.save(update_fields=['pending_cancel_gateway_subscription_id', 'updated_at'])\n\n        cache.delete(f'tenant_operational:{tenant.uuid}')\n\n        response_status = status.HTTP_202_ACCEPTED if cleanup_pending else status.HTTP_200_OK\n        return Response({\n            'subscription_id': sub.uuid,\n            'gateway_id': new_gateway_id,\n            'init_point': init_point,\n            'status': sub.status,\n            'cleanup_pending': cleanup_pending,\n        }, status=response_status)\n'''
replace_once("app/apps/billing/views.py", old_upgrade, new_upgrade)
old_cancel = '''        if sub.gateway_subscription_id:\n            try:\n                gateway = MercadoPagoGateway()\n                gateway.cancel_preapproval(sub.gateway_subscription_id)\n            except (MercadoPagoError, Exception) as e:\n                logger.warning(\n                    "Erro ao cancelar no MP: %s", e,\n                )\n\n        sub.status = Subscription.Status.CANCELED\n        sub.save(update_fields=['status', 'updated_at'])\n'''
new_cancel = '''        if sub.gateway_subscription_id:\n            try:\n                gateway = MercadoPagoGateway()\n                gateway.cancel_preapproval(sub.gateway_subscription_id)\n            except Exception:\n                logger.exception(\n                    "Falha ao confirmar cancelamento remoto da assinatura %s",\n                    sub.gateway_subscription_id,\n                )\n                return Response(\n                    {'error': 'Nao foi possivel confirmar o cancelamento no provedor. Tente novamente.'},\n                    status=status.HTTP_502_BAD_GATEWAY,\n                )\n\n        sub.status = Subscription.Status.CANCELED\n        sub.save(update_fields=['status', 'updated_at'])\n'''
replace_once("app/apps/billing/views.py", old_cancel, new_cancel)

old_limits = '''PLAN_LIMITS = {\n    'STARTER': None,\n    'PRO': None,\n    'ENTERPRISE': None,\n}\n\n\ndef _check_seller_limit(tenant):\n    # Desabilitado — limite de vendedores nao e mais aplicado.\n    # Quando o billing for ativado, reativar esta funcao.\n    pass\n'''
new_limits = '''def _seller_limit(tenant):\n    from django.conf import settings\n    return getattr(settings, 'PLAN_SELLER_LIMITS', {}).get(tenant.plan)\n\n\ndef _check_seller_limit(tenant, requested=1):\n    limit = _seller_limit(tenant)\n    if limit is None:\n        return\n    tenant.__class__.objects.select_for_update().get(pk=tenant.pk)\n    current = Seller.objects.filter(tenant=tenant, is_active=True).count()\n    if current + requested > limit:\n        raise serializers.ValidationError({\n            'detail': (\n                f'Limite de vendedores do plano {tenant.plan} atingido. '\n                f'Limite: {limit}; ativos: {current}; solicitados: {requested}; '\n                f'capacidade restante: {max(0, limit - current)}.'\n            )\n        })\n'''
replace_once("app/apps/api/serializers.py", old_limits, new_limits)
replace_once(
    "app/apps/api/serializers.py",
    "        _check_seller_limit(tenant)\n\n        base = slugify(validated_data['name'])",
    "        base = slugify(validated_data['name'])",
)
replace_once(
    "app/apps/api/serializers.py",
    "        with transaction.atomic():\n            user = User.objects.create_user(\n",
    "        with transaction.atomic():\n            _check_seller_limit(tenant)\n            user = User.objects.create_user(\n",
)
replace_once(
    "app/apps/api/serializers.py",
    "        _check_seller_limit(tenant)\n\n        limit = PLAN_LIMITS.get(tenant.plan)\n        if limit is not None:\n",
    "        limit = _seller_limit(tenant)\n        if limit is not None:\n",
)
replace_once(
    "app/apps/api/serializers.py",
    "            if current + valid_count > limit:\n                raise serializers.ValidationError(\n                    f'Limite de vendedores do plano {tenant.plan} atingido ({limit}). '\n                    f'Voce tem {current} vendedores ativos e esta tentando importar '\n                    f'{valid_count}. Faca upgrade para adicionar mais vendedores.'\n                )\n",
    "            if current + valid_count > limit:\n                raise serializers.ValidationError(\n                    f'Limite de vendedores do plano {tenant.plan} atingido ({limit}). '\n                    f'Ativos: {current}; validos solicitados: {valid_count}; '\n                    f'capacidade restante: {max(0, limit - current)}.'\n                )\n",
)
replace_once(
    "app/apps/api/serializers.py",
    "                created.append({\n                    'username': username,\n                    'password': password,\n                    'name': name,\n",
    "                created.append({\n                    'username': username,\n                    'name': name,\n",
)

replace_once(
    "app/apps/webhooks/views.py",
    "    if webhook_secret:\n        x_sig = request.META.get('HTTP_X_SIGNATURE', '')\n",
    "    if not webhook_secret:\n        logger.critical('Billing webhook indisponivel: MP_WEBHOOK_SECRET nao configurado')\n        return JsonResponse({'error': 'Webhook unavailable'}, status=503)\n\n    if webhook_secret:\n        x_sig = request.META.get('HTTP_X_SIGNATURE', '')\n",
)

replace_once(
    "app/apps/webhooks/tasks.py",
    '''        except (MercadoPagoError, Exception) as e:\n            logger.warning("Erro ao buscar payment %s: %s", payment_id, e)\n            event.processed = True\n            event.skip_reason = str(e)[:500]\n            event.save(update_fields=['processed', 'skip_reason'])\n            return\n''',
    '''        except Exception:\n            logger.exception("Erro temporario/indeterminado ao buscar payment %s", payment_id)\n            raise\n''',
)
replace_once(
    "app/apps/webhooks/tasks.py",
    '''        except (MercadoPagoError, Exception) as e:\n            logger.warning("Erro ao buscar preapproval %s: %s", preapproval_id, e)\n            event.processed = True\n            event.skip_reason = str(e)[:500]\n            event.save(update_fields=['processed', 'skip_reason'])\n            return\n''',
    '''        except Exception:\n            logger.exception(\n                "Erro temporario/indeterminado ao buscar preapproval %s", preapproval_id\n            )\n            raise\n''',
)

bootstrap_block = '''    # ── Bootstrap superuser (sempre roda, idempotente) ─────\n    log "===== BOOTSTRAP SUPERUSER ====="\n    python -c "\nimport os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','app.config.settings.production')\nimport django; django.setup()\nfrom django.utils.crypto import get_random_string\nfrom app.apps.accounts.models import User\n\nif User.objects.filter(is_superuser=True).exists():\n    print('Superuser ja existe. Nada a fazer.')\nelse:\n    email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip()\n    password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '').strip()\n    if email and password and len(password) >= 12:\n        User.objects.create_superuser(username=email, email=email, password=password)\n        print(f'Superuser criado com email fornecido: {email}')\n    else:\n        password = get_random_string(20)\n        email = 'admin@querolink.local'\n        User.objects.create_superuser(username=email, email=email, password=password)\n        print('========================================')\n        print('ATENCAO: Superuser criado com senha aleatoria.')\n        print(f'  Email : {email}')\n        print(f'  Senha : {password}')\n        print('GUARDE ESSA SENHA. Ela nao sera exibida novamente.')\n        print('Configure DJANGO_SUPERUSER_EMAIL e DJANGO_SUPERUSER_PASSWORD no Coolify.')\n        print('========================================')\n"\n    log "Bootstrap superuser concluido."\n\n'''
replace_once("entrypoint.sh", bootstrap_block, "")

replace_once(
    "docker-compose.yml",
    "      web:\n        condition: service_healthy\n    healthcheck:\n",
    "    healthcheck:\n",
)
replace_once(
    "docker-compose.yml",
    "      web:\n        condition: service_healthy\n    deploy:\n",
    "    deploy:\n",
)

write_new(
    "app/apps/accounts/management/commands/bootstrap_superuser.py",
    '''import os\n\nfrom django.core.management.base import BaseCommand, CommandError\n\nfrom app.apps.accounts.models import User\n\n\nclass Command(BaseCommand):\n    help = 'Cria o primeiro superusuario usando variaveis de ambiente, sem expor a senha.'\n\n    def handle(self, *args, **options):\n        if User.objects.filter(is_superuser=True).exists():\n            self.stdout.write('Superuser ja existe. Nada a fazer.')\n            return\n\n        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip().lower()\n        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '')\n        if not email or not password:\n            raise CommandError(\n                'DJANGO_SUPERUSER_EMAIL e DJANGO_SUPERUSER_PASSWORD sao obrigatorios.'\n            )\n        if len(password) < 12:\n            raise CommandError('DJANGO_SUPERUSER_PASSWORD deve ter no minimo 12 caracteres.')\n\n        User.objects.create_superuser(username=email, email=email, password=password)\n        self.stdout.write(self.style.SUCCESS(f'Superuser criado: {email}'))\n''',
)

write_new(
    "app/apps/billing/migrations/0003_subscription_pending_cancel_gateway_subscription_id.py",
    '''from django.db import migrations, models\n\n\nclass Migration(migrations.Migration):\n    dependencies = [\n        ('billing', '0002_alter_subscription_plan_alter_subscription_status'),\n    ]\n\n    operations = [\n        migrations.AddField(\n            model_name='subscription',\n            name='pending_cancel_gateway_subscription_id',\n            field=models.CharField(\n                blank=True,\n                db_index=True,\n                help_text='Assinatura remota anterior aguardando cancelamento/reconciliacao',\n                max_length=100,\n                null=True,\n            ),\n        ),\n    ]\n''',
)

replace_once(
    "app/apps/billing/tests.py",
    "    def test_no_secret_configured_accepts(self):\n",
    "    def test_no_secret_configured_fails_closed(self):\n",
)
replace_once(
    "app/apps/billing/tests.py",
    "        self.assertEqual(resp.status_code, 200)\n\n    @override_settings(MP_WEBHOOK_SECRET='test_secret_key')\n    def test_uppercase_data_id_lowered_for_validation",
    "        self.assertEqual(resp.status_code, 503)\n        self.assertFalse(WebhookEvent.objects.filter(gateway='mercadopago').exists())\n\n    @override_settings(MP_WEBHOOK_SECRET='test_secret_key')\n    def test_uppercase_data_id_lowered_for_validation",
)
replace_once(
    "app/apps/billing/tests.py",
    "data={'plan': 'PRO', 'billing_cycle': 'YEARLY', 'payment_method': 'credit_card'},",
    "data={'plan': 'PRO', 'billing_cycle': 'YEARLY'},",
)
replace_once(
    "app/apps/billing/tests.py",
    "data={'plan': 'PRO', 'billing_cycle': 'MONTHLY', 'payment_method': 'credit_card'},",
    "data={'plan': 'PRO', 'billing_cycle': 'MONTHLY'},",
)

replace_once(
    ".github/workflows/ci.yml",
    "      - name: Check pending migrations\n",
    "      - name: Production deploy check\n        env:\n          DJANGO_SETTINGS_MODULE: app.config.settings.production\n          SECRET_KEY: ci-production-check-secret-key-not-for-production\n          DATABASE_URL: sqlite:///ci-production-check.sqlite3\n          REDIS_URL: redis://localhost:6379/0\n          FERNET_KEY: 9Q1h8YVxQb9tY4uJz8M3oAaT7mZg0X8D8FH5g7T2YZY=\n          ALLOWED_HOSTS: localhost\n          MP_ACCESS_TOKEN: ''\n          MP_WEBHOOK_SECRET: ''\n        run: python manage.py check --deploy\n\n      - name: Check pending migrations\n",
)

replace_once(
    ".env.example",
    "# Senha do superusuário. Troque após o primeiro login.\nDJANGO_SUPERUSER_PASSWORD=\n",
    "# Senha do superusuário. Não é criada automaticamente no startup.\n# Após o primeiro deploy, execute manualmente: python manage.py bootstrap_superuser\n# A senha nunca é impressa nos logs.\nDJANGO_SUPERUSER_PASSWORD=\n",
)

# Minimal factual dossie updates.
replace_once(
    "CONTEXTO_MERITO.md",
    "**Roadmap pós-launch:** Git+CI (prioridade nº 1 — regressões só serão estruturalmente resolvidas com isso)",
    "**Roadmap pós-launch:** ampliar CI com PostgreSQL real e testes ofensivos/chaos",
)

residual = ROOT / "user"
if residual.exists():
    residual.unlink()
    print("removed residual user file")

print("all guarded patches applied")
