from django.contrib import admin

from .models import (
    ControlledDocument,
    ControlledDocumentRevision,
    EngineeringChangeObjectLink,
    EngineeringChangeOrder,
    EngineeringChangeRequest,
    InventoryBalance,
    InventoryReservation,
    InventoryTransaction,
    ItemPlanningPolicy,
    MachineAsset,
    MRPDemandSnapshot,
    MRPPegging,
    MRPRecommendation,
    MRPRequirement,
    MRPRun,
    MRPSupplySnapshot,
    OPCDiagram,
    OPCEdge,
    OPCNode,
    OPCNodeDocumentRequirement,
    OPCOperationMaterialAllocation,
    OPCToolingRequirement,
    Plant,
    PlanningCalendar,
    PlanningDemand,
    ProcessDefinition,
    PurchaseRequisition,
    ScheduledSupply,
    StorageLocation,
    ToolingDefinition,
    Warehouse,
    WorkCenter,
)


class OPCToolingRequirementInline(admin.TabularInline):
    model = OPCToolingRequirement
    extra = 0
    autocomplete_fields = ('tooling_definition',)


class OPCOperationMaterialAllocationInline(admin.TabularInline):
    model = OPCOperationMaterialAllocation
    extra = 0
    autocomplete_fields = ('component_item_revision',)


class OPCNodeDocumentRequirementInline(admin.TabularInline):
    model = OPCNodeDocumentRequirement
    extra = 0
    autocomplete_fields = ('document_revision',)


class OPCNodeInline(admin.TabularInline):
    model = OPCNode
    extra = 0
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('process_definition', 'plant', 'work_center', 'machine_asset')


class OPCEdgeInline(admin.TabularInline):
    model = OPCEdge
    fk_name = 'diagram'
    extra = 0
    readonly_fields = ('created_at', 'updated_at')


@admin.register(OPCDiagram)
class OPCDiagramAdmin(admin.ModelAdmin):
    list_display = ('title', 'part_code', 'revision', 'item_revision', 'manufacturing_bom_revision', 'status', 'effective_from', 'released_at')
    list_filter = ('status', 'effective_from', 'effective_to')
    search_fields = ('title', 'part_code', 'part_name', 'revision', 'item_revision__item__item_code')
    readonly_fields = ('submitted_at', 'approved_at', 'released_at', 'superseded_at', 'created_at', 'updated_at')
    inlines = [OPCNodeInline, OPCEdgeInline]


@admin.register(Plant)
class PlantAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'active')
    list_filter = ('active',)
    search_fields = ('code', 'name')


@admin.register(WorkCenter)
class WorkCenterAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'plant', 'capacity_classification', 'active')
    list_filter = ('active', 'capacity_classification', 'plant')
    search_fields = ('code', 'name', 'plant__code', 'plant__name')
    autocomplete_fields = ('plant',)


@admin.register(MachineAsset)
class MachineAssetAdmin(admin.ModelAdmin):
    list_display = ('asset_code', 'name', 'plant', 'work_center', 'machine_type', 'active')
    list_filter = ('active', 'machine_type', 'plant', 'work_center')
    search_fields = ('asset_code', 'name', 'machine_type', 'manufacturer', 'model', 'serial_number')
    autocomplete_fields = ('plant', 'work_center')


@admin.register(ProcessDefinition)
class ProcessDefinitionAdmin(admin.ModelAdmin):
    list_display = ('process_code', 'name', 'execution_classification', 'special_process', 'inspection_required', 'active')
    list_filter = ('active', 'execution_classification', 'special_process', 'inspection_required')
    search_fields = ('process_code', 'name', 'description')


@admin.register(ToolingDefinition)
class ToolingDefinitionAdmin(admin.ModelAdmin):
    list_display = ('tooling_code', 'name', 'category', 'reusable', 'consumable', 'calibration_required', 'active')
    list_filter = ('active', 'category', 'reusable', 'consumable', 'calibration_required')
    search_fields = ('tooling_code', 'name', 'description')


@admin.register(OPCNode)
class OPCNodeAdmin(admin.ModelAdmin):
    list_display = ('label', 'diagram', 'node_type', 'operation_number', 'process_definition', 'plant', 'work_center', 'machine_asset')
    list_filter = ('node_type', 'execution_type', 'plant', 'work_center')
    search_fields = ('label', 'part_code', 'process_code', 'station', 'process_definition__process_code')
    autocomplete_fields = ('diagram', 'process_definition', 'plant', 'work_center', 'machine_asset')
    inlines = [OPCToolingRequirementInline, OPCOperationMaterialAllocationInline, OPCNodeDocumentRequirementInline]


admin.site.register(OPCEdge)


@admin.register(ControlledDocument)
class ControlledDocumentAdmin(admin.ModelAdmin):
    list_display = ('document_number', 'title', 'document_type', 'active')
    list_filter = ('document_type', 'active')
    search_fields = ('document_number', 'title')


@admin.register(ControlledDocumentRevision)
class ControlledDocumentRevisionAdmin(admin.ModelAdmin):
    list_display = ('document', 'revision', 'status', 'effective_from', 'released_at')
    list_filter = ('status', 'effective_from')
    search_fields = ('document__document_number', 'document__title', 'revision')
    autocomplete_fields = ('document', 'superseded_by')


admin.site.register(EngineeringChangeRequest)
admin.site.register(EngineeringChangeOrder)
admin.site.register(EngineeringChangeObjectLink)


@admin.register(PlanningCalendar)
class PlanningCalendarAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'timezone', 'active')
    list_filter = ('active',)
    search_fields = ('code', 'name')


@admin.register(ItemPlanningPolicy)
class ItemPlanningPolicyAdmin(admin.ModelAdmin):
    list_display = ('item_revision', 'procurement_type', 'planning_method', 'default_warehouse', 'safety_stock', 'minimum_stock', 'lot_sizing_method', 'active')
    list_filter = ('procurement_type', 'planning_method', 'lot_sizing_method', 'active')
    search_fields = ('item_revision__item__item_code', 'item_revision__revision')
    autocomplete_fields = ('item_revision', 'default_warehouse')


@admin.register(PlanningDemand)
class PlanningDemandAdmin(admin.ModelAdmin):
    list_display = ('demand_number', 'item_revision', 'quantity', 'required_date', 'warehouse', 'status', 'demand_version')
    list_filter = ('status', 'demand_type', 'priority', 'warehouse')
    search_fields = ('demand_number', 'item_revision__item__item_code', 'source_reference')
    readonly_fields = ('demand_version', 'approved_at', 'cancelled_at', 'created_at', 'updated_at')
    autocomplete_fields = ('item_revision', 'warehouse', 'plant', 'created_by', 'approved_by', 'cancelled_by')


@admin.register(ScheduledSupply)
class ScheduledSupplyAdmin(admin.ModelAdmin):
    list_display = ('supply_number', 'item_revision', 'quantity', 'expected_date', 'warehouse', 'status', 'firm', 'supply_version')
    list_filter = ('status', 'supply_type', 'firm', 'warehouse')
    search_fields = ('supply_number', 'item_revision__item__item_code', 'source_reference')
    readonly_fields = ('supply_version', 'created_at', 'updated_at')
    autocomplete_fields = ('item_revision', 'warehouse', 'created_by')


class MRPDemandSnapshotInline(admin.TabularInline):
    model = MRPDemandSnapshot
    extra = 0
    readonly_fields = [field.name for field in MRPDemandSnapshot._meta.fields]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class MRPRequirementInline(admin.TabularInline):
    model = MRPRequirement
    extra = 0
    readonly_fields = [field.name for field in MRPRequirement._meta.fields]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(MRPRun)
class MRPRunAdmin(admin.ModelAdmin):
    list_display = ('run_number', 'status', 'horizon_start', 'horizon_end', 'warehouse', 'input_checksum', 'result_checksum', 'created_at')
    list_filter = ('status', 'warehouse', 'plant')
    search_fields = ('run_number', 'input_checksum', 'result_checksum')
    readonly_fields = [field.name for field in MRPRun._meta.fields]
    inlines = [MRPDemandSnapshotInline, MRPRequirementInline]

    def has_add_permission(self, request):
        return False


@admin.register(MRPRecommendation)
class MRPRecommendationAdmin(admin.ModelAdmin):
    list_display = ('recommendation_number', 'recommendation_type', 'item_revision', 'quantity', 'planned_release_date', 'status', 'recommendation_version')
    list_filter = ('recommendation_type', 'status', 'procurement_type')
    search_fields = ('recommendation_number', 'item_revision__item__item_code')
    readonly_fields = ('input_checksum', 'reviewed_at', 'approved_at', 'converted_at')
    autocomplete_fields = ('mrp_run', 'item_revision', 'warehouse', 'reviewed_by', 'approved_by', 'converted_by')


admin.site.register(MRPSupplySnapshot)
admin.site.register(MRPPegging)
admin.site.register(PurchaseRequisition)


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'warehouse_type', 'plant', 'active')
    list_filter = ('warehouse_type', 'active', 'plant')
    search_fields = ('code', 'name', 'description')
    autocomplete_fields = ('plant',)


@admin.register(StorageLocation)
class StorageLocationAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'warehouse', 'location_type', 'inventory_enabled', 'quarantine', 'active')
    list_filter = ('warehouse', 'location_type', 'inventory_enabled', 'quarantine', 'active')
    search_fields = ('code', 'name', 'warehouse__code')
    autocomplete_fields = ('warehouse', 'parent')


@admin.register(InventoryBalance)
class InventoryBalanceAdmin(admin.ModelAdmin):
    list_display = ('item_revision', 'warehouse', 'location', 'lot', 'serial', 'stock_status', 'on_hand_quantity', 'reserved_quantity', 'balance_version')
    list_filter = ('warehouse', 'stock_status')
    search_fields = ('item_revision__item__item_code', 'warehouse__code', 'location__code', 'lot__lot_number', 'serial__serial_number')
    readonly_fields = ('item_revision', 'warehouse', 'location', 'lot', 'serial', 'stock_status', 'ownership', 'unit', 'on_hand_quantity', 'reserved_quantity', 'balance_version', 'last_transaction', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = ('transaction_number', 'transaction_type', 'item_revision', 'quantity', 'unit', 'production_order', 'actor', 'server_recorded_at')
    list_filter = ('transaction_type', 'source_warehouse', 'destination_warehouse', 'server_recorded_at')
    search_fields = ('transaction_number', 'item_revision__item__item_code', 'production_order__order_number', 'source_lot__lot_number', 'source_serial__serial_number', 'destination_lot__lot_number', 'destination_serial__serial_number')
    readonly_fields = [field.name for field in InventoryTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False if obj else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InventoryReservation)
class InventoryReservationAdmin(admin.ModelAdmin):
    list_display = ('production_order', 'material_requirement', 'item_revision', 'warehouse', 'location', 'quantity', 'issued_quantity', 'released_quantity', 'status', 'reservation_version')
    list_filter = ('status', 'warehouse', 'location')
    search_fields = ('production_order__order_number', 'item_revision__item__item_code', 'warehouse__code', 'location__code')
    readonly_fields = ('production_order', 'material_requirement', 'item_revision', 'balance', 'warehouse', 'location', 'lot', 'serial', 'quantity', 'released_quantity', 'issued_quantity', 'unit', 'status', 'reservation_version', 'reserved_by', 'reserved_at', 'last_transaction', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
