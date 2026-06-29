import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.text import slugify
from .fields import EncryptedCharField, compute_hash


class Tenant(models.Model):
    class Plan(models.TextChoices):
        ESSENCIAL = 'ESSENCIAL', 'Essencial (ate 5 vendedores)'
        PROFISSIONAL = 'PROFISSIONAL', 'Profissional (ate 15 vendedores)'
        PLUS = 'PLUS', 'Plus (ate 30 vendedores)'
        ENTERPRISE = 'ENTERPRISE', 'Enterprise (ilimitado)'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_name = models.CharField(max_length=255)
    cnpj = EncryptedCharField(max_length=600, blank=True, null=True)
    cnpj_hash = models.CharField(max_length=64, blank=True, null=True, unique=True, db_index=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    pagarme_api_key = EncryptedCharField(max_length=600, blank=True, null=True)
    whatsapp_instance_id = models.CharField(max_length=100, blank=True, null=True)
    whatsapp_token = EncryptedCharField(max_length=600, blank=True, null=True)
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
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.ESSENCIAL)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    billing_cycle = models.CharField(max_length=10, choices=[('MONTHLY', 'Mensal'), ('YEARLY', 'Anual')], default='MONTHLY')
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
        super().save(*args, **kwargs)

    def _cnpj_changed(self):
        if not self.pk:
            return True
        try:
            old = Tenant.objects.get(pk=self.pk)
            return old.cnpj != self.cnpj
        except Tenant.DoesNotExist:
            return True

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
        indexes = [
            models.Index(fields=['tenant', 'role'], name='user_tenant_role_idx'),
            models.Index(fields=['role'], name='user_role_idx'),
        ]
