from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'app.apps.billing'
    verbose_name = 'Billing'

    def ready(self):
        from . import checks  # noqa: F401
