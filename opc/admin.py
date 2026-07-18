from django.contrib import admin

from .models import OPCDiagram, OPCEdge, OPCNode


class OPCNodeInline(admin.TabularInline):
    model = OPCNode
    extra = 0
    readonly_fields = ('created_at', 'updated_at')


class OPCEdgeInline(admin.TabularInline):
    model = OPCEdge
    fk_name = 'diagram'
    extra = 0
    readonly_fields = ('created_at', 'updated_at')


@admin.register(OPCDiagram)
class OPCDiagramAdmin(admin.ModelAdmin):
    list_display = ('title', 'part_code', 'revision', 'item_revision', 'status', 'effective_from', 'released_at')
    list_filter = ('status', 'effective_from', 'effective_to')
    search_fields = ('title', 'part_code', 'part_name', 'revision', 'item_revision__item__item_code')
    readonly_fields = ('submitted_at', 'approved_at', 'released_at', 'superseded_at', 'created_at', 'updated_at')
    inlines = [OPCNodeInline, OPCEdgeInline]


admin.site.register(OPCNode)
admin.site.register(OPCEdge)
