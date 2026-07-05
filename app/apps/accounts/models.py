import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.contrib.auth.models import AbstractUser
from django.utils.text import slugify
from django.utils import timezone
from .fields import EncryptedCharField, compute_hash


class Tenant(models.Model):
    class Plan(models.TextChoices):
        STARTER = 'STARTER', 'Starter'
        PRO = 'PRO', 'Pro'
        BUSINESS = 'BUSINESS', 'Business'
        ENTERPRISE = 'ENTERPRISE', 'Enterprise'  # legado

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_name = models.CharField(max_length=255)
    cnpj = EncryptedCharField(max_length=600, blank=True, null=True)
    cnpj_hash = models.CharField(max_length=64, blank=True, null=True, unique=True, db_index=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    pagarme_api_key = EncryptedCharField(max_length=600, blank=True, null=True)
    pagarme_webhook_username = EncryptedCharField(max_length=600, blank=True, null=True)
    pagarme_webhook_password = EncryptedCharField(max_length=600, blank=True, null=True)
    whatsapp_instance_id = models.CharField(max_length=100, blank=True, null=True)
    whatsapp_token = EncryptedCharField(max_length=600, blank=True, null=True)
    correios_usuario = models.CharField(max_length=100, blank=True, null=True,
        help_text='Usuario Meu Correios (idCorreios)')
    correios_codigo_acesso = EncryptedCharField(max_length=255, blank=True, null=True,
        help_text='Codigo de acesso a API CWS')
    correios_contrato = models.CharField(max_length=30, blank=True, null=True)
    correios_cartao = models.CharField(max_length=30, blank=True, null=True,
        help_text='Cartao de postagem (opcional)')
    default_commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)
    link_expires_in = models.PositiveIntegerField(
        default=1200,
        help_text="Tempo de expiracao do link de pagamento em minutos (padrao 1200 = 20h)",
    )
    pix_enabled = models.BooleanField(
        default=True,
        help_text="Incluir PIX como metodo de pagamento nos links",
    )
    is_active = models.BooleanField(default=True)
    billing_email = models.EmailField(blank=True, null=True)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.STARTER)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    billing_cycle = models.CharField(max_length=10, choices=[('MONTHLY', 'Mensal'), ('YEARLY', 'Anual')], default='MONTHLY')
    daily_reminder_enabled = models.BooleanField(default=False)
    daily_reminder_time = models.TimeField(default='20:00')
    ranking_visible_to_sellers = models.BooleanField(
        default=False,
        help_text="Exibe nomes dos colegas no ranking do app (valores nunca sao exibidos)",
    )
    accountant_email = models.EmailField(
        blank=True, null=True,
        help_text='E-mail da contabilidade para envio automatico do fechamento mensal',
    )
    accountant_auto_send = models.BooleanField(
        default=False,
        help_text='Enviar automaticamente o pacote contabil quando todas as comissoes da competencia forem pagas',
    )
    store_cep = models.CharField(max_length=9, blank=True, null=True,
        help_text='CEP de origem dos envios (loja)')
    freight_adjustment_percent = models.IntegerField(default=0,
        help_text='Calibracao da estimativa em % (-50 a +100)')
    freight_presets = models.JSONField(default=list, blank=True,
        help_text='Embalagens frequentes: [{"name": "Caixa P", "weight_grams": 500}]')
    motoboy_enabled = models.BooleanField(default=False)
    motoboy_price_per_km_cents = models.PositiveIntegerField(default=200)
    motoboy_min_price_cents = models.PositiveIntegerField(default=800)
    motoboy_max_km = models.PositiveIntegerField(default=15,
        help_text='Raio maximo de entrega (km)')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.company_name)
            slug = base_slug
            counter = 1
            while Tenant.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                counter += 1
                slug = f"{base_slug}-{counter}"
            self.slug = slug
        if self.cnpj and (not self.cnpj_hash or self._cnpj_changed()):
            self.cnpj_hash = compute_hash(self.cnpj)

        _field_changed = False
        if self.pk:
            try:
                old = Tenant.objects.get(pk=self.pk)
                _field_changed = (
                    old.plan != self.plan
                    or old.is_active != self.is_active
                    or old.trial_ends_at != self.trial_ends_at
                )
            except Tenant.DoesNotExist:
                _field_changed = True

        super().save(*args, **kwargs)

        if _field_changed:
            cache.delete(f'tenant_operational:{self.uuid}')

    def _cnpj_changed(self):
        if not self.pk:
            return True
        try:
            old = Tenant.objects.get(pk=self.pk)
            return old.cnpj != self.cnpj
        except Tenant.DoesNotExist:
            return True

    def clean(self):
        if self.freight_adjustment_percent < -50 or self.freight_adjustment_percent > 100:
            raise ValidationError({'freight_adjustment_percent': 'Ajuste deve estar entre -50 e +100.'})
        if self.freight_presets:
            if not isinstance(self.freight_presets, list):
                raise ValidationError({'freight_presets': 'Presets devem ser uma lista.'})
            if len(self.freight_presets) > 6:
                raise ValidationError({'freight_presets': 'Maximo de 6 embalagens.'})
            for i, p in enumerate(self.freight_presets):
                if not isinstance(p.get('name'), str) or len(p.get('name', '')) > 20:
                    raise ValidationError({'freight_presets': f'Item {i+1}: nome invalido (max 20 chars).'})
                w = p.get('weight_grams', 0)
                if not isinstance(w, int) or w < 50 or w > 30000:
                    raise ValidationError({'freight_presets': f'Item {i+1}: peso deve ser 50-30000g.'})

    def __str__(self):
        return self.company_name

    @property
    def pagarme_configured(self):
        if not self.pagarme_api_key:
            return False
        from app.services.gateway.pagar_me import _normalize_api_key
        key = _normalize_api_key(self.pagarme_api_key)
        return bool(key and key.startswith('sk_'))

    @property
    def whatsapp_configured(self):
        return bool(self.whatsapp_token and self.whatsapp_instance_id)

    @property
    def correios_cws_enabled(self):
        return bool(self.correios_usuario and self.correios_codigo_acesso)

    @property
    def is_trial_expired(self):
        return bool(self.trial_ends_at and self.trial_ends_at < timezone.now())


def tenant_operational(tenant):
    if not tenant.is_active:
        return False
    if tenant.trial_ends_at and tenant.trial_ends_at > timezone.now():
        return True
    from app.apps.billing.models import Subscription
    try:
        sub = Subscription.objects.get(tenant=tenant)
        if sub.status == 'ACTIVE':
            return True
        if sub.status == 'PAST_DUE' and sub.current_period_end:
            from datetime import timedelta
            if sub.current_period_end > timezone.now() - timedelta(days=5):
                return True
        return False
    except Subscription.DoesNotExist:
        return not tenant.is_trial_expired


def tenant_has_feature(tenant, feature_name):
    from django.conf import settings
    features = getattr(settings, 'PLAN_FEATURES', {}).get(tenant.plan, {})
    return features.get(feature_name, False)


def mark_onboarding_step(tenant, step_name):
    ob, _ = OnboardingProgress.objects.get_or_create(tenant=tenant)
    if ob.completed_at or ob.dismissed:
        return
    if not getattr(ob, step_name):
        setattr(ob, step_name, True)
        if all([ob.step_whatsapp, ob.step_sellers, ob.step_commission_rate,
                ob.step_first_invite, ob.step_first_sale]):
            ob.completed_at = timezone.now()
        ob.save()


class OnboardingProgress(models.Model):
    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name='onboarding')
    step_whatsapp = models.BooleanField(default=False)
    step_sellers = models.BooleanField(default=False)
    step_commission_rate = models.BooleanField(default=False)
    step_first_invite = models.BooleanField(default=False)
    step_first_sale = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    dismissed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Onboarding {self.tenant.company_name} ({self.completed_steps}/5)'

    @property
    def completed_steps(self):
        return sum([
            self.step_whatsapp, self.step_sellers, self.step_commission_rate,
            self.step_first_invite, self.step_first_sale,
        ])


class User(AbstractUser):
    # Role.ADMIN é admin DENTRO do tenant (gerencia vendedores, fechamento, etc. da propria loja).
    # is_superuser=True é admin do SaaS inteiro (Bruno) — acesso a TODOS os tenants e Django Admin geral.
    # NUNCA conceder is_superuser=True para usuarios criados via autocadastro publico.
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        MANAGER = 'MANAGER', 'Manager'
        FINANCEIRO = 'FINANCEIRO', 'Financeiro'
        SELLER = 'SELLER', 'Vendedor'
    
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True, related_name='users')
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MANAGER)
    
    def __str__(self):
        return self.username

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['email'], name='unique_user_email'),
        ]
        indexes = [
            models.Index(fields=['tenant', 'role'], name='user_tenant_role_idx'),
            models.Index(fields=['role'], name='user_role_idx'),
        ]
