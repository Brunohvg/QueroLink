from django.db import models
from app.apps.orders.models import PaymentLink

class LinkClick(models.Model):
    payment_link = models.ForeignKey(PaymentLink, on_delete=models.CASCADE, related_name='clicks')
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    clicked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['payment_link', 'clicked_at']),
        ]

    def __str__(self):
        return f"Click on {self.payment_link} at {self.clicked_at}"
