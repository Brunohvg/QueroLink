import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser

class Tenant(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_name = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=14, unique=True, blank=True, null=True)
    pagarme_api_key = models.CharField(max_length=255, blank=True, null=True)
    whatsapp_instance_id = models.CharField(max_length=100, blank=True, null=True)
    whatsapp_token = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.company_name

class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        MANAGER = 'MANAGER', 'Manager'
        FINANCEIRO = 'FINANCEIRO', 'Financeiro'
        SELLER = 'SELLER', 'Vendedor'
    
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True, related_name='users')
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MANAGER)
    
    def __str__(self):
        return self.username
