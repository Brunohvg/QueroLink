from django.db import models
from app.apps.orders.models import PaymentLink
from app.apps.accounts.fields import compute_hash


class LinkClick(models.Model):
    payment_link = models.ForeignKey(PaymentLink, on_delete=models.CASCADE, related_name='clicks')
    ip_hash = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    user_agent = models.TextField(blank=True, null=True)
    clicked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['payment_link', 'clicked_at']),
        ]

    def set_ip_address(self, ip):
        self.ip_hash = compute_hash(ip) if ip else None

    def __str__(self):
        return f"Click on {self.payment_link} at {self.clicked_at}"
