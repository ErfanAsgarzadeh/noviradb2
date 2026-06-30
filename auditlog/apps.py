from django.apps import AppConfig


class AuditlogConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'auditlog'
    verbose_name = 'لاگ سیستم'

    def ready(self):
        # signals را load می‌کند
        from . import signals  # noqa: F401
