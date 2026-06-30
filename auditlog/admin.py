from django.contrib import admin
from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'actor_username', 'category', 'action',
                    'target_model', 'target_id', 'success', 'request_path')
    list_filter = ('category', 'action', 'success', 'target_model')
    search_fields = ('actor_username', 'action', 'target_id', 'target_repr',
                     'request_path', 'error_message')
    readonly_fields = [f.name for f in AuditEvent._meta.concrete_fields]
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # حتی ادمین هم نباید لاگ را پاک کند
        return False
