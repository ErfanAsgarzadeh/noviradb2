from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from enterprise_items.serializers import ItemRevisionSerializer

from .models import (
    CalendarException,
    CapacityBucket,
    ControlledDocument,
    ControlledDocumentRevision,
    EngineeringChangeObjectLink,
    EngineeringChangeOrder,
    EngineeringChangeRequest,
    LaborSkill,
    MachineAsset,
    MachineCapacity,
    MachineDowntimeEvent,
    MachineMaintenanceProfile,
    MaintainableAsset,
    MaintenanceChecklistResult,
    MaintenanceEvent,
    MaintenanceRequest,
    MaintenanceSparePartIssue,
    MaintenanceSparePartRequirement,
    MaintenanceTaskChecklistItem,
    MaintenanceTaskTemplate,
    MaintenanceWorkOrder,
    PreventiveMaintenancePlan,
    AssetMeter,
    AssetMeterReading,
    FailureCode,
    CauseCode,
    RemedyCode,
    InspectionApplicability,
    InspectionCharacteristic,
    InspectionExecution,
    InspectionMeasurement,
    InspectionPlan,
    InspectionPlanRevision,
    InspectionSample,
    InventoryBalance,
    InventoryReservation,
    InventoryTransaction,
    ItemPlanningPolicy,
    ManufacturingExecutionEvent,
    ManufacturingGenealogyLink,
    ManufacturingOperation,
    ManufacturingOperationEligibleMachine,
    ManufacturingOperationPrecedence,
    NonconformanceRecord,
    OperationExecution,
    OperationExecutionCycle,
    OPCDiagram,
    OPCEdge,
    OPCNode,
    OPCNodeDocumentRequirement,
    OPCOperationMaterialAllocation,
    OPCToolingRequirement,
    OPCValidationEvidence,
    MRPDemandSnapshot,
    MRPPegging,
    MRPRecommendation,
    MRPRequirement,
    MRPRun,
    MRPSupplySnapshot,
    Plant,
    PlanningCalendar,
    PlanningDemand,
    ProductionDocumentAcknowledgement,
    ProductionDocumentRequirement,
    ProductionInspectionRequirement,
    ProductionLot,
    ProductionMaterialConsumption,
    ProductionMaterialRequirement,
    ProductionOrder,
    ProductionOrderValidationEvidence,
    ProductionSerial,
    ProductionToolingRequirement,
    ProductionToolingUsage,
    ApprovedSupplierItem,
    CostRate,
    CostSnapshot,
    CurrencyRatePolicy,
    OperationalKPISnapshot,
    ProcurementEvent,
    PurchaseOrder,
    PurchaseOrderDeliverySchedule,
    PurchaseOrderLine,
    PurchaseReceipt,
    PurchaseReceiptLine,
    PurchaseReturn,
    PurchaseRequisition,
    QuotationComparisonSnapshot,
    QualityDisposition,
    QualityEvent,
    QualityHold,
    RequestForQuotation,
    RFQLine,
    RFQSupplierInvitation,
    SourcingDecision,
    SourcingDecisionLine,
    Supplier,
    SupplierCapability,
    SupplierContact,
    SupplierPerformanceSnapshot,
    SupplierQuotation,
    SupplierQuotationLine,
    SupplierSite,
    ProcessDefinition,
    ResourceCalendar,
    ScheduledOperationAssignment,
    SchedulingCalendarSnapshot,
    SchedulingException,
    SchedulingOperationSnapshot,
    SchedulingPolicy,
    SchedulingRun,
    StorageLocation,
    ScheduledSupply,
    Shift,
    ToolingDefinition,
    Warehouse,
    WorkCenter,
    WorkCenterCapacity,
    WorkCenterLaborCapacity,
)
from .execution_services import allowed_execution_actions, progress_rollup
from .inventory_services import material_reconciliation, operation_material_summary, order_material_summary
from .mrp_services import recommendation_freshness
from .production_services import allowed_order_actions
from .quality_services import allowed_inspection_actions, allowed_ncr_actions, aggregate_inspection, order_quality_summary
from .resource_validation import validate_node_resource_assignments
from .scheduling_services import scheduling_freshness
from .services import ensure_diagram_editable, ensure_edge_editable, ensure_node_editable


class PlantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plant
        fields = ['id', 'code', 'name', 'description', 'address', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class WorkCenterSerializer(serializers.ModelSerializer):
    plant_code = serializers.CharField(source='plant.code', read_only=True)
    plant_name = serializers.CharField(source='plant.name', read_only=True)

    class Meta:
        model = WorkCenter
        fields = ['id', 'plant', 'plant_code', 'plant_name', 'code', 'name', 'description', 'capacity_classification', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'plant_code', 'plant_name', 'created_at', 'updated_at']


class MachineAssetSerializer(serializers.ModelSerializer):
    plant_code = serializers.CharField(source='plant.code', read_only=True)
    work_center_code = serializers.CharField(source='work_center.code', read_only=True)
    work_center_name = serializers.CharField(source='work_center.name', read_only=True)

    class Meta:
        model = MachineAsset
        fields = ['id', 'plant', 'plant_code', 'work_center', 'work_center_code', 'work_center_name', 'asset_code', 'name', 'machine_type', 'manufacturer', 'model', 'serial_number', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'plant_code', 'work_center_code', 'work_center_name', 'created_at', 'updated_at']


class ProcessDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessDefinition
        fields = ['id', 'process_code', 'name', 'description', 'execution_classification', 'default_setup_time_hours', 'default_run_time_per_unit_hours', 'special_process', 'inspection_required', 'preferred_work_center_category', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ToolingDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolingDefinition
        fields = ['id', 'tooling_code', 'name', 'description', 'category', 'reusable', 'consumable', 'calibration_required', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class OPCToolingRequirementSerializer(serializers.ModelSerializer):
    tooling_code = serializers.CharField(source='tooling_definition.tooling_code', read_only=True)
    tooling_name = serializers.CharField(source='tooling_definition.name', read_only=True)
    tooling_active = serializers.BooleanField(source='tooling_definition.active', read_only=True)
    tooling_category = serializers.CharField(source='tooling_definition.category', read_only=True)

    class Meta:
        model = OPCToolingRequirement
        fields = ['id', 'tooling_definition', 'tooling_code', 'tooling_name', 'tooling_active', 'tooling_category', 'quantity', 'mandatory', 'notes', 'sequence']
        read_only_fields = ['id', 'tooling_code', 'tooling_name', 'tooling_active', 'tooling_category']


class OPCOperationMaterialAllocationSerializer(serializers.ModelSerializer):
    bom_line_sequence = serializers.IntegerField(source='bom_line.sequence', read_only=True, default=None)
    bom_line_quantity = serializers.DecimalField(source='bom_line.quantity', max_digits=18, decimal_places=6, read_only=True, default=None)
    bom_line_unit = serializers.CharField(source='bom_line.unit', read_only=True, default=None)
    bom_line_scrap_percent = serializers.DecimalField(source='bom_line.scrap_percent', max_digits=7, decimal_places=3, read_only=True, default=None)
    component_item_code = serializers.CharField(source='component_item_revision.item.item_code', read_only=True)
    component_revision = serializers.CharField(source='component_item_revision.revision', read_only=True)
    component_title = serializers.CharField(source='component_item_revision.title', read_only=True, default='')

    class Meta:
        model = OPCOperationMaterialAllocation
        fields = [
            'id', 'bom_line', 'bom_line_sequence', 'bom_line_quantity', 'bom_line_unit',
            'bom_line_scrap_percent', 'component_item_revision', 'component_item_code',
            'component_revision', 'component_title', 'quantity', 'unit', 'scrap_percent',
            'allocation_type', 'issue_at_operation', 'backflush', 'sequence', 'notes',
        ]
        read_only_fields = ['id', 'bom_line_sequence', 'bom_line_quantity', 'bom_line_unit', 'bom_line_scrap_percent', 'component_item_code', 'component_revision', 'component_title']


class OPCNodeDocumentRequirementSerializer(serializers.ModelSerializer):
    document_number = serializers.CharField(source='document_revision.document.document_number', read_only=True)
    document_title = serializers.CharField(source='document_revision.document.title', read_only=True)
    document_type = serializers.CharField(source='document_revision.document.document_type', read_only=True)
    revision = serializers.CharField(source='document_revision.revision', read_only=True)
    status = serializers.CharField(source='document_revision.status', read_only=True)

    class Meta:
        model = OPCNodeDocumentRequirement
        fields = [
            'id', 'document_revision', 'document_number', 'document_title', 'document_type',
            'revision', 'status', 'purpose', 'mandatory', 'sequence', 'notes',
        ]
        read_only_fields = ['id', 'document_number', 'document_title', 'document_type', 'revision', 'status']


class OPCNodeSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='node_type', required=False)
    process_code_structured = serializers.CharField(source='process_definition.process_code', read_only=True, default=None)
    process_name = serializers.CharField(source='process_definition.name', read_only=True, default=None)
    process_active = serializers.BooleanField(source='process_definition.active', read_only=True, default=None)
    process_execution_classification = serializers.CharField(source='process_definition.execution_classification', read_only=True, default=None)
    plant_code = serializers.CharField(source='plant.code', read_only=True, default=None)
    plant_name = serializers.CharField(source='plant.name', read_only=True, default=None)
    plant_active = serializers.BooleanField(source='plant.active', read_only=True, default=None)
    work_center_code = serializers.CharField(source='work_center.code', read_only=True, default=None)
    work_center_name = serializers.CharField(source='work_center.name', read_only=True, default=None)
    work_center_active = serializers.BooleanField(source='work_center.active', read_only=True, default=None)
    machine_asset_code = serializers.CharField(source='machine_asset.asset_code', read_only=True, default=None)
    machine_asset_name = serializers.CharField(source='machine_asset.name', read_only=True, default=None)
    machine_asset_active = serializers.BooleanField(source='machine_asset.active', read_only=True, default=None)
    tooling_requirements = OPCToolingRequirementSerializer(many=True, read_only=True)
    material_allocations = OPCOperationMaterialAllocationSerializer(many=True, read_only=True)
    document_requirements = OPCNodeDocumentRequirementSerializer(many=True, read_only=True)

    class Meta:
        model = OPCNode
        fields = [
            'id', 'diagram', 'type', 'label', 'part_code',
            'process_code', 'station', 'execution_type', 'operation_number',
            'process_definition', 'process_code_structured', 'process_name', 'process_active', 'process_execution_classification',
            'plant', 'plant_code', 'plant_name', 'plant_active',
            'work_center', 'work_center_code', 'work_center_name', 'work_center_active',
            'machine_asset', 'machine_asset_code', 'machine_asset_name', 'machine_asset_active',
            'tooling_requirements',
            'material_allocations', 'document_requirements',
            'setup_time_hours',
            'run_time_per_unit_hours', 'queue_time_hours', 'move_time_hours',
            'inspection_time_hours', 'external_lead_time_days', 'buffer_time_hours',
            'description', 'sequence', 'x', 'y', 'meta', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'process_code_structured', 'process_name', 'process_active', 'process_execution_classification',
            'plant_code', 'plant_name', 'plant_active',
            'work_center_code', 'work_center_name', 'work_center_active',
            'machine_asset_code', 'machine_asset_name', 'machine_asset_active',
            'tooling_requirements', 'material_allocations', 'document_requirements', 'created_at', 'updated_at',
        ]
        extra_kwargs = {
            'diagram': {'write_only': True},
        }

    def validate(self, attrs):
        node_type = attrs.get('node_type')
        if not node_type and self.instance:
            attrs['node_type'] = self.instance.node_type
        if not attrs.get('node_type'):
            raise serializers.ValidationError({'type': 'Node type is required.'})
        instance = self.instance
        diagram = attrs.get('diagram') or getattr(instance, 'diagram', None)
        if instance:
            ensure_node_editable(instance)
        elif diagram and diagram.status in OPCDiagram.LOCKED_STATUSES:
            raise serializers.ValidationError({'diagram': 'Released OPC revisions are immutable.'})
        for field in ('setup_time_hours', 'run_time_per_unit_hours', 'queue_time_hours', 'move_time_hours', 'inspection_time_hours', 'external_lead_time_days', 'buffer_time_hours'):
            value = attrs.get(field, getattr(instance, field, 0) if instance else 0)
            if value is not None and value < 0:
                raise serializers.ValidationError({field: 'Duration values cannot be negative.'})
        combined = {
            'node_type': attrs.get('node_type', getattr(instance, 'node_type', None)),
            'execution_type': attrs.get('execution_type', getattr(instance, 'execution_type', None)),
            'operation_number': attrs.get('operation_number', getattr(instance, 'operation_number', None)),
            'process_definition_id': getattr(attrs.get('process_definition'), 'pk', attrs.get('process_definition_id', getattr(instance, 'process_definition_id', None))),
            'plant_id': getattr(attrs.get('plant'), 'pk', attrs.get('plant_id', getattr(instance, 'plant_id', None))),
            'work_center_id': getattr(attrs.get('work_center'), 'pk', attrs.get('work_center_id', getattr(instance, 'work_center_id', None))),
            'machine_asset_id': getattr(attrs.get('machine_asset'), 'pk', attrs.get('machine_asset_id', getattr(instance, 'machine_asset_id', None))),
        }
        try:
            validate_node_resource_assignments(
                index=0,
                attrs=combined,
                tooling_payloads=[],
                existing_node=instance,
                plants=Plant.objects.in_bulk([combined['plant_id']] if combined['plant_id'] else []),
                work_centers=WorkCenter.objects.select_related('plant').in_bulk([combined['work_center_id']] if combined['work_center_id'] else []),
                machines=MachineAsset.objects.select_related('plant', 'work_center').in_bulk([combined['machine_asset_id']] if combined['machine_asset_id'] else []),
                processes=ProcessDefinition.objects.in_bulk([combined['process_definition_id']] if combined['process_definition_id'] else []),
                tooling_definitions={},
                existing_tooling={},
            )
        except serializers.ValidationError:
            raise
        except Exception as exc:
            raise serializers.ValidationError({'resource_assignment': str(exc)}) from exc
        return attrs

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)

    def update(self, instance, validated_data):
        try:
            return super().update(instance, validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)


class OPCEdgeSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='edge_type', required=False)
    source_id = serializers.UUIDField(source='source.id', read_only=True)
    target_id = serializers.UUIDField(source='target.id', read_only=True)

    class Meta:
        model = OPCEdge
        fields = [
            'id', 'diagram', 'source', 'source_id', 'target', 'target_id',
            'type', 'label', 'sequence', 'meta',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'source_id', 'target_id', 'created_at', 'updated_at']
        extra_kwargs = {
            'diagram': {'write_only': True},
        }

    def validate(self, attrs):
        edge_type = attrs.get('edge_type')
        if not edge_type and self.instance:
            attrs['edge_type'] = self.instance.edge_type
        if not attrs.get('edge_type'):
            attrs['edge_type'] = 'FLOW'

        diagram = attrs.get('diagram') or getattr(self.instance, 'diagram', None)
        source = attrs.get('source') or getattr(self.instance, 'source', None)
        target = attrs.get('target') or getattr(self.instance, 'target', None)
        if source and target and source.id == target.id:
            raise serializers.ValidationError({'target': 'Source and target cannot be the same node.'})
        if diagram and source and source.diagram_id != diagram.id:
            raise serializers.ValidationError({'source': 'Source node must belong to the selected diagram.'})
        if diagram and target and target.diagram_id != diagram.id:
            raise serializers.ValidationError({'target': 'Target node must belong to the selected diagram.'})
        if self.instance:
            ensure_edge_editable(self.instance)
        elif diagram and diagram.status in OPCDiagram.LOCKED_STATUSES:
            raise serializers.ValidationError({'diagram': 'Released OPC revisions are immutable.'})
        return attrs

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)

    def update(self, instance, validated_data):
        try:
            return super().update(instance, validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)


class OPCDiagramSerializer(serializers.ModelSerializer):
    nodes = OPCNodeSerializer(many=True, read_only=True)
    edges = OPCEdgeSerializer(many=True, read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    updated_by = serializers.PrimaryKeyRelatedField(read_only=True)
    item_revision_detail = ItemRevisionSerializer(source='item_revision', read_only=True)
    released_by_username = serializers.CharField(source='released_by.username', read_only=True, default=None)
    approved_by_username = serializers.CharField(source='approved_by.username', read_only=True, default=None)
    submitted_by_username = serializers.CharField(source='submitted_by.username', read_only=True, default=None)
    superseded_by_revision = serializers.CharField(source='superseded_by.revision', read_only=True, default=None)
    allowed_actions = serializers.SerializerMethodField()
    latest_validation_evidence = serializers.SerializerMethodField()

    class Meta:
        model = OPCDiagram
        fields = [
            'id', 'item_revision', 'item_revision_detail', 'title', 'part_code',
            'part_name', 'revision', 'description', 'manufacturing_bom_revision', 'effective_from', 'effective_to',
            'status', 'submitted_by', 'submitted_by_username', 'submitted_at',
            'approved_by', 'approved_by_username', 'approved_at', 'released_by',
            'released_by_username', 'released_at', 'superseded_at', 'superseded_by',
            'superseded_by_revision', 'nodes', 'edges', 'created_by', 'updated_by',
            'graph_version', 'created_at', 'updated_at', 'allowed_actions', 'latest_validation_evidence',
        ]
        read_only_fields = ['id', 'item_revision_detail', 'status', 'submitted_by', 'submitted_by_username', 'submitted_at', 'approved_by', 'approved_by_username', 'approved_at', 'released_by', 'released_by_username', 'released_at', 'superseded_at', 'superseded_by', 'superseded_by_revision', 'created_by', 'updated_by', 'graph_version', 'created_at', 'updated_at', 'allowed_actions', 'latest_validation_evidence']

    def get_allowed_actions(self, obj):
        if obj.status == OPCDiagram.STATUS_DRAFT:
            return ['submit', 'clone', 'validate']
        if obj.status == OPCDiagram.STATUS_UNDER_REVIEW:
            return ['return_to_draft', 'approve', 'clone', 'validate']
        if obj.status == OPCDiagram.STATUS_APPROVED:
            return ['release', 'supersede', 'clone', 'validate']
        if obj.status in {OPCDiagram.STATUS_RELEASED, OPCDiagram.STATUS_SUPERSEDED}:
            return ['obsolete', 'clone', 'validate']
        return ['clone', 'validate']

    def get_latest_validation_evidence(self, obj):
        evidence = obj.validation_evidence.order_by('-created_at').first()
        if not evidence:
            return None
        return {
            'id': str(evidence.pk),
            'graph_version': evidence.graph_version,
            'policy_version': evidence.policy_version,
            'mode': evidence.mode,
            'validated_at': evidence.validated_at.isoformat(),
            'released_at': evidence.released_at.isoformat() if evidence.released_at else None,
            'actor': str(evidence.actor_id) if evidence.actor_id else None,
            'valid': evidence.valid,
            'release_ready': evidence.release_ready,
            'counts': evidence.counts,
            'acknowledged_warning_codes': evidence.acknowledged_warning_codes,
        }

    def validate(self, attrs):
        ensure_diagram_editable(self.instance, attrs)
        effective_from = attrs.get('effective_from', getattr(self.instance, 'effective_from', None))
        effective_to = attrs.get('effective_to', getattr(self.instance, 'effective_to', None))
        if effective_from and effective_to and effective_to < effective_from:
            raise serializers.ValidationError({'effective_to': 'Effective end date cannot precede effective start date.'})
        return attrs

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)

    def update(self, instance, validated_data):
        validated_data.pop('status', None)
        try:
            return super().update(instance, validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)


class OPCDiagramSummarySerializer(serializers.ModelSerializer):
    node_count = serializers.IntegerField(read_only=True)
    edge_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = OPCDiagram
        fields = [
            'id', 'item_revision', 'title', 'part_code', 'part_name', 'revision',
            'status', 'effective_from', 'effective_to', 'released_at', 'node_count', 'edge_count', 'graph_version', 'updated_at',
        ]


class ControlledDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ControlledDocument
        fields = ['id', 'document_number', 'title', 'document_type', 'description', 'owner_department', 'active', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']


class ControlledDocumentRevisionSerializer(serializers.ModelSerializer):
    document_number = serializers.CharField(source='document.document_number', read_only=True)
    document_title = serializers.CharField(source='document.title', read_only=True)
    document_type = serializers.CharField(source='document.document_type', read_only=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = ControlledDocumentRevision
        fields = [
            'id', 'document', 'document_number', 'document_title', 'document_type',
            'revision', 'status', 'effective_from', 'effective_to', 'file',
            'download_url', 'checksum_sha256', 'mime_type', 'original_filename',
            'file_size', 'description', 'change_summary', 'released_by',
            'released_at', 'superseded_by', 'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'document_number', 'document_title', 'document_type', 'download_url', 'checksum_sha256', 'mime_type', 'original_filename', 'file_size', 'released_by', 'released_at', 'superseded_by', 'created_by', 'created_at', 'updated_at']

    def get_download_url(self, obj):
        request = self.context.get('request')
        if not obj.pk:
            return None
        url = f'/api/opc/document-revisions/{obj.pk}/download/'
        return request.build_absolute_uri(url) if request else url


class EngineeringChangeRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = EngineeringChangeRequest
        fields = ['id', 'change_number', 'title', 'description', 'reason', 'status', 'created_by', 'submitted_at', 'decided_at', 'created_at', 'updated_at']
        read_only_fields = ['id', 'status', 'created_by', 'submitted_at', 'decided_at', 'created_at', 'updated_at']


class EngineeringChangeOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = EngineeringChangeOrder
        fields = ['id', 'change_number', 'request', 'title', 'description', 'status', 'effectivity_date', 'created_by', 'approved_at', 'implemented_at', 'created_at', 'updated_at']
        read_only_fields = ['id', 'status', 'created_by', 'approved_at', 'implemented_at', 'created_at', 'updated_at']


class EngineeringChangeObjectLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = EngineeringChangeObjectLink
        fields = ['id', 'request', 'order', 'object_type', 'object_id', 'role', 'before_object_type', 'before_object_id', 'after_object_type', 'after_object_id', 'notes', 'created_at']
        read_only_fields = ['id', 'created_at']


class ManufacturingOperationEligibleMachineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManufacturingOperationEligibleMachine
        fields = ['id', 'machine_asset', 'machine_code_snapshot', 'machine_name_snapshot', 'preferred', 'reason', 'sequence']
        read_only_fields = fields


class ManufacturingOperationSerializer(serializers.ModelSerializer):
    eligible_machines = ManufacturingOperationEligibleMachineSerializer(many=True, read_only=True)

    class Meta:
        model = ManufacturingOperation
        fields = [
            'id', 'source_opc_node', 'source_operation_number', 'operation_number',
            'sequence', 'node_type', 'label', 'description', 'execution_type',
            'process_definition', 'process_code_snapshot', 'plant',
            'plant_code_snapshot', 'work_center', 'work_center_code_snapshot',
            'selected_machine', 'machine_code_snapshot', 'eligible_machines',
            'setup_time_hours', 'run_time_per_unit_hours', 'queue_time_hours',
            'move_time_hours', 'inspection_time_hours', 'external_lead_time_days',
            'buffer_time_hours', 'planned_quantity', 'planned_start',
            'planned_end', 'status',
        ]
        read_only_fields = fields


class ManufacturingOperationPrecedenceSerializer(serializers.ModelSerializer):
    predecessor_operation_number = serializers.IntegerField(source='predecessor.operation_number', read_only=True, default=None)
    successor_operation_number = serializers.IntegerField(source='successor.operation_number', read_only=True, default=None)

    class Meta:
        model = ManufacturingOperationPrecedence
        fields = [
            'id', 'predecessor', 'successor', 'predecessor_operation_number',
            'successor_operation_number', 'source_opc_edge', 'edge_type',
            'label_snapshot', 'sequence', 'optional', 'rework',
        ]
        read_only_fields = fields


class ProductionMaterialRequirementSerializer(serializers.ModelSerializer):
    operation_number = serializers.IntegerField(source='operation.operation_number', read_only=True, default=None)
    material_control = serializers.SerializerMethodField()

    class Meta:
        model = ProductionMaterialRequirement
        fields = [
            'id', 'operation', 'operation_number', 'source_allocation',
            'source_mbom_line', 'component_item_revision',
            'component_code_snapshot', 'component_revision_snapshot',
            'quantity_per_unit', 'planned_order_quantity', 'total_net_quantity',
            'scrap_percent', 'total_planned_quantity', 'unit',
            'allocation_type', 'sequence', 'notes', 'material_control',
        ]
        read_only_fields = fields

    def get_material_control(self, obj):
        return material_reconciliation(obj)


class ProductionToolingRequirementSerializer(serializers.ModelSerializer):
    operation_number = serializers.IntegerField(source='operation.operation_number', read_only=True, default=None)

    class Meta:
        model = ProductionToolingRequirement
        fields = [
            'id', 'operation', 'operation_number', 'source_tooling_requirement',
            'tooling_definition', 'tooling_code_snapshot', 'tooling_name_snapshot',
            'quantity', 'mandatory', 'sequence', 'notes',
        ]
        read_only_fields = fields


class ProductionDocumentRequirementSerializer(serializers.ModelSerializer):
    operation_number = serializers.IntegerField(source='operation.operation_number', read_only=True, default=None)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductionDocumentRequirement
        fields = [
            'id', 'operation', 'operation_number', 'source_document_requirement',
            'controlled_document_revision', 'document_number_snapshot',
            'document_revision_snapshot', 'title_snapshot', 'purpose',
            'mandatory', 'sequence', 'notes', 'download_url',
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        request = self.context.get('request')
        url = f'/api/opc/document-revisions/{obj.controlled_document_revision_id}/download/'
        return request.build_absolute_uri(url) if request else url


class ProductionOrderValidationEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionOrderValidationEvidence
        fields = ['id', 'order_version', 'policy_version', 'validated_at', 'released_at', 'actor', 'valid', 'release_ready', 'counts', 'issues']
        read_only_fields = fields


class OperationExecutionCycleSerializer(serializers.ModelSerializer):
    source_rework_edge_label = serializers.CharField(source='source_rework_edge.label_snapshot', read_only=True, default=None)

    class Meta:
        model = OperationExecutionCycle
        fields = [
            'id', 'operation_execution', 'cycle_number', 'reason',
            'source_rework_edge', 'source_rework_edge_label', 'quantity',
            'status', 'started_at', 'completed_at', 'created_by', 'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class ManufacturingExecutionEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.get_username', read_only=True)
    assigned_operator_name = serializers.CharField(source='assigned_operator.get_username', read_only=True, default=None)
    machine_code = serializers.CharField(source='machine.asset_code', read_only=True, default=None)

    class Meta:
        model = ManufacturingExecutionEvent
        fields = [
            'id', 'production_order', 'operation_execution', 'cycle',
            'event_sequence', 'event_type', 'actor', 'actor_name',
            'assigned_operator', 'assigned_operator_name', 'machine',
            'machine_code', 'event_timestamp', 'server_recorded_at',
            'produced_quantity', 'accepted_quantity', 'rejected_quantity',
            'scrapped_quantity', 'rework_quantity', 'duration_seconds',
            'idempotency_key', 'previous_execution_version',
            'new_execution_version', 'notes', 'metadata',
        ]
        read_only_fields = fields


class ProductionLotSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    item_revision_code = serializers.CharField(source='item_revision.revision', read_only=True)
    order_number = serializers.CharField(source='production_order.order_number', read_only=True)

    class Meta:
        model = ProductionLot
        fields = [
            'id', 'lot_number', 'item_revision', 'item_code',
            'item_revision_code', 'production_order', 'order_number',
            'created_by', 'created_at', 'notes',
        ]
        read_only_fields = ['id', 'item_code', 'item_revision_code', 'order_number', 'created_by', 'created_at']


class ProductionSerialSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    item_revision_code = serializers.CharField(source='item_revision.revision', read_only=True)
    order_number = serializers.CharField(source='production_order.order_number', read_only=True)
    lot_number = serializers.CharField(source='lot.lot_number', read_only=True, default=None)

    class Meta:
        model = ProductionSerial
        fields = [
            'id', 'serial_number', 'item_revision', 'item_code',
            'item_revision_code', 'production_order', 'order_number', 'lot',
            'lot_number', 'created_by', 'created_at', 'notes',
        ]
        read_only_fields = ['id', 'item_code', 'item_revision_code', 'order_number', 'lot_number', 'created_by', 'created_at']


class ProductionMaterialConsumptionSerializer(serializers.ModelSerializer):
    component_code = serializers.CharField(source='component_item_revision.item.item_code', read_only=True)
    component_revision = serializers.CharField(source='component_item_revision.revision', read_only=True)
    lot_number = serializers.CharField(source='lot.lot_number', read_only=True, default=None)
    serial_number = serializers.CharField(source='serial.serial_number', read_only=True, default=None)

    class Meta:
        model = ProductionMaterialConsumption
        fields = [
            'id', 'production_order', 'operation_execution', 'cycle',
            'requirement', 'component_item_revision', 'component_code',
            'component_revision', 'quantity', 'unit', 'consumption_type',
            'lot', 'lot_number', 'serial', 'serial_number', 'event',
            'actor', 'consumed_at', 'notes',
        ]
        read_only_fields = fields


class ProductionToolingUsageSerializer(serializers.ModelSerializer):
    tooling_code = serializers.CharField(source='tooling_definition.tooling_code', read_only=True)
    tooling_name = serializers.CharField(source='tooling_definition.name', read_only=True)
    machine_code = serializers.CharField(source='machine.asset_code', read_only=True, default=None)

    class Meta:
        model = ProductionToolingUsage
        fields = [
            'id', 'production_order', 'operation_execution', 'requirement',
            'tooling_definition', 'tooling_code', 'tooling_name', 'quantity',
            'machine', 'machine_code', 'event', 'actor', 'used_at', 'notes',
        ]
        read_only_fields = fields


class ProductionDocumentAcknowledgementSerializer(serializers.ModelSerializer):
    document_number = serializers.CharField(source='controlled_document_revision.document.document_number', read_only=True)
    document_revision = serializers.CharField(source='controlled_document_revision.revision', read_only=True)
    title = serializers.CharField(source='controlled_document_revision.document.title', read_only=True)
    actor_name = serializers.CharField(source='actor.get_username', read_only=True)

    class Meta:
        model = ProductionDocumentAcknowledgement
        fields = [
            'id', 'production_order', 'operation_execution', 'requirement',
            'controlled_document_revision', 'document_number',
            'document_revision', 'title', 'event', 'actor', 'actor_name',
            'acknowledged_at', 'notes',
        ]
        read_only_fields = fields


class ManufacturingGenealogyLinkSerializer(serializers.ModelSerializer):
    parent_lot_number = serializers.CharField(source='parent_lot.lot_number', read_only=True, default=None)
    parent_serial_number = serializers.CharField(source='parent_serial.serial_number', read_only=True, default=None)
    component_lot_number = serializers.CharField(source='component_lot.lot_number', read_only=True, default=None)
    component_serial_number = serializers.CharField(source='component_serial.serial_number', read_only=True, default=None)

    class Meta:
        model = ManufacturingGenealogyLink
        fields = [
            'id', 'production_order', 'parent_lot', 'parent_lot_number',
            'parent_serial', 'parent_serial_number', 'component_lot',
            'component_lot_number', 'component_serial',
            'component_serial_number', 'consumption', 'event', 'actor',
            'linked_at', 'notes',
        ]
        read_only_fields = fields


class OperationExecutionSerializer(serializers.ModelSerializer):
    operation_number = serializers.IntegerField(source='manufacturing_operation.operation_number', read_only=True, default=None)
    operation_label = serializers.CharField(source='manufacturing_operation.label_snapshot', read_only=True, default='')
    work_center_code = serializers.CharField(source='manufacturing_operation.work_center_code_snapshot', read_only=True, default='')
    machine_code = serializers.CharField(source='assigned_machine.asset_code', read_only=True, default=None)
    assigned_operator_name = serializers.CharField(source='assigned_operator.get_username', read_only=True, default=None)
    allowed_actions = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    cycles = OperationExecutionCycleSerializer(many=True, read_only=True)
    events = ManufacturingExecutionEventSerializer(many=True, read_only=True)
    material_consumptions = ProductionMaterialConsumptionSerializer(many=True, read_only=True)
    tooling_usages = ProductionToolingUsageSerializer(many=True, read_only=True)
    document_acknowledgements = ProductionDocumentAcknowledgementSerializer(many=True, read_only=True)
    quality_summary = serializers.SerializerMethodField()
    material_summary = serializers.SerializerMethodField()

    class Meta:
        model = OperationExecution
        fields = [
            'id', 'production_order', 'manufacturing_operation',
            'operation_number', 'operation_label', 'status',
            'execution_version', 'dispatch_priority', 'ready',
            'block_reason', 'assigned_operator', 'assigned_operator_name',
            'assigned_machine', 'machine_code', 'work_center_code',
            'planned_quantity', 'produced_quantity', 'accepted_quantity',
            'rejected_quantity', 'scrapped_quantity', 'rework_quantity',
            'actual_setup_seconds', 'actual_run_seconds', 'active_interval',
            'active_interval_started_at', 'first_started_at', 'paused_at',
            'completed_at', 'current_cycle_number', 'created_at',
            'updated_at', 'allowed_actions', 'progress', 'cycles', 'events',
            'material_consumptions', 'tooling_usages',
            'document_acknowledgements', 'quality_summary', 'material_summary',
        ]
        read_only_fields = fields

    def get_allowed_actions(self, obj):
        return allowed_execution_actions(obj)

    def get_progress(self, obj):
        return progress_rollup(obj.production_order)

    def get_quality_summary(self, obj):
        return {
            'required_inspections': obj.production_order.inspection_requirements.filter(operation=obj.manufacturing_operation).count(),
            'inspection_count': obj.inspection_executions.count(),
            'active_hold_count': obj.quality_holds.filter(active=True).count(),
            'open_ncr_count': obj.nonconformances.exclude(status__in=[NonconformanceRecord.STATUS_CLOSED, NonconformanceRecord.STATUS_CANCELLED]).count(),
        }

    def get_material_summary(self, obj):
        return operation_material_summary(obj)


class WarehouseSerializer(serializers.ModelSerializer):
    plant_code = serializers.CharField(source='plant.code', read_only=True, default=None)

    class Meta:
        model = Warehouse
        fields = ['id', 'code', 'name', 'description', 'plant', 'plant_code', 'warehouse_type', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class StorageLocationSerializer(serializers.ModelSerializer):
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True)
    parent_code = serializers.CharField(source='parent.code', read_only=True, default=None)

    class Meta:
        model = StorageLocation
        fields = ['id', 'warehouse', 'warehouse_code', 'parent', 'parent_code', 'code', 'name', 'description', 'location_type', 'active', 'inventory_enabled', 'quarantine', 'created_at', 'updated_at']
        read_only_fields = ['id', 'warehouse_code', 'parent_code', 'created_at', 'updated_at']


class InventoryTransactionSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    item_revision_code = serializers.CharField(source='item_revision.revision', read_only=True)
    source_warehouse_code = serializers.CharField(source='source_warehouse.code', read_only=True, default=None)
    source_location_code = serializers.CharField(source='source_location.code', read_only=True, default=None)
    destination_warehouse_code = serializers.CharField(source='destination_warehouse.code', read_only=True, default=None)
    destination_location_code = serializers.CharField(source='destination_location.code', read_only=True, default=None)
    source_lot_number = serializers.CharField(source='source_lot.lot_number', read_only=True, default=None)
    source_serial_number = serializers.CharField(source='source_serial.serial_number', read_only=True, default=None)
    destination_lot_number = serializers.CharField(source='destination_lot.lot_number', read_only=True, default=None)
    destination_serial_number = serializers.CharField(source='destination_serial.serial_number', read_only=True, default=None)
    actor_name = serializers.CharField(source='actor.get_username', read_only=True)

    class Meta:
        model = InventoryTransaction
        fields = [
            'id', 'transaction_number', 'transaction_type', 'item_revision',
            'item_code', 'item_revision_code', 'quantity', 'unit',
            'source_warehouse', 'source_warehouse_code', 'source_location',
            'source_location_code', 'destination_warehouse',
            'destination_warehouse_code', 'destination_location',
            'destination_location_code', 'source_lot', 'source_lot_number',
            'source_serial', 'source_serial_number', 'destination_lot',
            'destination_lot_number', 'destination_serial',
            'destination_serial_number', 'source_stock_status',
            'destination_stock_status', 'ownership', 'production_order',
            'operation_execution', 'material_requirement',
            'material_consumption', 'quality_hold', 'nonconformance',
            'disposition', 'actor', 'actor_name', 'event_timestamp',
            'server_recorded_at', 'idempotency_key', 'reversal_of',
            'reason_code', 'notes', 'metadata',
        ]
        read_only_fields = fields


class InventoryBalanceSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    item_revision_code = serializers.CharField(source='item_revision.revision', read_only=True)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True)
    location_code = serializers.CharField(source='location.code', read_only=True)
    lot_number = serializers.CharField(source='lot.lot_number', read_only=True, default=None)
    serial_number = serializers.CharField(source='serial.serial_number', read_only=True, default=None)
    available_quantity = serializers.SerializerMethodField()
    last_transaction_number = serializers.CharField(source='last_transaction.transaction_number', read_only=True, default=None)

    class Meta:
        model = InventoryBalance
        fields = [
            'id', 'item_revision', 'item_code', 'item_revision_code',
            'warehouse', 'warehouse_code', 'location', 'location_code',
            'lot', 'lot_number', 'serial', 'serial_number', 'stock_status',
            'ownership', 'unit', 'on_hand_quantity', 'reserved_quantity',
            'available_quantity', 'balance_version', 'last_transaction',
            'last_transaction_number', 'updated_at',
        ]
        read_only_fields = fields

    def get_available_quantity(self, obj):
        return str(obj.available_quantity)


class InventoryReservationSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source='production_order.order_number', read_only=True)
    operation_number = serializers.IntegerField(source='material_requirement.operation.operation_number', read_only=True, default=None)
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True, default=None)
    location_code = serializers.CharField(source='location.code', read_only=True, default=None)
    lot_number = serializers.CharField(source='lot.lot_number', read_only=True, default=None)
    serial_number = serializers.CharField(source='serial.serial_number', read_only=True, default=None)
    open_quantity = serializers.SerializerMethodField()

    class Meta:
        model = InventoryReservation
        fields = [
            'id', 'production_order', 'order_number', 'material_requirement',
            'operation_number', 'item_revision', 'item_code', 'balance',
            'warehouse', 'warehouse_code', 'location', 'location_code',
            'lot', 'lot_number', 'serial', 'serial_number', 'quantity',
            'released_quantity', 'issued_quantity', 'open_quantity', 'unit',
            'status', 'reservation_version', 'reserved_by', 'reserved_at',
            'last_transaction', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_open_quantity(self, obj):
        return str(obj.open_quantity)


class ResourceCalendarSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceCalendar
        fields = ['id', 'code', 'name', 'timezone', 'calendar_type', 'planning_calendar', 'effective_start', 'effective_end', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ShiftSerializer(serializers.ModelSerializer):
    calendar_code = serializers.CharField(source='resource_calendar.code', read_only=True)

    class Meta:
        model = Shift
        fields = ['id', 'resource_calendar', 'calendar_code', 'code', 'name', 'weekday', 'local_start_time', 'local_end_time', 'capacity_factor', 'effective_start', 'effective_end', 'active', 'sequence']
        read_only_fields = ['id', 'calendar_code']


class CalendarExceptionSerializer(serializers.ModelSerializer):
    calendar_code = serializers.CharField(source='resource_calendar.code', read_only=True)

    class Meta:
        model = CalendarException
        fields = ['id', 'resource_calendar', 'calendar_code', 'start_datetime', 'end_datetime', 'exception_type', 'capacity_factor', 'reason', 'source_reference', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'calendar_code', 'created_at', 'updated_at']


class WorkCenterCapacitySerializer(serializers.ModelSerializer):
    work_center_code = serializers.CharField(source='work_center.code', read_only=True)
    calendar_code = serializers.CharField(source='resource_calendar.code', read_only=True)

    class Meta:
        model = WorkCenterCapacity
        fields = ['id', 'work_center', 'work_center_code', 'resource_calendar', 'calendar_code', 'parallel_capacity_units', 'default_efficiency_factor', 'queue_capacity_metadata', 'effective_start', 'effective_end', 'active']
        read_only_fields = ['id', 'work_center_code', 'calendar_code']


class MachineCapacitySerializer(serializers.ModelSerializer):
    machine_code = serializers.CharField(source='machine.asset_code', read_only=True)
    calendar_code = serializers.CharField(source='resource_calendar.code', read_only=True)

    class Meta:
        model = MachineCapacity
        fields = ['id', 'machine', 'machine_code', 'resource_calendar', 'calendar_code', 'exclusive_capacity', 'efficiency_factor', 'setup_family', 'effective_start', 'effective_end', 'active']
        read_only_fields = ['id', 'machine_code', 'calendar_code']


class LaborSkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = LaborSkill
        fields = ['id', 'code', 'name', 'active']
        read_only_fields = ['id']


class WorkCenterLaborCapacitySerializer(serializers.ModelSerializer):
    work_center_code = serializers.CharField(source='work_center.code', read_only=True)
    skill_code = serializers.CharField(source='labor_skill.code', read_only=True)
    calendar_code = serializers.CharField(source='resource_calendar.code', read_only=True)

    class Meta:
        model = WorkCenterLaborCapacity
        fields = ['id', 'work_center', 'work_center_code', 'labor_skill', 'skill_code', 'resource_calendar', 'calendar_code', 'available_units', 'active']
        read_only_fields = ['id', 'work_center_code', 'skill_code', 'calendar_code']


class SchedulingPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = SchedulingPolicy
        fields = ['id', 'code', 'name', 'direction', 'dispatch_rule', 'allow_overtime', 'allow_overload', 'freeze_fence_days', 'default_queue_time_hours', 'default_move_time_hours', 'lateness_weight', 'priority_weight', 'policy_version', 'active']
        read_only_fields = ['id']


class SchedulingOperationSnapshotSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source='production_order.order_number', read_only=True)
    operation_label = serializers.CharField(source='manufacturing_operation.label', read_only=True)
    work_center_code = serializers.CharField(source='work_center.code', read_only=True, default=None)

    class Meta:
        model = SchedulingOperationSnapshot
        fields = ['id', 'production_order', 'order_number', 'manufacturing_operation', 'operation_label', 'operation_number', 'priority', 'required_completion', 'current_planned_start', 'current_planned_end', 'fixed_start', 'fixed_end', 'fixed_reason', 'source_order_version', 'source_execution_version', 'work_center', 'work_center_code', 'selected_machine', 'eligible_machine_ids', 'calculated_duration_minutes', 'setup_duration_minutes', 'run_duration_minutes', 'queue_duration_minutes', 'move_duration_minutes', 'buffer_duration_minutes', 'execution_type', 'status_snapshot', 'source_metadata', 'sequence']
        read_only_fields = fields


class ScheduledOperationAssignmentSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source='production_order.order_number', read_only=True)
    operation_number = serializers.IntegerField(source='manufacturing_operation.operation_number', read_only=True)
    operation_label = serializers.CharField(source='manufacturing_operation.label', read_only=True)
    work_center_code = serializers.CharField(source='work_center.code', read_only=True, default=None)
    machine_code = serializers.CharField(source='machine.asset_code', read_only=True, default=None)

    class Meta:
        model = ScheduledOperationAssignment
        fields = ['id', 'operation_snapshot', 'production_order', 'order_number', 'manufacturing_operation', 'operation_number', 'operation_label', 'planned_start', 'planned_end', 'setup_start', 'setup_end', 'run_start', 'run_end', 'work_center', 'work_center_code', 'machine', 'machine_code', 'sequence_on_resource', 'dispatch_priority', 'lateness_minutes', 'slack_minutes', 'status', 'explanation', 'created_at']
        read_only_fields = fields


class SchedulingCalendarSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchedulingCalendarSnapshot
        fields = ['id', 'resource_type', 'resource_id', 'interval_start', 'interval_end', 'capacity_units', 'source_calendar', 'source_shift', 'source_exception', 'availability_status']
        read_only_fields = fields


class CapacityBucketSerializer(serializers.ModelSerializer):
    class Meta:
        model = CapacityBucket
        fields = ['id', 'resource_type', 'resource_id', 'bucket_start', 'bucket_end', 'available_minutes', 'fixed_load_minutes', 'proposed_load_minutes', 'utilization', 'overload_minutes', 'idle_minutes', 'bottleneck', 'affected_orders']
        read_only_fields = fields


class SchedulingExceptionSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source='production_order.order_number', read_only=True, default=None)
    operation_number = serializers.IntegerField(source='manufacturing_operation.operation_number', read_only=True, default=None)

    class Meta:
        model = SchedulingException
        fields = ['id', 'severity', 'code', 'production_order', 'order_number', 'manufacturing_operation', 'operation_number', 'resource_type', 'resource_id', 'interval_start', 'interval_end', 'message', 'blocking', 'metadata', 'sequence']
        read_only_fields = fields


class SchedulingRunSerializer(serializers.ModelSerializer):
    freshness = serializers.SerializerMethodField()
    assignment_count = serializers.IntegerField(source='assignments.count', read_only=True)
    exception_count = serializers.IntegerField(source='exceptions.count', read_only=True)
    bottleneck_count = serializers.SerializerMethodField()

    class Meta:
        model = SchedulingRun
        fields = ['id', 'run_number', 'scenario_name', 'status', 'scheduling_policy', 'horizon_start', 'horizon_end', 'cutoff_timestamp', 'plant', 'work_center', 'direction', 'input_checksum', 'result_checksum', 'policy_version', 'parameter_snapshot', 'freshness_snapshot', 'metrics', 'error_summary', 'run_version', 'approved_by', 'applied_by', 'started_by', 'started_at', 'completed_at', 'approved_at', 'applied_at', 'failed_at', 'application_idempotency_key', 'created_at', 'freshness', 'assignment_count', 'exception_count', 'bottleneck_count']
        read_only_fields = ['id', 'run_number', 'status', 'cutoff_timestamp', 'input_checksum', 'result_checksum', 'policy_version', 'parameter_snapshot', 'freshness_snapshot', 'metrics', 'error_summary', 'run_version', 'approved_by', 'applied_by', 'started_by', 'started_at', 'completed_at', 'approved_at', 'applied_at', 'failed_at', 'application_idempotency_key', 'created_at', 'freshness', 'assignment_count', 'exception_count', 'bottleneck_count']

    def get_freshness(self, obj):
        return scheduling_freshness(obj)

    def get_bottleneck_count(self, obj):
        return obj.capacity_buckets.filter(bottleneck=True).count()


class SchedulingRunDetailSerializer(SchedulingRunSerializer):
    operation_snapshots = SchedulingOperationSnapshotSerializer(many=True, read_only=True)
    assignments = ScheduledOperationAssignmentSerializer(many=True, read_only=True)
    calendar_snapshots = SchedulingCalendarSnapshotSerializer(many=True, read_only=True)
    capacity_buckets = CapacityBucketSerializer(many=True, read_only=True)
    exceptions = SchedulingExceptionSerializer(many=True, read_only=True)

    class Meta(SchedulingRunSerializer.Meta):
        fields = [*SchedulingRunSerializer.Meta.fields, 'operation_snapshots', 'assignments', 'calendar_snapshots', 'capacity_buckets', 'exceptions']


class MaintainableAssetSerializer(serializers.ModelSerializer):
    plant_code = serializers.CharField(source='plant.code', read_only=True, default=None)
    work_center_code = serializers.CharField(source='work_center.code', read_only=True, default=None)
    parent_code = serializers.CharField(source='parent.asset_code', read_only=True, default=None)

    class Meta:
        model = MaintainableAsset
        fields = ['id', 'asset_code', 'name', 'asset_type', 'plant', 'plant_code', 'work_center', 'work_center_code', 'parent', 'parent_code', 'location_code', 'classification', 'criticality', 'operational_status', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'plant_code', 'work_center_code', 'parent_code', 'created_at', 'updated_at']


class MachineMaintenanceProfileSerializer(serializers.ModelSerializer):
    machine_code = serializers.CharField(source='machine.asset_code', read_only=True)
    parent_asset_code = serializers.CharField(source='parent_asset.asset_code', read_only=True, default=None)

    class Meta:
        model = MachineMaintenanceProfile
        fields = ['id', 'machine', 'machine_code', 'parent_asset', 'parent_asset_code', 'classification', 'criticality', 'operational_status', 'maintenance_location', 'risk_notes', 'active', 'profile_version', 'updated_at']
        read_only_fields = ['id', 'machine_code', 'parent_asset_code', 'profile_version', 'updated_at']


class FailureCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = FailureCode
        fields = ['id', 'code', 'name', 'category', 'active']


class CauseCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = CauseCode
        fields = ['id', 'code', 'name', 'active']


class RemedyCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RemedyCode
        fields = ['id', 'code', 'name', 'active']


class AssetMeterSerializer(serializers.ModelSerializer):
    machine_code = serializers.CharField(source='machine.asset_code', read_only=True, default=None)
    asset_code = serializers.CharField(source='maintainable_asset.asset_code', read_only=True, default=None)
    latest_reading = serializers.SerializerMethodField()

    class Meta:
        model = AssetMeter
        fields = ['id', 'machine', 'machine_code', 'maintainable_asset', 'asset_code', 'code', 'name', 'meter_type', 'unit', 'rollover_value', 'active', 'latest_reading', 'created_at', 'updated_at']
        read_only_fields = ['id', 'machine_code', 'asset_code', 'latest_reading', 'created_at', 'updated_at']

    def get_latest_reading(self, obj):
        reading = obj.readings.order_by('-reading_timestamp', '-created_at').first()
        return AssetMeterReadingSerializer(reading).data if reading else None


class AssetMeterReadingSerializer(serializers.ModelSerializer):
    meter_code = serializers.CharField(source='meter.code', read_only=True)
    recorded_by_username = serializers.CharField(source='recorded_by.username', read_only=True, default='')

    class Meta:
        model = AssetMeterReading
        fields = ['id', 'meter', 'meter_code', 'reading_value', 'reading_timestamp', 'recorded_by', 'recorded_by_username', 'source', 'idempotency_key', 'notes', 'created_at']
        read_only_fields = ['id', 'meter_code', 'recorded_by', 'recorded_by_username', 'created_at']


class MaintenanceTaskChecklistItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaintenanceTaskChecklistItem
        fields = ['id', 'template', 'sequence', 'label', 'mandatory', 'expected_result']


class MaintenanceTaskTemplateSerializer(serializers.ModelSerializer):
    checklist_items = MaintenanceTaskChecklistItemSerializer(many=True, read_only=True)
    required_skill_code = serializers.CharField(source='required_skill.code', read_only=True, default=None)

    class Meta:
        model = MaintenanceTaskTemplate
        fields = ['id', 'code', 'name', 'description', 'estimated_duration_hours', 'required_skill', 'required_skill_code', 'safety_notes', 'active', 'checklist_items', 'created_at', 'updated_at']
        read_only_fields = ['id', 'required_skill_code', 'checklist_items', 'created_at', 'updated_at']


class PreventiveMaintenancePlanSerializer(serializers.ModelSerializer):
    machine_code = serializers.CharField(source='machine.asset_code', read_only=True, default=None)
    asset_code = serializers.CharField(source='maintainable_asset.asset_code', read_only=True, default=None)
    task_template_code = serializers.CharField(source='task_template.code', read_only=True)
    meter_code = serializers.CharField(source='meter.code', read_only=True, default=None)

    class Meta:
        model = PreventiveMaintenancePlan
        fields = ['id', 'plan_number', 'name', 'machine', 'machine_code', 'maintainable_asset', 'asset_code', 'task_template', 'task_template_code', 'trigger_type', 'interval_days', 'meter', 'meter_code', 'meter_interval', 'threshold_value', 'next_due_date', 'next_due_meter_value', 'generate_horizon_days', 'status', 'plan_version', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['id', 'plan_number', 'machine_code', 'asset_code', 'task_template_code', 'meter_code', 'plan_version', 'created_by', 'created_at', 'updated_at']


class MaintenanceRequestSerializer(serializers.ModelSerializer):
    machine_code = serializers.CharField(source='machine.asset_code', read_only=True, default=None)
    asset_code = serializers.CharField(source='maintainable_asset.asset_code', read_only=True, default=None)
    failure_code_value = serializers.CharField(source='failure_code.code', read_only=True, default=None)
    requested_by_username = serializers.CharField(source='requested_by.username', read_only=True, default='')

    class Meta:
        model = MaintenanceRequest
        fields = ['id', 'request_number', 'machine', 'machine_code', 'maintainable_asset', 'asset_code', 'title', 'description', 'priority', 'failure_code', 'failure_code_value', 'production_order', 'operation_execution', 'status', 'request_version', 'requested_by', 'requested_by_username', 'requested_at', 'converted_work_order', 'updated_at']
        read_only_fields = ['id', 'request_number', 'machine_code', 'asset_code', 'failure_code_value', 'status', 'request_version', 'requested_by', 'requested_by_username', 'requested_at', 'converted_work_order', 'updated_at']


class MaintenanceChecklistResultSerializer(serializers.ModelSerializer):
    recorded_by_username = serializers.CharField(source='recorded_by.username', read_only=True, default='')

    class Meta:
        model = MaintenanceChecklistResult
        fields = ['id', 'work_order', 'template_item', 'sequence', 'label', 'mandatory', 'status', 'notes', 'recorded_by', 'recorded_by_username', 'recorded_at']
        read_only_fields = ['id', 'recorded_by', 'recorded_by_username', 'recorded_at']


class MaintenanceSparePartRequirementSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    item_revision_code = serializers.CharField(source='item_revision.revision', read_only=True)

    class Meta:
        model = MaintenanceSparePartRequirement
        fields = ['id', 'work_order', 'item_revision', 'item_code', 'item_revision_code', 'quantity', 'unit', 'status', 'issued_quantity', 'returned_quantity', 'requirement_version', 'notes']
        read_only_fields = ['id', 'item_code', 'item_revision_code', 'status', 'issued_quantity', 'returned_quantity', 'requirement_version']


class MaintenanceSparePartIssueSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='requirement.item_revision.item.item_code', read_only=True)
    issued_by_username = serializers.CharField(source='issued_by.username', read_only=True, default='')

    class Meta:
        model = MaintenanceSparePartIssue
        fields = ['id', 'work_order', 'requirement', 'item_code', 'issue_transaction', 'return_transaction', 'quantity', 'returned_quantity', 'issued_by', 'issued_by_username', 'issued_at']
        read_only_fields = fields


class MaintenanceEventSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source='actor.username', read_only=True, default='')

    class Meta:
        model = MaintenanceEvent
        fields = ['id', 'work_order', 'event_type', 'event_timestamp', 'actor', 'actor_username', 'from_status', 'to_status', 'idempotency_key', 'notes', 'metadata', 'created_at']
        read_only_fields = fields


class MachineDowntimeEventSerializer(serializers.ModelSerializer):
    machine_code = serializers.CharField(source='machine.asset_code', read_only=True)
    work_order_number = serializers.CharField(source='work_order.work_order_number', read_only=True, default=None)

    class Meta:
        model = MachineDowntimeEvent
        fields = ['id', 'machine', 'machine_code', 'work_order', 'work_order_number', 'downtime_type', 'start_datetime', 'end_datetime', 'planned', 'failure_code', 'cause_code', 'calendar_exception', 'notes', 'created_at']
        read_only_fields = ['id', 'machine_code', 'work_order_number', 'created_at']


class MaintenanceWorkOrderSerializer(serializers.ModelSerializer):
    machine_code = serializers.CharField(source='machine.asset_code', read_only=True, default=None)
    asset_code = serializers.CharField(source='maintainable_asset.asset_code', read_only=True, default=None)
    assigned_to_username = serializers.CharField(source='assigned_to.username', read_only=True, default=None)
    checklist_results = MaintenanceChecklistResultSerializer(many=True, read_only=True)
    spare_part_requirements = MaintenanceSparePartRequirementSerializer(many=True, read_only=True)
    spare_part_issues = MaintenanceSparePartIssueSerializer(many=True, read_only=True)
    events = MaintenanceEventSerializer(many=True, read_only=True)
    downtime_events = MachineDowntimeEventSerializer(many=True, read_only=True)

    class Meta:
        model = MaintenanceWorkOrder
        fields = ['id', 'work_order_number', 'work_order_type', 'title', 'description', 'machine', 'machine_code', 'maintainable_asset', 'asset_code', 'request', 'preventive_plan', 'task_template', 'priority', 'status', 'planned_start', 'planned_end', 'estimated_duration_hours', 'actual_start', 'actual_end', 'failure_code', 'cause_code', 'remedy_code', 'downtime_required', 'downtime_exception', 'work_order_version', 'created_by', 'assigned_to', 'assigned_to_username', 'completed_by', 'created_at', 'updated_at', 'checklist_results', 'spare_part_requirements', 'spare_part_issues', 'events', 'downtime_events']
        read_only_fields = ['id', 'work_order_number', 'status', 'actual_start', 'actual_end', 'downtime_exception', 'work_order_version', 'created_by', 'completed_by', 'created_at', 'updated_at', 'checklist_results', 'spare_part_requirements', 'spare_part_issues', 'events', 'downtime_events']


class PlanningCalendarSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningCalendar
        fields = ['id', 'code', 'name', 'timezone', 'working_weekdays', 'holidays', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ItemPlanningPolicySerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    item_revision_code = serializers.CharField(source='item_revision.revision', read_only=True)
    warehouse_code = serializers.CharField(source='default_warehouse.code', read_only=True, default=None)

    class Meta:
        model = ItemPlanningPolicy
        fields = [
            'id', 'item_revision', 'item_code', 'item_revision_code',
            'planning_method', 'procurement_type', 'default_warehouse',
            'warehouse_code', 'safety_stock', 'minimum_stock', 'reorder_point',
            'pre_processing_lead_days', 'supply_lead_days',
            'post_processing_lead_days', 'safety_lead_days',
            'variable_lead_days_per_unit', 'lot_sizing_method',
            'minimum_order_quantity', 'maximum_order_quantity',
            'order_multiple', 'fixed_lot_quantity',
            'planning_time_fence_days', 'active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'item_code', 'item_revision_code', 'warehouse_code', 'created_at', 'updated_at']


class PlanningDemandSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    item_revision_code = serializers.CharField(source='item_revision.revision', read_only=True)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True, default=None)
    created_by_name = serializers.CharField(source='created_by.get_username', read_only=True, default=None)

    class Meta:
        model = PlanningDemand
        fields = [
            'id', 'demand_number', 'demand_type', 'item_revision',
            'item_code', 'item_revision_code', 'warehouse', 'warehouse_code',
            'plant', 'quantity', 'unit', 'required_date', 'priority',
            'status', 'source_reference', 'description', 'demand_version',
            'created_by', 'created_by_name', 'approved_by', 'approved_at',
            'cancelled_by', 'cancelled_at', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'demand_number', 'status', 'demand_version', 'created_by', 'created_by_name', 'approved_by', 'approved_at', 'cancelled_by', 'cancelled_at', 'created_at', 'updated_at']


class ScheduledSupplySerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True, default=None)

    class Meta:
        model = ScheduledSupply
        fields = ['id', 'supply_number', 'item_revision', 'item_code', 'quantity', 'unit', 'expected_date', 'warehouse', 'warehouse_code', 'supply_type', 'status', 'firm', 'source_reference', 'supply_version', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['id', 'supply_number', 'created_by', 'created_at', 'updated_at']


class MRPDemandSnapshotSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)

    class Meta:
        model = MRPDemandSnapshot
        fields = ['id', 'source_demand', 'item_revision', 'item_code', 'warehouse', 'quantity', 'unit', 'required_date', 'priority', 'source_status', 'source_version', 'source_metadata', 'sequence']
        read_only_fields = fields


class MRPSupplySnapshotSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)

    class Meta:
        model = MRPSupplySnapshot
        fields = ['id', 'source_type', 'source_id', 'item_revision', 'item_code', 'warehouse', 'quantity', 'unit', 'available_date', 'firm', 'source_status', 'source_version', 'source_metadata', 'sequence']
        read_only_fields = fields


class MRPRequirementSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)

    class Meta:
        model = MRPRequirement
        fields = ['id', 'item_revision', 'item_code', 'warehouse', 'bucket_date', 'gross_requirement', 'scheduled_receipt', 'projected_available', 'safety_stock', 'net_requirement', 'planned_receipt', 'planned_release', 'planned_release_date', 'procurement_type', 'bom_revision', 'parent_requirement', 'demand_snapshot', 'bom_path', 'exception_codes', 'sequence']
        read_only_fields = fields


class MRPRecommendationSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    item_revision_code = serializers.CharField(source='item_revision.revision', read_only=True)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True, default=None)
    freshness = serializers.SerializerMethodField()

    class Meta:
        model = MRPRecommendation
        fields = ['id', 'mrp_run', 'recommendation_number', 'recommendation_type', 'item_revision', 'item_code', 'item_revision_code', 'warehouse', 'warehouse_code', 'quantity', 'unit', 'required_date', 'planned_receipt_date', 'planned_release_date', 'procurement_type', 'source_requirement', 'shortage_quantity', 'priority', 'status', 'recommendation_version', 'explanation', 'exception_codes', 'input_checksum', 'reviewed_by', 'approved_by', 'converted_by', 'reviewed_at', 'approved_at', 'converted_at', 'converted_production_order', 'converted_purchase_requisition', 'freshness']
        read_only_fields = fields

    def get_freshness(self, obj):
        return recommendation_freshness(obj)


class MRPPeggingSerializer(serializers.ModelSerializer):
    demand_number = serializers.CharField(source='demand_snapshot.source_metadata.demand_number', read_only=True, default=None)

    class Meta:
        model = MRPPegging
        fields = ['id', 'recommendation', 'requirement', 'demand_snapshot', 'demand_number', 'parent_requirement', 'bom_path', 'quantity', 'sequence']
        read_only_fields = fields


class MRPRunSerializer(serializers.ModelSerializer):
    demand_count = serializers.IntegerField(source='demand_snapshots.count', read_only=True)
    dependent_requirement_count = serializers.IntegerField(source='requirements.count', read_only=True)
    recommendation_count = serializers.IntegerField(source='recommendations.count', read_only=True)
    exception_count = serializers.SerializerMethodField()

    class Meta:
        model = MRPRun
        fields = ['id', 'run_number', 'status', 'horizon_start', 'horizon_end', 'cutoff_timestamp', 'planning_calendar', 'warehouse', 'plant', 'policy_version', 'input_checksum', 'result_checksum', 'parameter_snapshot', 'freshness_snapshot', 'error_summary', 'run_version', 'started_by', 'started_at', 'completed_at', 'failed_at', 'created_at', 'demand_count', 'dependent_requirement_count', 'recommendation_count', 'exception_count']
        read_only_fields = ['id', 'run_number', 'status', 'cutoff_timestamp', 'policy_version', 'input_checksum', 'result_checksum', 'parameter_snapshot', 'freshness_snapshot', 'error_summary', 'run_version', 'started_by', 'started_at', 'completed_at', 'failed_at', 'created_at', 'demand_count', 'dependent_requirement_count', 'recommendation_count', 'exception_count']

    def get_exception_count(self, obj):
        import json
        try:
            return len(json.loads(obj.error_summary or '[]'))
        except Exception:
            return 0


class MRPRunDetailSerializer(MRPRunSerializer):
    demand_snapshots = MRPDemandSnapshotSerializer(many=True, read_only=True)
    supply_snapshots = MRPSupplySnapshotSerializer(many=True, read_only=True)
    requirements = MRPRequirementSerializer(many=True, read_only=True)
    recommendations = MRPRecommendationSerializer(many=True, read_only=True)

    class Meta(MRPRunSerializer.Meta):
        fields = [*MRPRunSerializer.Meta.fields, 'demand_snapshots', 'supply_snapshots', 'requirements', 'recommendations']


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)

    class Meta:
        model = PurchaseRequisition
        fields = ['id', 'requisition_number', 'item_revision', 'item_code', 'quantity', 'unit', 'required_date', 'warehouse', 'status', 'source_recommendation', 'created_by', 'created_at']
        read_only_fields = fields


class SupplierSiteSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)

    class Meta:
        model = SupplierSite
        fields = ['id', 'supplier', 'supplier_code', 'site_code', 'name', 'site_type', 'address', 'country', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'supplier_code', 'created_at', 'updated_at']


class SupplierContactSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)

    class Meta:
        model = SupplierContact
        fields = ['id', 'supplier', 'supplier_code', 'site', 'name', 'role', 'email', 'phone', 'primary', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'supplier_code', 'created_at', 'updated_at']


class SupplierCapabilitySerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)

    class Meta:
        model = SupplierCapability
        fields = ['id', 'supplier', 'supplier_code', 'site', 'capability_code', 'description', 'process_definition', 'item_revision', 'item_code', 'status', 'lead_time_days', 'capacity_per_week', 'approved_by', 'approved_at', 'created_at', 'updated_at']
        read_only_fields = ['id', 'supplier_code', 'item_code', 'approved_by', 'approved_at', 'created_at', 'updated_at']


class ApprovedSupplierItemSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)

    class Meta:
        model = ApprovedSupplierItem
        fields = ['id', 'supplier', 'supplier_code', 'supplier_site', 'item_revision', 'item_code', 'supplier_item_code', 'status', 'lead_time_days', 'min_order_quantity', 'currency', 'last_unit_price', 'valid_from', 'valid_to', 'approval_evidence', 'approved_by', 'approved_at', 'created_at', 'updated_at']
        read_only_fields = ['id', 'supplier_code', 'item_code', 'approved_by', 'approved_at', 'created_at', 'updated_at']


class SupplierSerializer(serializers.ModelSerializer):
    sites = SupplierSiteSerializer(many=True, read_only=True)
    contacts = SupplierContactSerializer(many=True, read_only=True)
    capabilities = SupplierCapabilitySerializer(many=True, read_only=True)
    approved_items = ApprovedSupplierItemSerializer(many=True, read_only=True)

    class Meta:
        model = Supplier
        fields = ['id', 'supplier_code', 'name', 'legal_name', 'tax_identifier', 'country', 'status', 'qualification_score', 'risk_rating', 'supplier_version', 'approved_by', 'approved_at', 'created_by', 'created_at', 'updated_at', 'sites', 'contacts', 'capabilities', 'approved_items']
        read_only_fields = ['id', 'supplier_version', 'approved_by', 'approved_at', 'created_by', 'created_at', 'updated_at', 'sites', 'contacts', 'capabilities', 'approved_items']


class RFQLineSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)

    class Meta:
        model = RFQLine
        fields = ['id', 'rfq', 'line_number', 'item_revision', 'item_code', 'description', 'quantity', 'unit', 'required_date', 'delivery_warehouse', 'delivery_location', 'source_requisition', 'created_at']
        read_only_fields = ['id', 'item_code', 'created_at']


class RFQSupplierInvitationSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)

    class Meta:
        model = RFQSupplierInvitation
        fields = ['id', 'rfq', 'supplier', 'supplier_code', 'supplier_site', 'status', 'invited_at', 'responded_at', 'contact', 'created_at']
        read_only_fields = ['id', 'supplier_code', 'status', 'invited_at', 'responded_at', 'created_at']


class RequestForQuotationSerializer(serializers.ModelSerializer):
    lines = RFQLineSerializer(many=True, read_only=True)
    invitations = RFQSupplierInvitationSerializer(many=True, read_only=True)

    class Meta:
        model = RequestForQuotation
        fields = ['id', 'rfq_number', 'title', 'status', 'currency', 'due_date', 'requisition', 'project_requirement', 'wbs_activity', 'project', 'technical_requirements', 'commercial_terms', 'rfq_version', 'released_by', 'released_at', 'created_by', 'created_at', 'updated_at', 'lines', 'invitations']
        read_only_fields = ['id', 'status', 'rfq_version', 'released_by', 'released_at', 'created_by', 'created_at', 'updated_at', 'lines', 'invitations']


class SupplierQuotationLineSerializer(serializers.ModelSerializer):
    extended_price = serializers.SerializerMethodField()

    class Meta:
        model = SupplierQuotationLine
        fields = ['id', 'quotation', 'rfq_line', 'line_number', 'quantity', 'unit_price', 'extended_price', 'lead_time_days', 'promised_date', 'technical_compliance', 'commercial_rank', 'notes', 'created_at']
        read_only_fields = ['id', 'extended_price', 'created_at']

    def get_extended_price(self, obj):
        return str(obj.extended_price)


class SupplierQuotationSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)
    lines = SupplierQuotationLineSerializer(many=True, read_only=True)

    class Meta:
        model = SupplierQuotation
        fields = ['id', 'quotation_number', 'rfq', 'supplier', 'supplier_code', 'supplier_site', 'status', 'currency', 'submitted_at', 'valid_until', 'payment_terms', 'incoterms', 'technical_score', 'commercial_score', 'quotation_version', 'created_by', 'created_at', 'updated_at', 'lines']
        read_only_fields = ['id', 'supplier_code', 'status', 'submitted_at', 'quotation_version', 'created_by', 'created_at', 'updated_at', 'lines']


class QuotationComparisonSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuotationComparisonSnapshot
        fields = ['id', 'rfq', 'snapshot_number', 'policy_version', 'technical_matrix', 'commercial_matrix', 'recommended_supplier', 'recommended_quotation', 'created_by', 'created_at']
        read_only_fields = fields


class SourcingDecisionLineSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)

    class Meta:
        model = SourcingDecisionLine
        fields = ['id', 'decision', 'quotation_line', 'rfq_line', 'item_revision', 'item_code', 'quantity', 'unit', 'unit_price', 'currency', 'promised_date', 'delivery_warehouse', 'delivery_location', 'created_at']
        read_only_fields = ['id', 'item_code', 'created_at']


class SourcingDecisionSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)
    lines = SourcingDecisionLineSerializer(many=True, read_only=True)

    class Meta:
        model = SourcingDecision
        fields = ['id', 'decision_number', 'rfq', 'comparison_snapshot', 'supplier', 'supplier_code', 'quotation', 'status', 'decision_reason', 'project', 'procurement_requirement', 'decision_version', 'approved_by', 'approved_at', 'converted_purchase_order', 'created_by', 'created_at', 'updated_at', 'lines']
        read_only_fields = ['id', 'supplier_code', 'status', 'decision_version', 'approved_by', 'approved_at', 'converted_purchase_order', 'created_by', 'created_at', 'updated_at', 'lines']


class PurchaseOrderDeliveryScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseOrderDeliverySchedule
        fields = ['id', 'purchase_order_line', 'schedule_number', 'quantity', 'promised_date', 'confirmed_date', 'status', 'supplier_commitment_reference', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    open_quantity = serializers.SerializerMethodField()
    schedules = PurchaseOrderDeliveryScheduleSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = ['id', 'purchase_order', 'line_number', 'item_revision', 'item_code', 'description', 'quantity', 'received_quantity', 'accepted_quantity', 'open_quantity', 'unit', 'unit_price', 'currency', 'requisition', 'sourcing_decision_line', 'delivery_warehouse', 'delivery_location', 'required_date', 'line_version', 'created_at', 'updated_at', 'schedules']
        read_only_fields = ['id', 'item_code', 'received_quantity', 'accepted_quantity', 'open_quantity', 'line_version', 'created_at', 'updated_at', 'schedules']

    def get_open_quantity(self, obj):
        return str(obj.open_quantity)


class PurchaseOrderSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ['id', 'po_number', 'supplier', 'supplier_code', 'supplier_site', 'status', 'currency', 'sourcing_decision', 'quotation', 'project', 'wbs_activity', 'expected_delivery_date', 'supplier_reference', 'payment_terms', 'incoterms', 'po_version', 'approved_by', 'approved_at', 'released_by', 'released_at', 'acknowledged_at', 'confirmed_at', 'created_by', 'created_at', 'updated_at', 'lines']
        read_only_fields = ['id', 'supplier_code', 'status', 'po_version', 'approved_by', 'approved_at', 'released_by', 'released_at', 'acknowledged_at', 'confirmed_at', 'created_by', 'created_at', 'updated_at', 'lines']


class PurchaseReceiptLineSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)

    class Meta:
        model = PurchaseReceiptLine
        fields = ['id', 'receipt', 'purchase_order_line', 'item_revision', 'item_code', 'quantity', 'accepted_quantity', 'unit', 'warehouse', 'location', 'inventory_transaction', 'inventory_balance', 'quality_status', 'inspection_execution', 'quality_hold', 'created_at']
        read_only_fields = fields


class PurchaseReceiptSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)
    lines = PurchaseReceiptLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseReceipt
        fields = ['id', 'receipt_number', 'purchase_order', 'supplier', 'supplier_code', 'status', 'received_at', 'packing_slip', 'idempotency_key', 'received_by', 'created_at', 'lines']
        read_only_fields = fields


class PurchaseReturnSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)

    class Meta:
        model = PurchaseReturn
        fields = ['id', 'return_number', 'supplier', 'supplier_code', 'purchase_order', 'receipt_line', 'nonconformance', 'quantity', 'reason', 'status', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['id', 'return_number', 'supplier_code', 'status', 'created_by', 'created_at', 'updated_at']


class ProcurementEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcurementEvent
        fields = ['id', 'event_number', 'event_type', 'supplier', 'rfq', 'quotation', 'sourcing_decision', 'purchase_order', 'receipt', 'actor', 'event_timestamp', 'idempotency_key', 'payload', 'created_at']
        read_only_fields = fields


class CostRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostRate
        fields = ['id', 'rate_code', 'rate_type', 'currency', 'hourly_rate', 'unit_rate', 'labor_skill', 'work_center', 'machine', 'effective_from', 'effective_to', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class CurrencyRatePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = CurrencyRatePolicy
        fields = ['id', 'policy_code', 'base_currency', 'target_currency', 'rate', 'effective_from', 'source', 'created_at']
        read_only_fields = ['id', 'created_at']


class CostSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostSnapshot
        fields = ['id', 'snapshot_number', 'snapshot_type', 'policy_version', 'currency', 'item_revision', 'production_order', 'maintenance_work_order', 'project', 'purchase_receipt', 'material_cost', 'labor_cost', 'machine_cost', 'subcontract_cost', 'overhead_cost', 'total_cost', 'evidence', 'created_by', 'created_at']
        read_only_fields = fields


class SupplierPerformanceSnapshotSerializer(serializers.ModelSerializer):
    supplier_code = serializers.CharField(source='supplier.supplier_code', read_only=True)

    class Meta:
        model = SupplierPerformanceSnapshot
        fields = ['id', 'supplier', 'supplier_code', 'snapshot_number', 'period_start', 'period_end', 'on_time_delivery_percent', 'quality_acceptance_percent', 'average_lead_time_days', 'defect_count', 'receipt_count', 'score', 'evidence', 'created_at']
        read_only_fields = fields


class OperationalKPISnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = OperationalKPISnapshot
        fields = ['id', 'snapshot_number', 'period_start', 'period_end', 'plant', 'work_center', 'machine', 'production_order', 'oee_percent', 'availability_percent', 'performance_percent', 'quality_percent', 'throughput_quantity', 'cycle_time_seconds', 'setup_seconds', 'wait_seconds', 'yield_percent', 'scrap_percent', 'rework_percent', 'schedule_adherence_percent', 'utilization_percent', 'evidence', 'created_by', 'created_at']
        read_only_fields = fields


class InspectionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = InspectionPlan
        fields = ['id', 'plan_code', 'name', 'description', 'inspection_type', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class InspectionCharacteristicSerializer(serializers.ModelSerializer):
    effective_lower_limit = serializers.SerializerMethodField()
    effective_upper_limit = serializers.SerializerMethodField()

    class Meta:
        model = InspectionCharacteristic
        fields = [
            'id', 'inspection_plan_revision', 'characteristic_number', 'name',
            'description', 'characteristic_type', 'unit', 'nominal_value',
            'lower_spec_limit', 'upper_spec_limit', 'lower_tolerance',
            'upper_tolerance', 'effective_lower_limit', 'effective_upper_limit',
            'expected_boolean', 'allowed_attribute_values', 'mandatory',
            'destructive', 'sampling_method', 'sample_size', 'inspect_all',
            'acceptance_number', 'rejection_number', 'percentage', 'sequence',
            'instruction_document_revision', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'effective_lower_limit', 'effective_upper_limit', 'created_at', 'updated_at']

    def get_effective_lower_limit(self, obj):
        lower, _upper = obj.effective_limits()
        return str(lower) if lower is not None else None

    def get_effective_upper_limit(self, obj):
        _lower, upper = obj.effective_limits()
        return str(upper) if upper is not None else None


class InspectionPlanRevisionSerializer(serializers.ModelSerializer):
    plan_code = serializers.CharField(source='inspection_plan.plan_code', read_only=True)
    inspection_type = serializers.CharField(source='inspection_plan.inspection_type', read_only=True)
    characteristics = InspectionCharacteristicSerializer(many=True, read_only=True)

    class Meta:
        model = InspectionPlanRevision
        fields = [
            'id', 'inspection_plan', 'plan_code', 'inspection_type', 'revision',
            'status', 'effective_from', 'effective_to',
            'instruction_document_revision', 'change_summary', 'released_by',
            'released_at', 'superseded_by', 'created_by', 'created_at',
            'updated_at', 'characteristics',
        ]
        read_only_fields = ['id', 'plan_code', 'inspection_type', 'released_by', 'released_at', 'created_by', 'created_at', 'updated_at', 'characteristics']


class InspectionApplicabilitySerializer(serializers.ModelSerializer):
    plan_code = serializers.CharField(source='inspection_plan_revision.inspection_plan.plan_code', read_only=True)

    class Meta:
        model = InspectionApplicability
        fields = [
            'id', 'inspection_plan_revision', 'plan_code', 'item_revision',
            'process_definition', 'opc_node', 'tooling_definition',
            'applicability_type', 'mandatory', 'sequence', 'effective_from',
            'effective_to', 'notes', 'created_at',
        ]
        read_only_fields = ['id', 'plan_code', 'created_at']


class ProductionInspectionRequirementSerializer(serializers.ModelSerializer):
    plan_code = serializers.CharField(source='inspection_plan_revision.inspection_plan.plan_code', read_only=True)
    revision = serializers.CharField(source='inspection_plan_revision.revision', read_only=True)
    operation_number = serializers.IntegerField(source='operation.operation_number', read_only=True, default=None)

    class Meta:
        model = ProductionInspectionRequirement
        fields = [
            'id', 'production_order', 'operation', 'operation_number',
            'source_applicability', 'inspection_plan_revision', 'plan_code',
            'revision', 'inspection_type', 'mandatory',
            'sampling_policy_snapshot', 'instruction_document_revision',
            'sequence', 'source_metadata_snapshot', 'created_at',
        ]
        read_only_fields = fields


class QualityEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.get_username', read_only=True)

    class Meta:
        model = QualityEvent
        fields = [
            'id', 'production_order', 'inspection_execution',
            'event_sequence', 'event_type', 'actor', 'actor_name',
            'event_timestamp', 'server_recorded_at', 'idempotency_key',
            'previous_version', 'new_version', 'notes', 'metadata',
        ]
        read_only_fields = fields


class InspectionSampleSerializer(serializers.ModelSerializer):
    serial_number = serializers.CharField(source='serial.serial_number', read_only=True, default=None)
    lot_number = serializers.CharField(source='lot.lot_number', read_only=True, default=None)

    class Meta:
        model = InspectionSample
        fields = ['id', 'inspection_execution', 'sample_number', 'serial', 'serial_number', 'lot', 'lot_number', 'quantity_represented', 'status', 'created_at', 'updated_at']
        read_only_fields = fields


class InspectionMeasurementSerializer(serializers.ModelSerializer):
    characteristic_number = serializers.CharField(source='characteristic.characteristic_number', read_only=True)
    characteristic_name = serializers.CharField(source='characteristic.name', read_only=True)
    observed_by_name = serializers.CharField(source='observed_by.get_username', read_only=True)

    class Meta:
        model = InspectionMeasurement
        fields = [
            'id', 'inspection_execution', 'sample', 'characteristic',
            'characteristic_number', 'characteristic_name',
            'measurement_sequence', 'numeric_value', 'boolean_value',
            'attribute_value', 'text_value', 'observed_by',
            'observed_by_name', 'observed_at', 'server_recorded_at',
            'evaluation_result', 'nonconforming', 'notes', 'supersedes',
            'quality_event', 'created_at',
        ]
        read_only_fields = fields


class QualityHoldSerializer(serializers.ModelSerializer):
    placed_by_name = serializers.CharField(source='placed_by.get_username', read_only=True)
    released_by_name = serializers.CharField(source='released_by.get_username', read_only=True, default=None)

    class Meta:
        model = QualityHold
        fields = [
            'id', 'production_order', 'operation_execution',
            'inspection_execution', 'serial', 'lot', 'scope', 'reason',
            'active', 'placed_by', 'placed_by_name', 'placed_at',
            'released_by', 'released_by_name', 'released_at',
            'release_reason', 'placed_event', 'released_event',
        ]
        read_only_fields = fields


class QualityDispositionSerializer(serializers.ModelSerializer):
    proposed_by_name = serializers.CharField(source='proposed_by.get_username', read_only=True)

    class Meta:
        model = QualityDisposition
        fields = [
            'id', 'ncr', 'disposition_type', 'quantity', 'reason',
            'instructions', 'target_rework_edge',
            'instruction_document_revision', 'status', 'proposed_by',
            'proposed_by_name', 'approved_by', 'implemented_by',
            'created_at', 'approved_at', 'implemented_at', 'reinspection',
            'rework_cycle',
        ]
        read_only_fields = fields


class NonconformanceRecordSerializer(serializers.ModelSerializer):
    allowed_actions = serializers.SerializerMethodField()
    dispositions = QualityDispositionSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_username', read_only=True)
    inventory_transactions = serializers.SerializerMethodField()

    class Meta:
        model = NonconformanceRecord
        fields = [
            'id', 'ncr_number', 'production_order', 'operation_execution',
            'inspection_execution', 'measurement', 'serial', 'lot',
            'defect_code', 'defect_description', 'severity',
            'affected_quantity', 'status', 'ncr_version', 'owner',
            'created_by', 'created_by_name', 'created_at', 'closed_at',
            'allowed_actions', 'dispositions', 'inventory_transactions',
        ]
        read_only_fields = fields

    def get_allowed_actions(self, obj):
        return allowed_ncr_actions(obj)

    def get_inventory_transactions(self, obj):
        return InventoryTransactionSerializer(obj.inventory_transactions.all()[:20]).data


class InspectionExecutionSerializer(serializers.ModelSerializer):
    plan_code = serializers.CharField(source='requirement.inspection_plan_revision.inspection_plan.plan_code', read_only=True)
    plan_revision = serializers.CharField(source='requirement.inspection_plan_revision.revision', read_only=True)
    inspection_plan_revision = serializers.UUIDField(source='requirement.inspection_plan_revision_id', read_only=True)
    operation_number = serializers.IntegerField(source='requirement.operation.operation_number', read_only=True, default=None)
    allowed_actions = serializers.SerializerMethodField()
    evaluation_summary = serializers.SerializerMethodField()
    samples = InspectionSampleSerializer(many=True, read_only=True)
    measurements = InspectionMeasurementSerializer(many=True, read_only=True)
    quality_events = QualityEventSerializer(many=True, read_only=True)
    quality_holds = QualityHoldSerializer(many=True, read_only=True)
    nonconformances = NonconformanceRecordSerializer(many=True, read_only=True)

    class Meta:
        model = InspectionExecution
        fields = [
            'id', 'requirement', 'inspection_plan_revision', 'plan_code', 'plan_revision',
            'production_order', 'operation_execution', 'operation_number',
            'execution_cycle', 'serial', 'lot', 'status',
            'inspection_version', 'assigned_inspector', 'started_at',
            'completed_at', 'evaluated_at', 'result',
            'disposition_status', 'created_at', 'updated_at',
            'allowed_actions', 'evaluation_summary', 'samples',
            'measurements', 'quality_events', 'quality_holds',
            'nonconformances',
        ]
        read_only_fields = fields

    def get_allowed_actions(self, obj):
        return allowed_inspection_actions(obj)

    def get_evaluation_summary(self, obj):
        return aggregate_inspection(obj)


class ProductionOrderSerializer(serializers.ModelSerializer):
    operations = ManufacturingOperationSerializer(many=True, read_only=True)
    precedences = ManufacturingOperationPrecedenceSerializer(many=True, read_only=True)
    material_requirements = ProductionMaterialRequirementSerializer(many=True, read_only=True)
    tooling_requirements = ProductionToolingRequirementSerializer(many=True, read_only=True)
    document_requirements = ProductionDocumentRequirementSerializer(many=True, read_only=True)
    inspection_requirements = ProductionInspectionRequirementSerializer(many=True, read_only=True)
    quality_summary = serializers.SerializerMethodField()
    material_summary = serializers.SerializerMethodField()
    latest_validation_evidence = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    item_revision_code = serializers.CharField(source='item_revision.revision', read_only=True)
    source_opc_title = serializers.CharField(source='source_opc_diagram.title', read_only=True)
    source_mbom_revision = serializers.CharField(source='source_manufacturing_bom_revision.revision', read_only=True)

    class Meta:
        model = ProductionOrder
        fields = [
            'id', 'order_number', 'item_revision', 'item_code', 'item_revision_code',
            'planned_quantity', 'unit', 'status', 'priority', 'planned_start',
            'planned_end', 'requested_completion_date', 'description',
            'source_opc_diagram', 'source_opc_title', 'source_graph_version',
            'source_validation_evidence', 'source_manufacturing_bom_revision',
            'source_mbom_revision', 'source_snapshot', 'source_snapshot_checksum',
            'generator_policy_version', 'generated_at', 'order_version',
            'released_by', 'released_at', 'cancelled_by', 'cancelled_at',
            'created_by', 'created_at', 'updated_at', 'allowed_actions',
            'latest_validation_evidence', 'operations', 'precedences',
            'material_requirements', 'tooling_requirements', 'document_requirements',
            'inspection_requirements', 'quality_summary', 'material_summary',
        ]
        read_only_fields = [
            'id', 'order_number', 'status', 'source_graph_version',
            'source_validation_evidence', 'source_snapshot',
            'source_snapshot_checksum', 'generator_policy_version',
            'generated_at', 'order_version', 'released_by', 'released_at',
            'cancelled_by', 'cancelled_at', 'created_by', 'created_at',
            'updated_at', 'allowed_actions', 'latest_validation_evidence',
            'operations', 'precedences', 'material_requirements',
            'tooling_requirements', 'document_requirements',
            'inspection_requirements', 'quality_summary', 'material_summary',
        ]

    def get_allowed_actions(self, obj):
        return allowed_order_actions(obj)

    def get_latest_validation_evidence(self, obj):
        evidence = obj.validation_evidence.order_by('-created_at').first()
        return ProductionOrderValidationEvidenceSerializer(evidence).data if evidence else None

    def get_quality_summary(self, obj):
        return order_quality_summary(obj)

    def get_material_summary(self, obj):
        return order_material_summary(obj)


class ProductionOrderSummarySerializer(serializers.ModelSerializer):
    allowed_actions = serializers.SerializerMethodField()
    item_code = serializers.CharField(source='item_revision.item.item_code', read_only=True)
    item_revision_code = serializers.CharField(source='item_revision.revision', read_only=True)

    class Meta:
        model = ProductionOrder
        fields = [
            'id', 'order_number', 'item_revision', 'item_code',
            'item_revision_code', 'planned_quantity', 'unit', 'status',
            'priority', 'planned_start', 'planned_end',
            'requested_completion_date', 'source_opc_diagram',
            'source_graph_version', 'source_manufacturing_bom_revision',
            'source_snapshot_checksum', 'generated_at', 'order_version',
            'allowed_actions', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_allowed_actions(self, obj):
        return allowed_order_actions(obj)
    NonconformanceRecord,
    ProductionInspectionRequirement,
    QualityDisposition,
    QualityEvent,
    QualityHold,
