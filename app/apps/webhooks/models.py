from django.db import models

class WebhookEvent(models.Model):
    gateway = models.CharField(max_length=50)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True, null=True)
    tenant = models.ForeignKey(
        'accounts.Tenant', on_delete=models.CASCADE,
        null=True, blank=True, related_name='webhook_events',
    )
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['gateway', 'received_at']),
            models.Index(fields=['processed']),
        ]

    def __str__(self):
        return f"{self.gateway} Event - {self.received_at}"
