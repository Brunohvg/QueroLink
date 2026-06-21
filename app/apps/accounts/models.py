import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.text import slugify
from .fields import EncryptedCharField


class Tenant(models.Model):
    class Plan(models.TextChoices):
        ESSENCIAL = 'ESSENCIAL', 'Essencial (ate 5 vendedores)'
        PROFISSIONAL = 'PROFISSIONAL', 'Profissional (ate 15 vendedores)'
        PLUS = 'PLUS', 'Plus (ate 30 vendedores)'
        ENTERPRISE = 'ENTERPRISE', 'Enterprise (ilimitado)'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_name = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=14, unique=True, blank=True, null=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    pagarme_api_key = EncryptedCharField(max_length=255, blank=True, null=True)
    whatsapp_instance_id = models.CharField(max_length=100, blank=True, null=True)
    whatsapp_token = EncryptedCharField(max_length=255, blank=True, null=True)
    default_commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)
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
        super().save(*args, **kwargs)

    def __str__(self):
        return self.company_name

    @property
    def pagarme_configured(self):
        return bool(self.pagarme_api_key)

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
