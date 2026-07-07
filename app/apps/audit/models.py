from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    tenant = models.ForeignKey('accounts.Tenant', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    action = models.CharField(max_length=255)
    model_name = models.CharField(max_length=100, blank=True, null=True)
    object_id = models.CharField(max_length=255, blank=True, null=True)
    changes = models.JSONField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['tenant', 'created_at']),
            models.Index(fields=['action']),
            models.Index(fields=['model_name', 'object_id']),
        ]

    def __str__(self):
        return f"{self.action} at {self.created_at}"
