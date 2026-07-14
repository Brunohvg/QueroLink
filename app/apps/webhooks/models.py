from django.db import models
from django.db.models import Q

class WebhookEvent(models.Model):
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'Received'
        PROCESSING = 'PROCESSING', 'Processing'
        PROCESSED = 'PROCESSED', 'Processed'
        FAILED = 'FAILED', 'Failed'
        SKIPPED = 'SKIPPED', 'Skipped'

    gateway = models.CharField(max_length=50)
    gateway_event_id = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="ID unico do evento no gateway (ex: evt_xxx no Pagar.me)",
    )
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RECEIVED,
    )
    processing_error = models.TextField(blank=True, null=True)
    skip_reason = models.TextField(blank=True, null=True, help_text="Motivo do skip (evento estrangeiro)")
    tenant = models.ForeignKey(
        'accounts.Tenant', on_delete=models.CASCADE,
        null=True, blank=True, related_name='webhook_events',
    )
    received_at = models.DateTimeField(auto_now_add=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['gateway', 'received_at']),
            models.Index(fields=['processed']),
            models.Index(fields=['gateway', 'status']),
            models.Index(fields=['gateway_event_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['gateway', 'gateway_event_id'],
                condition=Q(gateway_event_id__isnull=False),
                name='webhook_event_gateway_event_id_uniq',
            ),
        ]

    def __str__(self):
        return f"{self.gateway} Event - {self.received_at}"
