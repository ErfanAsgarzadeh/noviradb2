from django.contrib import admin

from .models import BarcodeResolutionAudit, ExportJob, ImportJob, ImportJobRow, IntegrationOutboxEvent, Notification, NotificationPreference, NotificationSubscription


@admin.register(Notification, NotificationPreference, NotificationSubscription, BarcodeResolutionAudit, ImportJob, ImportJobRow, ExportJob, IntegrationOutboxEvent)
class ReadOnlyReleaseAdmin(admin.ModelAdmin):
    readonly_fields = []

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields] if obj else []
