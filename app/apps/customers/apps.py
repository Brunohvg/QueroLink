from django.apps import AppConfig


class CustomersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.apps.customers"
    verbose_name = "Clientes"

    def ready(self):
        from . import signals  # noqa: F401
