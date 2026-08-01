from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.serializers import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.permissions import CanManageEngineering
from rest_framework.response import Response

from enterprise_items.models import BOMLine, BOMRevision, ItemRevision

from .models import (
    CalendarException,
    CapacityBucket,
    ControlledDocument,
    ControlledDocumentRevision,
    EngineeringChangeObjectLink,
    EngineeringChangeOrder,
    EngineeringChangeRequest,
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
    LaborSkill,
    ManufacturingGenealogyLink,
    ManufacturingOperationPrecedence,
    MRPPegging,
    MRPRecommendation,
    MRPRun,
    NonconformanceRecord,
    OperationExecution,
    OPCDiagram,
    OPCEdge,
    OPCNode,
    Plant,
    PlanningCalendar,
    PlanningDemand,
    ProductionDocumentRequirement,
    ProductionLot,
    ProductionMaterialConsumption,
    ProductionMaterialRequirement,
    ProcessDefinition,
    ProductionOrder,
    ProductionSerial,
    ProductionToolingRequirement,
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
    QualityHold,
    RequestForQuotation,
    RFQLine,
    RFQSupplierInvitation,
    ResourceCalendar,
    SourcingDecision,
    SourcingDecisionLine,
    Supplier,
    SupplierCapability,
    SupplierContact,
    SupplierPerformanceSnapshot,
    SupplierQuotation,
    SupplierQuotationLine,
    SupplierSite,
    ScheduledOperationAssignment,
    SchedulingCalendarSnapshot,
    SchedulingException,
    SchedulingOperationSnapshot,
    SchedulingPolicy,
    SchedulingRun,
    ScheduledSupply,
    Shift,
    StorageLocation,
    ToolingDefinition,
    Warehouse,
    WorkCenter,
    WorkCenterCapacity,
    WorkCenterLaborCapacity,
)
from .inventory_services import (
    InventoryConflict,
    adjust_inventory,
    issue_material,
    material_queue,
    quality_release_stock,
    quarantine_stock,
    receive_inventory,
    release_reservation,
    reserve_material,
    return_material as return_material_to_inventory,
    scrap_stock,
    transfer_inventory,
)
from .mrp_services import (
    MRPConflict,
    convert_recommendation,
    create_demand,
    run_mrp,
    transition_demand,
    transition_recommendation,
    update_demand,
)
from .scheduling_services import (
    SchedulingConflict,
    apply_scheduling_run,
    run_finite_schedule,
    transition_scheduling_run,
)
from .maintenance_services import (
    MaintenanceConflict,
    create_maintenance_request,
    create_work_order,
    generate_preventive_work_orders,
    issue_spare_part,
    maintenance_dashboard,
    record_checklist_result,
    record_meter_reading,
    reliability_metrics,
    return_spare_part,
    transition_work_order,
)
from .quality_services import (
    InspectionConflict,
    NonconformanceConflict,
    approve_disposition,
    complete_inspection,
    create_ncr,
    ensure_inspections_for_order,
    evaluate_inspection,
    implement_disposition,
    place_quality_hold,
    propose_disposition,
    record_measurement,
    release_inspection_plan_revision,
    release_quality_hold,
    start_inspection,
    transition_ncr,
)
from .execution_services import (
    ExecutionConflict,
    acknowledge_document,
    command_execution,
    consume_material,
    ensure_executions_for_order,
    link_genealogy,
    progress_rollup,
    record_tooling_usage,
)
from .production_services import (
    ProductionOrderConflict,
    cancel_production_order,
    create_production_order,
    generate_order_baseline,
    release_production_order,
    update_production_order,
    validate_production_order,
)
from .cost_performance_services import (
    create_maintenance_cost_snapshot,
    create_operational_kpi_snapshot,
    create_production_cost_snapshot,
    create_project_cost_snapshot,
    create_receipt_cost_snapshot,
)
from .phase6_services import (
    analyze_change_impact,
    calculate_document_file_metadata,
    change_order_transition,
    change_request_transition,
    document_download_response,
    release_document_revision,
    released_mbom_for_item_revision,
)
from .procurement_services import (
    ProcurementConflict,
    approve_sourcing_decision,
    approve_supplier_item,
    convert_sourcing_to_purchase_order,
    create_purchase_return,
    create_quotation_comparison,
    create_supplier_performance_snapshot,
    receive_purchase_order_line,
    submit_quotation,
    transition_purchase_order,
    transition_rfq,
    transition_supplier,
)
from .graph_persistence import OPCGraphConflict, increment_graph_version, save_opc_graph
from .release_validation import OPCValidationConflict, validate_opc_release_readiness
from .services import (
    approve_opc_revision,
    clone_opc_revision,
    ensure_edge_editable,
    ensure_node_editable,
    obsolete_opc_revision,
    release_opc_revision,
    return_opc_revision_to_draft,
    return_opc_revision_to_review,
    submit_opc_revision,
    OPCReleaseValidationError,
)
from .serializers import (
    CalendarExceptionSerializer,
    CapacityBucketSerializer,
    OPCDiagramSerializer,
    OPCDiagramSummarySerializer,
    OPCEdgeSerializer,
    OPCNodeSerializer,
    MachineAssetSerializer,
    ControlledDocumentRevisionSerializer,
    ControlledDocumentSerializer,
    InspectionApplicabilitySerializer,
    InspectionCharacteristicSerializer,
    InspectionExecutionSerializer,
    InspectionPlanRevisionSerializer,
    InspectionPlanSerializer,
    InventoryBalanceSerializer,
    InventoryReservationSerializer,
    InventoryTransactionSerializer,
    ItemPlanningPolicySerializer,
    LaborSkillSerializer,
    MachineCapacitySerializer,
    MachineDowntimeEventSerializer,
    MachineMaintenanceProfileSerializer,
    MaintainableAssetSerializer,
    MaintenanceChecklistResultSerializer,
    MaintenanceRequestSerializer,
    MaintenanceEventSerializer,
    MaintenanceSparePartIssueSerializer,
    MaintenanceSparePartRequirementSerializer,
    MaintenanceTaskChecklistItemSerializer,
    MaintenanceTaskTemplateSerializer,
    MaintenanceWorkOrderSerializer,
    PreventiveMaintenancePlanSerializer,
    AssetMeterSerializer,
    AssetMeterReadingSerializer,
    FailureCodeSerializer,
    CauseCodeSerializer,
    RemedyCodeSerializer,
    MRPPeggingSerializer,
    MRPRecommendationSerializer,
    MRPRunDetailSerializer,
    MRPRunSerializer,
    NonconformanceRecordSerializer,
    OperationExecutionSerializer,
    EngineeringChangeObjectLinkSerializer,
    EngineeringChangeOrderSerializer,
    EngineeringChangeRequestSerializer,
    PlantSerializer,
    PlanningCalendarSerializer,
    PlanningDemandSerializer,
    ProcessDefinitionSerializer,
    ProductionLotSerializer,
    ProductionOrderSerializer,
    ProductionOrderSummarySerializer,
    ProductionSerialSerializer,
    ApprovedSupplierItemSerializer,
    CostRateSerializer,
    CostSnapshotSerializer,
    CurrencyRatePolicySerializer,
    OperationalKPISnapshotSerializer,
    ProcurementEventSerializer,
    PurchaseOrderDeliveryScheduleSerializer,
    PurchaseOrderLineSerializer,
    PurchaseOrderSerializer,
    PurchaseReceiptLineSerializer,
    PurchaseReceiptSerializer,
    PurchaseReturnSerializer,
    PurchaseRequisitionSerializer,
    QuotationComparisonSnapshotSerializer,
    QualityDispositionSerializer,
    QualityHoldSerializer,
    RequestForQuotationSerializer,
    RFQLineSerializer,
    RFQSupplierInvitationSerializer,
    ResourceCalendarSerializer,
    SourcingDecisionLineSerializer,
    SourcingDecisionSerializer,
    SupplierCapabilitySerializer,
    SupplierContactSerializer,
    SupplierPerformanceSnapshotSerializer,
    SupplierQuotationLineSerializer,
    SupplierQuotationSerializer,
    SupplierSerializer,
    SupplierSiteSerializer,
    ScheduledOperationAssignmentSerializer,
    SchedulingCalendarSnapshotSerializer,
    SchedulingExceptionSerializer,
    SchedulingOperationSnapshotSerializer,
    SchedulingPolicySerializer,
    SchedulingRunDetailSerializer,
    SchedulingRunSerializer,
    ScheduledSupplySerializer,
    ShiftSerializer,
    StorageLocationSerializer,
    ToolingDefinitionSerializer,
    WarehouseSerializer,
    WorkCenterSerializer,
    WorkCenterCapacitySerializer,
    WorkCenterLaborCapacitySerializer,
)


def _validation_payload(exc):
    if hasattr(exc, 'validation_result'):
        return {'validation': exc.validation_result}
    if hasattr(exc, 'message_dict'):
        return exc.message_dict
    if hasattr(exc, 'messages'):
        return exc.messages
    return str(exc)


def _changed(instance, before, fields):
    return any(before.get(field) != getattr(instance, field) for field in fields)


def _active_param(request):
    value = request.query_params.get('active')
    if value is None:
        return True
    if str(value).lower() in {'all', 'any'}:
        return None
    return str(value).lower() not in {'0', 'false', 'no'}


class PlantViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PlantSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Plant.objects.all()
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(code__icontains=search) | queryset.filter(name__icontains=search)
        return queryset.distinct()


class WorkCenterViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = WorkCenterSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = WorkCenter.objects.select_related('plant')
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        plant = self.request.query_params.get('plant')
        if plant:
            queryset = queryset.filter(plant_id=plant)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(code__icontains=search) | queryset.filter(name__icontains=search)
        return queryset.distinct()


class MachineAssetViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MachineAssetSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = MachineAsset.objects.select_related('plant', 'work_center')
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        plant = self.request.query_params.get('plant')
        if plant:
            queryset = queryset.filter(plant_id=plant)
        work_center = self.request.query_params.get('work_center')
        if work_center:
            queryset = queryset.filter(work_center_id=work_center)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(asset_code__icontains=search) | queryset.filter(name__icontains=search)
        return queryset.distinct()


class ProcessDefinitionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProcessDefinitionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = ProcessDefinition.objects.all()
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        execution = self.request.query_params.get('execution_classification') or self.request.query_params.get('execution')
        if execution:
            queryset = queryset.filter(execution_classification=execution)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(process_code__icontains=search) | queryset.filter(name__icontains=search)
        return queryset.distinct()


class ToolingDefinitionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ToolingDefinitionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = ToolingDefinition.objects.all()
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(tooling_code__icontains=search) | queryset.filter(name__icontains=search)
        return queryset.distinct()


class ControlledDocumentViewSet(viewsets.ModelViewSet):
    queryset = ControlledDocument.objects.prefetch_related('revisions')
    serializer_class = ControlledDocumentSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = super().get_queryset()
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        document_type = self.request.query_params.get('document_type')
        if document_type:
            queryset = queryset.filter(document_type=document_type)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(document_number__icontains=search) | queryset.filter(title__icontains=search)
        return queryset.distinct()

    def perform_create(self, serializer):
        plan_number = serializer.validated_data.get('plan_number') or f'PM-{timezone.now():%Y%m%d}-{PreventiveMaintenancePlan.objects.count() + 1:06d}'
        serializer.save(created_by=self.request.user, plan_number=plan_number)


class ControlledDocumentRevisionViewSet(viewsets.ModelViewSet):
    queryset = ControlledDocumentRevision.objects.select_related('document', 'released_by', 'created_by', 'superseded_by')
    serializer_class = ControlledDocumentRevisionSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = super().get_queryset()
        document = self.request.query_params.get('document')
        if document:
            queryset = queryset.filter(document_id=document)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        document_type = self.request.query_params.get('document_type')
        if document_type:
            queryset = queryset.filter(document__document_type=document_type)
        return queryset

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        calculate_document_file_metadata(instance)
        instance.save(update_fields=['checksum_sha256', 'mime_type', 'original_filename', 'file_size', 'updated_at'])

    def perform_update(self, serializer):
        try:
            instance = serializer.save()
        except DjangoValidationError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        calculate_document_file_metadata(instance)
        instance.save(update_fields=['checksum_sha256', 'mime_type', 'original_filename', 'file_size', 'updated_at'])

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        effective_from = request.data.get('effective_from')
        parsed_date = parse_date(effective_from) if effective_from else None
        if effective_from and not parsed_date:
            raise DRFValidationError({'effective_from': 'Use YYYY-MM-DD date format.'})
        try:
            revision = release_document_revision(self.get_object(), actor=request.user, effective_from=parsed_date, supersede_current=False)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(revision).data)

    @action(detail=True, methods=['post'])
    def supersede(self, request, pk=None):
        effective_from = request.data.get('effective_from')
        parsed_date = parse_date(effective_from) if effective_from else None
        if effective_from and not parsed_date:
            raise DRFValidationError({'effective_from': 'Use YYYY-MM-DD date format.'})
        try:
            revision = release_document_revision(self.get_object(), actor=request.user, effective_from=parsed_date, supersede_current=True)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(revision).data)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        try:
            return document_download_response(self.get_object())
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc


class OPCMaterialCandidateViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def list(self, request):
        item_revision_id = request.query_params.get('item_revision')
        if not item_revision_id:
            raise DRFValidationError({'item_revision': 'This query parameter is required.'})
        mbom = released_mbom_for_item_revision(item_revision_id)
        if not mbom:
            return Response({'manufacturing_bom_revision': None, 'lines': []})
        lines = BOMLine.objects.filter(bom_revision=mbom).select_related('component_item_revision__item').order_by('sequence')
        return Response({
            'manufacturing_bom_revision': str(mbom.pk),
            'bom_code': mbom.bom.bom_code,
            'revision': mbom.revision,
            'lines': [
                {
                    'id': str(line.pk),
                    'sequence': line.sequence,
                    'component_item_revision': str(line.component_item_revision_id),
                    'component_item_code': line.component_item_revision.item.item_code,
                    'component_revision': line.component_item_revision.revision,
                    'component_title': line.component_item_revision.title,
                    'quantity': str(line.quantity),
                    'unit': line.unit,
                    'scrap_percent': str(line.scrap_percent),
                    'is_phantom': line.is_phantom,
                    'is_optional': line.is_optional,
                }
                for line in lines
            ],
        })


class EngineeringChangeRequestViewSet(viewsets.ModelViewSet):
    queryset = EngineeringChangeRequest.objects.all()
    serializer_class = EngineeringChangeRequestSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        return Response(self.get_serializer(change_request_transition(self.get_object(), actor=request.user, status=EngineeringChangeRequest.STATUS_SUBMITTED)).data)

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        return Response(self.get_serializer(change_request_transition(self.get_object(), actor=request.user, status=EngineeringChangeRequest.STATUS_ACCEPTED)).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return Response(self.get_serializer(change_request_transition(self.get_object(), actor=request.user, status=EngineeringChangeRequest.STATUS_REJECTED)).data)


class EngineeringChangeOrderViewSet(viewsets.ModelViewSet):
    queryset = EngineeringChangeOrder.objects.select_related('request')
    serializer_class = EngineeringChangeOrderSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        return Response(self.get_serializer(change_order_transition(self.get_object(), actor=request.user, status=EngineeringChangeOrder.STATUS_UNDER_REVIEW)).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return Response(self.get_serializer(change_order_transition(self.get_object(), actor=request.user, status=EngineeringChangeOrder.STATUS_APPROVED)).data)

    @action(detail=True, methods=['post'])
    def implement(self, request, pk=None):
        return Response(self.get_serializer(change_order_transition(self.get_object(), actor=request.user, status=EngineeringChangeOrder.STATUS_IMPLEMENTED)).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        return Response(self.get_serializer(change_order_transition(self.get_object(), actor=request.user, status=EngineeringChangeOrder.STATUS_CANCELLED)).data)


class EngineeringChangeObjectLinkViewSet(viewsets.ModelViewSet):
    queryset = EngineeringChangeObjectLink.objects.select_related('request', 'order')
    serializer_class = EngineeringChangeObjectLinkSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]


class EngineeringImpactViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def list(self, request):
        object_type = request.query_params.get('object_type')
        object_id = request.query_params.get('object_id')
        if not object_type or not object_id:
            raise DRFValidationError({'object': 'object_type and object_id are required.'})
        return Response(analyze_change_impact(object_type=object_type, object_id=object_id))


class ProductionOrderViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = ProductionOrder.objects.select_related(
            'item_revision__item',
            'source_opc_diagram',
            'source_manufacturing_bom_revision__bom',
            'source_validation_evidence',
            'released_by',
            'cancelled_by',
            'created_by',
        ).prefetch_related(
            'operations__eligible_machines',
            'precedences__predecessor',
            'precedences__successor',
            'material_requirements__operation',
            'tooling_requirements__operation',
            'document_requirements__operation',
            'validation_evidence',
        )
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(order_number__icontains=search)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        item_revision = self.request.query_params.get('item_revision')
        if item_revision:
            queryset = queryset.filter(item_revision_id=item_revision)
        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return ProductionOrderSummarySerializer
        return ProductionOrderSerializer

    def create(self, request, *args, **kwargs):
        serializer = ProductionOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order = create_production_order(actor=request.user, **serializer.validated_data)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(order).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = ProductionOrderSerializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            order = update_production_order(
                instance,
                actor=request.user,
                expected_version=request.data.get('expected_version'),
                **serializer.validated_data,
            )
        except ProductionOrderConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(order).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def generate(self, request, pk=None):
        try:
            order = generate_order_baseline(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'))
        except ProductionOrderConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None):
        return Response(validate_production_order(self.get_object(), actor=request.user, persist=True))

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        try:
            order = release_production_order(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'))
        except ProductionOrderConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        try:
            order = cancel_production_order(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'))
        except ProductionOrderConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=['post'], url_path='initialize-executions')
    def initialize_executions(self, request, pk=None):
        try:
            ensure_executions_for_order(self.get_object(), actor=request.user)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(OperationExecutionSerializer(self.get_object().operation_executions.all(), many=True, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='execution-progress')
    def execution_progress(self, request, pk=None):
        return Response(progress_rollup(self.get_object()))

    @action(detail=True, methods=['post'], url_path='initialize-inspections')
    def initialize_inspections(self, request, pk=None):
        try:
            ensure_inspections_for_order(self.get_object(), actor=request.user)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(InspectionExecutionSerializer(self.get_object().inspection_executions.all(), many=True, context={'request': request}).data)


class OperationExecutionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OperationExecutionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = OperationExecution.objects.select_related(
            'production_order',
            'manufacturing_operation',
            'assigned_operator',
            'assigned_machine',
        ).prefetch_related(
            'cycles',
            'events',
            'material_consumptions__component_item_revision__item',
            'material_consumptions__lot',
            'material_consumptions__serial',
            'tooling_usages__tooling_definition',
            'document_acknowledgements__controlled_document_revision__document',
        )
        production_order = self.request.query_params.get('production_order') or self.request.query_params.get('order')
        if production_order:
            queryset = queryset.filter(production_order_id=production_order)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        work_center = self.request.query_params.get('work_center')
        if work_center:
            queryset = queryset.filter(manufacturing_operation__work_center_id=work_center)
        machine = self.request.query_params.get('machine')
        if machine:
            queryset = queryset.filter(assigned_machine_id=machine)
        operator = self.request.query_params.get('operator')
        if operator:
            queryset = queryset.filter(assigned_operator_id=operator)
        return queryset.order_by('production_order__order_number', 'manufacturing_operation__sequence')

    def _respond(self, service, **kwargs):
        try:
            execution, _result = service(self.get_object(), actor=self.request.user, **kwargs)
        except ExecutionConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(execution).data)

    def _command(self, command, **payload):
        return self._respond(
            command_execution,
            expected_version=self.request.data.get('expected_version'),
            command=command,
            idempotency_key=self.request.data.get('idempotency_key', ''),
            **payload,
        )

    @action(detail=True, methods=['post'], url_path='dispatch', url_name='dispatch')
    def dispatch_execution(self, request, pk=None):
        return self._command('dispatch', notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'], url_path='assign-operator')
    def assign_operator(self, request, pk=None):
        operator_id = request.data.get('operator') or request.data.get('operator_id')
        operator = get_user_model().objects.get(pk=operator_id) if operator_id else request.user
        return self._command('assign_operator', operator=operator, notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'], url_path='assign-machine')
    def assign_machine(self, request, pk=None):
        machine = MachineAsset.objects.get(pk=request.data.get('machine') or request.data.get('machine_id'))
        return self._command('assign_machine', machine=machine, notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        return self._command('start', notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        return self._command('pause', notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        return self._command('resume', notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'], url_path='record-output')
    def record_output(self, request, pk=None):
        return self._command(
            'record_output',
            produced_quantity=request.data.get('produced_quantity', 0),
            accepted_quantity=request.data.get('accepted_quantity', 0),
            rejected_quantity=request.data.get('rejected_quantity', 0),
            scrapped_quantity=request.data.get('scrapped_quantity', 0),
            rework_quantity=request.data.get('rework_quantity', 0),
            notes=request.data.get('notes', ''),
        )

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        return self._command('complete', notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'])
    def block(self, request, pk=None):
        return self._command('block', reason=request.data.get('reason', ''), notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'])
    def unblock(self, request, pk=None):
        return self._command('unblock', notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        return self._command('cancel', notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'], url_path='ack-document')
    def ack_document(self, request, pk=None):
        requirement = ProductionDocumentRequirement.objects.get(pk=request.data.get('requirement') or request.data.get('requirement_id'))
        return self._respond(
            acknowledge_document,
            expected_version=request.data.get('expected_version'),
            requirement=requirement,
            idempotency_key=request.data.get('idempotency_key', ''),
            notes=request.data.get('notes', ''),
        )

    @action(detail=True, methods=['post'], url_path='record-tooling')
    def record_tooling(self, request, pk=None):
        requirement = ProductionToolingRequirement.objects.get(pk=request.data.get('requirement') or request.data.get('requirement_id'))
        return self._respond(
            record_tooling_usage,
            expected_version=request.data.get('expected_version'),
            requirement=requirement,
            quantity=request.data.get('quantity', 1),
            idempotency_key=request.data.get('idempotency_key', ''),
            notes=request.data.get('notes', ''),
        )

    @action(detail=True, methods=['post'], url_path='consume-material')
    def consume_material(self, request, pk=None):
        requirement = ProductionMaterialRequirement.objects.get(pk=request.data.get('requirement') or request.data.get('requirement_id'))
        lot_id = request.data.get('lot') or request.data.get('lot_id')
        serial_id = request.data.get('serial') or request.data.get('serial_id')
        return self._respond(
            consume_material,
            expected_version=request.data.get('expected_version'),
            requirement=requirement,
            quantity=request.data.get('quantity'),
            lot=ProductionLot.objects.get(pk=lot_id) if lot_id else None,
            serial=ProductionSerial.objects.get(pk=serial_id) if serial_id else None,
            idempotency_key=request.data.get('idempotency_key', ''),
            notes=request.data.get('notes', ''),
        )

    @action(detail=True, methods=['post'], url_path='reverse-material')
    def reverse_material(self, request, pk=None):
        requirement = ProductionMaterialRequirement.objects.get(pk=request.data.get('requirement') or request.data.get('requirement_id'))
        return self._respond(
            consume_material,
            expected_version=request.data.get('expected_version'),
            requirement=requirement,
            quantity=request.data.get('quantity'),
            reverse=True,
            idempotency_key=request.data.get('idempotency_key', ''),
            notes=request.data.get('notes', ''),
        )

    @action(detail=True, methods=['post'])
    def rework(self, request, pk=None):
        edge = ManufacturingOperationPrecedence.objects.get(pk=request.data.get('rework_edge') or request.data.get('rework_edge_id'))
        return self._command(
            'rework',
            rework_edge=edge,
            quantity=request.data.get('quantity'),
            reason=request.data.get('reason', ''),
            notes=request.data.get('notes', ''),
        )

    @action(detail=True, methods=['post'], url_path='link-genealogy')
    def link_genealogy(self, request, pk=None):
        consumption = ProductionMaterialConsumption.objects.get(pk=request.data.get('consumption') or request.data.get('consumption_id'))
        parent_lot_id = request.data.get('parent_lot') or request.data.get('parent_lot_id')
        parent_serial_id = request.data.get('parent_serial') or request.data.get('parent_serial_id')
        component_lot_id = request.data.get('component_lot') or request.data.get('component_lot_id')
        component_serial_id = request.data.get('component_serial') or request.data.get('component_serial_id')
        return self._respond(
            link_genealogy,
            expected_version=request.data.get('expected_version'),
            consumption=consumption,
            parent_lot=ProductionLot.objects.get(pk=parent_lot_id) if parent_lot_id else None,
            parent_serial=ProductionSerial.objects.get(pk=parent_serial_id) if parent_serial_id else None,
            component_lot=ProductionLot.objects.get(pk=component_lot_id) if component_lot_id else None,
            component_serial=ProductionSerial.objects.get(pk=component_serial_id) if component_serial_id else None,
            idempotency_key=request.data.get('idempotency_key', ''),
            notes=request.data.get('notes', ''),
        )


class ProductionLotViewSet(viewsets.ModelViewSet):
    serializer_class = ProductionLotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = ProductionLot.objects.select_related('item_revision__item', 'production_order', 'created_by')
        production_order = self.request.query_params.get('production_order') or self.request.query_params.get('order')
        if production_order:
            queryset = queryset.filter(production_order_id=production_order)
        item_revision = self.request.query_params.get('item_revision')
        if item_revision:
            queryset = queryset.filter(item_revision_id=item_revision)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ProductionSerialViewSet(viewsets.ModelViewSet):
    serializer_class = ProductionSerialSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = ProductionSerial.objects.select_related('item_revision__item', 'production_order', 'lot', 'created_by')
        production_order = self.request.query_params.get('production_order') or self.request.query_params.get('order')
        if production_order:
            queryset = queryset.filter(production_order_id=production_order)
        item_revision = self.request.query_params.get('item_revision')
        if item_revision:
            queryset = queryset.filter(item_revision_id=item_revision)
        lot = self.request.query_params.get('lot')
        if lot:
            queryset = queryset.filter(lot_id=lot)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class WarehouseViewSet(viewsets.ModelViewSet):
    serializer_class = WarehouseSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = Warehouse.objects.select_related('plant')
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        warehouse_type = self.request.query_params.get('warehouse_type') or self.request.query_params.get('type')
        if warehouse_type:
            queryset = queryset.filter(warehouse_type=warehouse_type)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(code__icontains=search) | queryset.filter(name__icontains=search)
        return queryset.distinct()


class StorageLocationViewSet(viewsets.ModelViewSet):
    serializer_class = StorageLocationSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = StorageLocation.objects.select_related('warehouse', 'parent')
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        warehouse = self.request.query_params.get('warehouse')
        if warehouse:
            queryset = queryset.filter(warehouse_id=warehouse)
        inventory_enabled = self.request.query_params.get('inventory_enabled')
        if inventory_enabled is not None:
            queryset = queryset.filter(inventory_enabled=str(inventory_enabled).lower() not in {'0', 'false', 'no'})
        quarantine = self.request.query_params.get('quarantine')
        if quarantine is not None:
            queryset = queryset.filter(quarantine=str(quarantine).lower() not in {'0', 'false', 'no'})
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(code__icontains=search) | queryset.filter(name__icontains=search)
        return queryset.distinct()


def _inventory_error_response(exc):
    if isinstance(exc, InventoryConflict):
        return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
    if isinstance(exc, EngineeringPermissionError):
        raise PermissionDenied(str(exc)) from exc
    if isinstance(exc, EngineeringLifecycleError):
        raise DRFValidationError(_validation_payload(exc)) from exc
    raise exc


class InventoryBalanceViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = InventoryBalanceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = InventoryBalance.objects.select_related('item_revision__item', 'warehouse', 'location', 'lot', 'serial', 'last_transaction')
        for param, field in [
            ('item_revision', 'item_revision_id'),
            ('warehouse', 'warehouse_id'),
            ('location', 'location_id'),
            ('lot', 'lot_id'),
            ('serial', 'serial_id'),
            ('stock_status', 'stock_status'),
        ]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        if self.request.query_params.get('available_only'):
            queryset = queryset.filter(stock_status=InventoryBalance.STATUS_AVAILABLE, on_hand_quantity__gt=0)
        if self.request.query_params.get('quarantine'):
            queryset = queryset.filter(stock_status=InventoryBalance.STATUS_QUARANTINED)
        return queryset

    @action(detail=False, methods=['post'])
    def receive(self, request):
        try:
            tx, balance = receive_inventory(
                actor=request.user,
                item_revision=ItemRevision.objects.get(pk=request.data.get('item_revision')),
                quantity=request.data.get('quantity'),
                unit=request.data.get('unit', 'EA'),
                warehouse=Warehouse.objects.get(pk=request.data.get('warehouse')),
                location=StorageLocation.objects.get(pk=request.data.get('location')),
                lot=ProductionLot.objects.get(pk=request.data.get('lot')) if request.data.get('lot') else None,
                serial=ProductionSerial.objects.get(pk=request.data.get('serial')) if request.data.get('serial') else None,
                stock_status=request.data.get('stock_status', InventoryBalance.STATUS_AVAILABLE),
                ownership=request.data.get('ownership', ''),
                expected_version=request.data.get('expected_version'),
                idempotency_key=request.data.get('idempotency_key', ''),
                reason_code=request.data.get('reason_code', 'MANUAL_RECEIPT'),
                notes=request.data.get('notes', ''),
            )
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response({'transaction': InventoryTransactionSerializer(tx, context={'request': request}).data, 'balance': InventoryBalanceSerializer(balance, context={'request': request}).data}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def transfer(self, request, pk=None):
        try:
            tx = transfer_inventory(
                actor=request.user,
                source_balance=self.get_object(),
                quantity=request.data.get('quantity'),
                destination_warehouse=Warehouse.objects.get(pk=request.data.get('destination_warehouse')),
                destination_location=StorageLocation.objects.get(pk=request.data.get('destination_location')),
                destination_status=request.data.get('destination_status'),
                expected_version=request.data.get('expected_version'),
                idempotency_key=request.data.get('idempotency_key', ''),
                reason_code=request.data.get('reason_code', 'TRANSFER'),
                notes=request.data.get('notes', ''),
            )
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response(InventoryTransactionSerializer(tx, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def adjust(self, request, pk=None):
        try:
            tx = adjust_inventory(actor=request.user, balance=self.get_object(), quantity=request.data.get('quantity'), adjustment_type=request.data.get('adjustment_type'), expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''), reason_code=request.data.get('reason_code', ''), notes=request.data.get('notes', ''))
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response(InventoryTransactionSerializer(tx, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def quarantine(self, request, pk=None):
        try:
            tx = quarantine_stock(actor=request.user, balance=self.get_object(), quantity=request.data.get('quantity'), quality_hold=QualityHold.objects.get(pk=request.data.get('quality_hold')) if request.data.get('quality_hold') else None, nonconformance=NonconformanceRecord.objects.get(pk=request.data.get('nonconformance')) if request.data.get('nonconformance') else None, idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response(InventoryTransactionSerializer(tx, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='quality-release')
    def quality_release(self, request, pk=None):
        try:
            tx = quality_release_stock(actor=request.user, balance=self.get_object(), quantity=request.data.get('quantity'), quality_hold=QualityHold.objects.get(pk=request.data.get('quality_hold')) if request.data.get('quality_hold') else None, disposition=QualityDisposition.objects.get(pk=request.data.get('disposition')) if request.data.get('disposition') else None, idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response(InventoryTransactionSerializer(tx, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def scrap(self, request, pk=None):
        try:
            tx = scrap_stock(actor=request.user, balance=self.get_object(), quantity=request.data.get('quantity'), nonconformance=NonconformanceRecord.objects.get(pk=request.data.get('nonconformance')) if request.data.get('nonconformance') else None, disposition=QualityDisposition.objects.get(pk=request.data.get('disposition')) if request.data.get('disposition') else None, idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response(InventoryTransactionSerializer(tx, context={'request': request}).data)


class InventoryTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = InventoryTransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = InventoryTransaction.objects.select_related(
            'item_revision__item', 'source_warehouse', 'source_location',
            'destination_warehouse', 'destination_location', 'source_lot',
            'source_serial', 'destination_lot', 'destination_serial', 'actor',
        )
        production_order = self.request.query_params.get('production_order')
        if production_order:
            queryset = queryset.filter(production_order_id=production_order)
        requirement = self.request.query_params.get('material_requirement')
        if requirement:
            queryset = queryset.filter(material_requirement_id=requirement)
        transaction_type = self.request.query_params.get('transaction_type')
        if transaction_type:
            queryset = queryset.filter(transaction_type=transaction_type)
        return queryset


class InventoryReservationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = InventoryReservationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = InventoryReservation.objects.select_related('production_order', 'material_requirement__operation', 'item_revision__item', 'balance', 'warehouse', 'location', 'lot', 'serial', 'reserved_by', 'last_transaction')
        production_order = self.request.query_params.get('production_order')
        if production_order:
            queryset = queryset.filter(production_order_id=production_order)
        requirement = self.request.query_params.get('material_requirement')
        if requirement:
            queryset = queryset.filter(material_requirement_id=requirement)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        try:
            reservation, tx = release_reservation(actor=request.user, reservation=self.get_object(), quantity=request.data.get('quantity'), expected_reservation_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response({'reservation': self.get_serializer(reservation).data, 'transaction': InventoryTransactionSerializer(tx, context={'request': request}).data})

    @action(detail=True, methods=['post'])
    def issue(self, request, pk=None):
        reservation = self.get_object()
        try:
            tx = issue_material(actor=request.user, operation_execution=OperationExecution.objects.get(pk=request.data.get('operation_execution')), requirement=reservation.material_requirement, reservation=reservation, quantity=request.data.get('quantity'), expected_balance_version=request.data.get('expected_balance_version'), expected_reservation_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response(InventoryTransactionSerializer(tx, context={'request': request}).data)


class MaterialControlViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        return Response(material_queue(order_id=request.query_params.get('production_order')))

    @action(detail=False, methods=['post'])
    def reserve(self, request):
        try:
            reservation, tx = reserve_material(actor=request.user, requirement=ProductionMaterialRequirement.objects.get(pk=request.data.get('material_requirement')), balance=InventoryBalance.objects.get(pk=request.data.get('balance')), quantity=request.data.get('quantity'), expected_balance_version=request.data.get('expected_balance_version'), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response({'reservation': InventoryReservationSerializer(reservation, context={'request': request}).data, 'transaction': InventoryTransactionSerializer(tx, context={'request': request}).data}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def issue(self, request):
        try:
            tx = issue_material(actor=request.user, operation_execution=OperationExecution.objects.get(pk=request.data.get('operation_execution')), requirement=ProductionMaterialRequirement.objects.get(pk=request.data.get('material_requirement')), balance=InventoryBalance.objects.get(pk=request.data.get('balance')) if request.data.get('balance') else None, reservation=InventoryReservation.objects.get(pk=request.data.get('reservation')) if request.data.get('reservation') else None, quantity=request.data.get('quantity'), expected_balance_version=request.data.get('expected_balance_version'), expected_reservation_version=request.data.get('expected_reservation_version'), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response(InventoryTransactionSerializer(tx, context={'request': request}).data)

    @action(detail=False, methods=['post'])
    def return_material(self, request):
        try:
            tx = return_material_to_inventory(actor=request.user, issue_transaction=InventoryTransaction.objects.get(pk=request.data.get('issue_transaction')), quantity=request.data.get('quantity'), destination_warehouse=Warehouse.objects.get(pk=request.data.get('destination_warehouse')), destination_location=StorageLocation.objects.get(pk=request.data.get('destination_location')), stock_status=request.data.get('stock_status', InventoryBalance.STATUS_AVAILABLE), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
        except Exception as exc:
            return _inventory_error_response(exc)
        return Response(InventoryTransactionSerializer(tx, context={'request': request}).data)


def _mrp_error_response(exc):
    if isinstance(exc, MRPConflict):
        return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
    if isinstance(exc, EngineeringPermissionError):
        raise PermissionDenied(str(exc)) from exc
    if isinstance(exc, EngineeringLifecycleError):
        raise DRFValidationError(_validation_payload(exc)) from exc
    raise exc


def _procurement_error_response(exc):
    if isinstance(exc, ProcurementConflict):
        return Response(exc.payload, status=status.HTTP_409_CONFLICT)
    if isinstance(exc, EngineeringPermissionError):
        raise PermissionDenied(str(exc)) from exc
    if isinstance(exc, EngineeringLifecycleError):
        raise DRFValidationError(_validation_payload(exc)) from exc
    if isinstance(exc, DjangoValidationError):
        raise DRFValidationError(_validation_payload(exc)) from exc
    raise exc


class PlanningCalendarViewSet(viewsets.ModelViewSet):
    serializer_class = PlanningCalendarSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = PlanningCalendar.objects.all()
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(code__icontains=search) | queryset.filter(name__icontains=search)
        return queryset.distinct()


class ItemPlanningPolicyViewSet(viewsets.ModelViewSet):
    serializer_class = ItemPlanningPolicySerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = ItemPlanningPolicy.objects.select_related('item_revision__item', 'default_warehouse')
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        item_revision = self.request.query_params.get('item_revision')
        if item_revision:
            queryset = queryset.filter(item_revision_id=item_revision)
        procurement_type = self.request.query_params.get('procurement_type')
        if procurement_type:
            queryset = queryset.filter(procurement_type=procurement_type)
        return queryset


class PlanningDemandViewSet(viewsets.ModelViewSet):
    serializer_class = PlanningDemandSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = PlanningDemand.objects.select_related('item_revision__item', 'warehouse', 'plant', 'created_by', 'approved_by')
        for param, field in [('item_revision', 'item_revision_id'), ('warehouse', 'warehouse_id'), ('plant', 'plant_id'), ('status', 'status')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        horizon_start = self.request.query_params.get('horizon_start')
        horizon_end = self.request.query_params.get('horizon_end')
        if horizon_start:
            queryset = queryset.filter(required_date__gte=horizon_start)
        if horizon_end:
            queryset = queryset.filter(required_date__lte=horizon_end)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            demand = create_demand(actor=request.user, **serializer.validated_data)
        except Exception as exc:
            return _mrp_error_response(exc)
        return Response(self.get_serializer(demand).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=kwargs.pop('partial', False))
        serializer.is_valid(raise_exception=True)
        try:
            demand = update_demand(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), **serializer.validated_data)
        except Exception as exc:
            return _mrp_error_response(exc)
        return Response(self.get_serializer(demand).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        try:
            demand = transition_demand(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), action='approve')
        except Exception as exc:
            return _mrp_error_response(exc)
        return Response(self.get_serializer(demand).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        try:
            demand = transition_demand(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), action='cancel')
        except Exception as exc:
            return _mrp_error_response(exc)
        return Response(self.get_serializer(demand).data)


class ScheduledSupplyViewSet(viewsets.ModelViewSet):
    serializer_class = ScheduledSupplySerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = ScheduledSupply.objects.select_related('item_revision__item', 'warehouse', 'created_by')
        for param, field in [('item_revision', 'item_revision_id'), ('warehouse', 'warehouse_id'), ('status', 'status'), ('supply_type', 'supply_type')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def perform_create(self, serializer):
        from django.utils import timezone

        prefix = f'SS-{timezone.now():%Y}-'
        number = serializer.validated_data.get('supply_number') or f'{prefix}{ScheduledSupply.objects.filter(supply_number__startswith=prefix).count() + 1:06d}'
        serializer.save(created_by=self.request.user, supply_number=number)


class MRPRunViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        return MRPRun.objects.select_related('warehouse', 'plant', 'planning_calendar', 'started_by').prefetch_related('demand_snapshots', 'supply_snapshots', 'requirements', 'recommendations')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return MRPRunDetailSerializer
        return MRPRunSerializer

    def create(self, request, *args, **kwargs):
        horizon_start = parse_date(request.data.get('horizon_start') or '')
        horizon_end = parse_date(request.data.get('horizon_end') or '')
        if not horizon_start or not horizon_end:
            raise DRFValidationError({'horizon': 'horizon_start and horizon_end must use YYYY-MM-DD.'})
        try:
            run = run_mrp(
                actor=request.user,
                horizon_start=horizon_start,
                horizon_end=horizon_end,
                warehouse=Warehouse.objects.get(pk=request.data.get('warehouse')) if request.data.get('warehouse') else None,
                plant=Plant.objects.get(pk=request.data.get('plant')) if request.data.get('plant') else None,
                planning_calendar=PlanningCalendar.objects.get(pk=request.data.get('planning_calendar')) if request.data.get('planning_calendar') else None,
            )
        except Exception as exc:
            return _mrp_error_response(exc)
        return Response(MRPRunDetailSerializer(run, context={'request': request}).data, status=status.HTTP_201_CREATED)


class MRPRecommendationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MRPRecommendationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = MRPRecommendation.objects.select_related('mrp_run', 'item_revision__item', 'warehouse', 'source_requirement')
        for param, field in [('mrp_run', 'mrp_run_id'), ('item_revision', 'item_revision_id'), ('status', 'status'), ('recommendation_type', 'recommendation_type')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def _transition(self, request, action_name):
        try:
            rec = transition_recommendation(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), action=action_name)
        except Exception as exc:
            return _mrp_error_response(exc)
        return Response(self.get_serializer(rec).data)

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        return self._transition(request, 'review')

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._transition(request, 'approve')

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return self._transition(request, 'reject')

    @action(detail=True, methods=['post'])
    def convert(self, request, pk=None):
        try:
            rec = convert_recommendation(
                self.get_object(),
                actor=request.user,
                expected_version=request.data.get('expected_version'),
                idempotency_key=request.data.get('idempotency_key', ''),
                source_opc_diagram=OPCDiagram.objects.get(pk=request.data.get('source_opc_diagram')) if request.data.get('source_opc_diagram') else None,
                source_manufacturing_bom_revision=BOMRevision.objects.get(pk=request.data.get('source_manufacturing_bom_revision')) if request.data.get('source_manufacturing_bom_revision') else None,
            )
        except Exception as exc:
            return _mrp_error_response(exc)
        return Response(self.get_serializer(rec).data)


class MRPPeggingViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MRPPeggingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = MRPPegging.objects.select_related('mrp_run', 'recommendation', 'requirement', 'demand_snapshot')
        for param, field in [('mrp_run', 'mrp_run_id'), ('recommendation', 'recommendation_id'), ('requirement', 'requirement_id')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset


class PurchaseRequisitionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PurchaseRequisitionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = PurchaseRequisition.objects.select_related('item_revision__item', 'warehouse', 'source_recommendation', 'created_by')
        for param, field in [('item_revision', 'item_revision_id'), ('warehouse', 'warehouse_id'), ('status', 'status')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset


class SupplierViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = Supplier.objects.prefetch_related('sites', 'contacts', 'capabilities', 'approved_items')
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(supplier_code__icontains=search) | queryset.filter(name__icontains=search)
        return queryset.distinct()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def _transition(self, request, target):
        try:
            supplier = transition_supplier(self.get_object(), actor=request.user, target_status=target, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''))
        except Exception as exc:
            return _procurement_error_response(exc)
        return Response(self.get_serializer(supplier).data)

    @action(detail=True, methods=['post'], url_path='qualify')
    def qualify(self, request, pk=None):
        return self._transition(request, Supplier.STATUS_QUALIFIED)

    @action(detail=True, methods=['post'], url_path='suspend')
    def suspend(self, request, pk=None):
        return self._transition(request, Supplier.STATUS_SUSPENDED)

    @action(detail=True, methods=['post'], url_path='disqualify')
    def disqualify(self, request, pk=None):
        return self._transition(request, Supplier.STATUS_DISQUALIFIED)

    @action(detail=True, methods=['post'], url_path='reactivate')
    def reactivate(self, request, pk=None):
        return self._transition(request, Supplier.STATUS_DRAFT)


class SupplierSiteViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierSiteSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = SupplierSite.objects.select_related('supplier')
        supplier = self.request.query_params.get('supplier')
        if supplier:
            queryset = queryset.filter(supplier_id=supplier)
        return queryset


class SupplierContactViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierContactSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = SupplierContact.objects.select_related('supplier', 'site')
        supplier = self.request.query_params.get('supplier')
        if supplier:
            queryset = queryset.filter(supplier_id=supplier)
        return queryset


class SupplierCapabilityViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierCapabilitySerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = SupplierCapability.objects.select_related('supplier', 'site', 'process_definition', 'item_revision__item')
        supplier = self.request.query_params.get('supplier')
        if supplier:
            queryset = queryset.filter(supplier_id=supplier)
        return queryset


class ApprovedSupplierItemViewSet(viewsets.ModelViewSet):
    serializer_class = ApprovedSupplierItemSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = ApprovedSupplierItem.objects.select_related('supplier', 'supplier_site', 'item_revision__item')
        for param, field in [('supplier', 'supplier_id'), ('item_revision', 'item_revision_id'), ('status', 'status')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        try:
            row = approve_supplier_item(self.get_object(), actor=request.user)
        except Exception as exc:
            return _procurement_error_response(exc)
        return Response(self.get_serializer(row).data)


class RFQLineViewSet(viewsets.ModelViewSet):
    serializer_class = RFQLineSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = RFQLine.objects.select_related('rfq', 'item_revision__item', 'delivery_warehouse', 'delivery_location', 'source_requisition')
        rfq = self.request.query_params.get('rfq')
        if rfq:
            queryset = queryset.filter(rfq_id=rfq)
        return queryset


class RFQSupplierInvitationViewSet(viewsets.ModelViewSet):
    serializer_class = RFQSupplierInvitationSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = RFQSupplierInvitation.objects.select_related('rfq', 'supplier', 'supplier_site', 'contact')
        rfq = self.request.query_params.get('rfq')
        if rfq:
            queryset = queryset.filter(rfq_id=rfq)
        return queryset


class RequestForQuotationViewSet(viewsets.ModelViewSet):
    serializer_class = RequestForQuotationSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = RequestForQuotation.objects.select_related('requisition', 'project_requirement', 'project', 'wbs_activity').prefetch_related('lines', 'invitations')
        for param, field in [('status', 'status'), ('project', 'project_id'), ('requisition', 'requisition_id')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def _transition(self, request, target):
        try:
            rfq = transition_rfq(self.get_object(), actor=request.user, target_status=target, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''))
        except Exception as exc:
            return _procurement_error_response(exc)
        return Response(self.get_serializer(rfq).data)

    @action(detail=True, methods=['post'], url_path='release')
    def release(self, request, pk=None):
        return self._transition(request, RequestForQuotation.STATUS_RELEASED)

    @action(detail=True, methods=['post'], url_path='close')
    def close(self, request, pk=None):
        return self._transition(request, RequestForQuotation.STATUS_CLOSED)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        return self._transition(request, RequestForQuotation.STATUS_CANCELLED)

    @action(detail=True, methods=['post'], url_path='compare')
    def compare(self, request, pk=None):
        try:
            snapshot = create_quotation_comparison(self.get_object(), actor=request.user, policy_version=request.data.get('policy_version', 'quotation-comparison-v1'))
        except Exception as exc:
            return _procurement_error_response(exc)
        return Response(QuotationComparisonSnapshotSerializer(snapshot, context={'request': request}).data, status=status.HTTP_201_CREATED)


class SupplierQuotationLineViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierQuotationLineSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = SupplierQuotationLine.objects.select_related('quotation', 'rfq_line')
        quotation = self.request.query_params.get('quotation')
        if quotation:
            queryset = queryset.filter(quotation_id=quotation)
        return queryset


class SupplierQuotationViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierQuotationSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = SupplierQuotation.objects.select_related('rfq', 'supplier', 'supplier_site').prefetch_related('lines')
        for param, field in [('rfq', 'rfq_id'), ('supplier', 'supplier_id'), ('status', 'status')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        try:
            quotation = submit_quotation(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''))
        except Exception as exc:
            return _procurement_error_response(exc)
        return Response(self.get_serializer(quotation).data)


class QuotationComparisonSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = QuotationComparisonSnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = QuotationComparisonSnapshot.objects.select_related('rfq', 'recommended_supplier', 'recommended_quotation')
        rfq = self.request.query_params.get('rfq')
        if rfq:
            queryset = queryset.filter(rfq_id=rfq)
        return queryset


class SourcingDecisionLineViewSet(viewsets.ModelViewSet):
    serializer_class = SourcingDecisionLineSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = SourcingDecisionLine.objects.select_related('decision', 'quotation_line', 'rfq_line', 'item_revision__item')
        decision = self.request.query_params.get('decision')
        if decision:
            queryset = queryset.filter(decision_id=decision)
        return queryset


class SourcingDecisionViewSet(viewsets.ModelViewSet):
    serializer_class = SourcingDecisionSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = SourcingDecision.objects.select_related('rfq', 'comparison_snapshot', 'supplier', 'quotation', 'project', 'procurement_requirement', 'converted_purchase_order').prefetch_related('lines')
        for param, field in [('status', 'status'), ('supplier', 'supplier_id'), ('rfq', 'rfq_id'), ('project', 'project_id')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        try:
            decision = approve_sourcing_decision(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''))
        except Exception as exc:
            return _procurement_error_response(exc)
        return Response(self.get_serializer(decision).data)

    @action(detail=True, methods=['post'], url_path='convert-to-po')
    def convert_to_po(self, request, pk=None):
        try:
            po = convert_sourcing_to_purchase_order(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''))
        except Exception as exc:
            return _procurement_error_response(exc)
        return Response(PurchaseOrderSerializer(po, context={'request': request}).data, status=status.HTTP_201_CREATED)


class PurchaseOrderLineViewSet(viewsets.ModelViewSet):
    serializer_class = PurchaseOrderLineSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = PurchaseOrderLine.objects.select_related('purchase_order', 'item_revision__item', 'delivery_warehouse', 'delivery_location').prefetch_related('schedules')
        purchase_order = self.request.query_params.get('purchase_order')
        if purchase_order:
            queryset = queryset.filter(purchase_order_id=purchase_order)
        return queryset

    @action(detail=True, methods=['post'], url_path='receive')
    def receive(self, request, pk=None):
        try:
            receipt = receive_purchase_order_line(
                actor=request.user,
                purchase_order_line=self.get_object(),
                quantity=request.data.get('quantity'),
                warehouse=Warehouse.objects.get(pk=request.data.get('warehouse')) if request.data.get('warehouse') else None,
                location=StorageLocation.objects.get(pk=request.data.get('location')) if request.data.get('location') else None,
                accepted_quantity=request.data.get('accepted_quantity'),
                idempotency_key=request.data.get('idempotency_key', ''),
                packing_slip=request.data.get('packing_slip', ''),
                quarantine=request.data.get('quarantine', True),
            )
        except Exception as exc:
            return _procurement_error_response(exc)
        return Response(PurchaseReceiptSerializer(receipt, context={'request': request}).data, status=status.HTTP_201_CREATED)


class PurchaseOrderDeliveryScheduleViewSet(viewsets.ModelViewSet):
    serializer_class = PurchaseOrderDeliveryScheduleSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = PurchaseOrderDeliverySchedule.objects.select_related('purchase_order_line__purchase_order')
        line = self.request.query_params.get('purchase_order_line')
        if line:
            queryset = queryset.filter(purchase_order_line_id=line)
        return queryset


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    serializer_class = PurchaseOrderSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = PurchaseOrder.objects.select_related('supplier', 'supplier_site', 'sourcing_decision', 'quotation', 'project', 'wbs_activity').prefetch_related('lines__schedules')
        for param, field in [('status', 'status'), ('supplier', 'supplier_id'), ('project', 'project_id')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def _transition(self, request, target):
        try:
            po = transition_purchase_order(self.get_object(), actor=request.user, target_status=target, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''), supplier_reference=request.data.get('supplier_reference', ''))
        except Exception as exc:
            return _procurement_error_response(exc)
        return Response(self.get_serializer(po).data)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        return self._transition(request, PurchaseOrder.STATUS_APPROVED)

    @action(detail=True, methods=['post'], url_path='release')
    def release(self, request, pk=None):
        return self._transition(request, PurchaseOrder.STATUS_RELEASED)

    @action(detail=True, methods=['post'], url_path='acknowledge')
    def acknowledge(self, request, pk=None):
        return self._transition(request, PurchaseOrder.STATUS_ACKNOWLEDGED)

    @action(detail=True, methods=['post'], url_path='confirm')
    def confirm(self, request, pk=None):
        return self._transition(request, PurchaseOrder.STATUS_CONFIRMED)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        return self._transition(request, PurchaseOrder.STATUS_CANCELLED)


class PurchaseReceiptViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PurchaseReceiptSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = PurchaseReceipt.objects.select_related('purchase_order', 'supplier', 'received_by').prefetch_related('lines')
        po = self.request.query_params.get('purchase_order')
        if po:
            queryset = queryset.filter(purchase_order_id=po)
        return queryset


class PurchaseReceiptLineViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PurchaseReceiptLineSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = PurchaseReceiptLine.objects.select_related('receipt', 'purchase_order_line', 'item_revision__item', 'warehouse', 'location', 'inventory_transaction', 'inventory_balance')
        receipt = self.request.query_params.get('receipt')
        if receipt:
            queryset = queryset.filter(receipt_id=receipt)
        return queryset


class PurchaseReturnViewSet(viewsets.ModelViewSet):
    serializer_class = PurchaseReturnSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = PurchaseReturn.objects.select_related('supplier', 'purchase_order', 'receipt_line', 'nonconformance')
        supplier = self.request.query_params.get('supplier')
        if supplier:
            queryset = queryset.filter(supplier_id=supplier)
        return queryset

    def create(self, request, *args, **kwargs):
        if request.data.get('receipt_line'):
            try:
                ret = create_purchase_return(actor=request.user, receipt_line=PurchaseReceiptLine.objects.get(pk=request.data.get('receipt_line')), quantity=request.data.get('quantity'), reason=request.data.get('reason', ''), idempotency_key=request.data.get('idempotency_key', ''))
            except Exception as exc:
                return _procurement_error_response(exc)
            return Response(self.get_serializer(ret).data, status=status.HTTP_201_CREATED)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ProcurementEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProcurementEventSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = ProcurementEvent.objects.select_related('supplier', 'rfq', 'quotation', 'sourcing_decision', 'purchase_order', 'receipt', 'actor')
        for param, field in [('supplier', 'supplier_id'), ('purchase_order', 'purchase_order_id'), ('event_type', 'event_type')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset


class CostRateViewSet(viewsets.ModelViewSet):
    serializer_class = CostRateSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = CostRate.objects.select_related('labor_skill', 'work_center', 'machine')
        rate_type = self.request.query_params.get('rate_type')
        if rate_type:
            queryset = queryset.filter(rate_type=rate_type)
        return queryset


class CurrencyRatePolicyViewSet(viewsets.ModelViewSet):
    serializer_class = CurrencyRatePolicySerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    queryset = CurrencyRatePolicy.objects.all()


class CostSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CostSnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = CostSnapshot.objects.select_related('item_revision__item', 'production_order', 'maintenance_work_order', 'project', 'purchase_receipt', 'created_by')
        snapshot_type = self.request.query_params.get('snapshot_type')
        if snapshot_type:
            queryset = queryset.filter(snapshot_type=snapshot_type)
        return queryset

    @action(detail=False, methods=['post'], url_path='create-receipt')
    def create_receipt(self, request):
        snapshot = create_receipt_cost_snapshot(receipt=PurchaseReceipt.objects.get(pk=request.data.get('purchase_receipt')), actor=request.user, policy_version=request.data.get('policy_version', 'cost-rollup-v1'), currency=request.data.get('currency', 'USD'))
        return Response(self.get_serializer(snapshot).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='create-production')
    def create_production(self, request):
        snapshot = create_production_cost_snapshot(production_order=ProductionOrder.objects.get(pk=request.data.get('production_order')), actor=request.user, policy_version=request.data.get('policy_version', 'cost-rollup-v1'), currency=request.data.get('currency', 'USD'))
        return Response(self.get_serializer(snapshot).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='create-maintenance')
    def create_maintenance(self, request):
        snapshot = create_maintenance_cost_snapshot(work_order=MaintenanceWorkOrder.objects.get(pk=request.data.get('maintenance_work_order')), actor=request.user, policy_version=request.data.get('policy_version', 'cost-rollup-v1'), currency=request.data.get('currency', 'USD'))
        return Response(self.get_serializer(snapshot).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='create-project')
    def create_project(self, request):
        from ktcPlanning.models import Project
        snapshot = create_project_cost_snapshot(project=Project.objects.get(pk=request.data.get('project')), actor=request.user, policy_version=request.data.get('policy_version', 'cost-rollup-v1'), currency=request.data.get('currency', 'USD'))
        return Response(self.get_serializer(snapshot).data, status=status.HTTP_201_CREATED)


class SupplierPerformanceSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SupplierPerformanceSnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = SupplierPerformanceSnapshot.objects.select_related('supplier')
        supplier = self.request.query_params.get('supplier')
        if supplier:
            queryset = queryset.filter(supplier_id=supplier)
        return queryset

    @action(detail=False, methods=['post'], url_path='create')
    def create_snapshot(self, request):
        snapshot = create_supplier_performance_snapshot(supplier=Supplier.objects.get(pk=request.data.get('supplier')), period_start=parse_date(request.data.get('period_start')), period_end=parse_date(request.data.get('period_end')))
        return Response(self.get_serializer(snapshot).data, status=status.HTTP_201_CREATED)


class OperationalKPISnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OperationalKPISnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = OperationalKPISnapshot.objects.select_related('plant', 'work_center', 'machine', 'production_order', 'created_by')
        for param, field in [('plant', 'plant_id'), ('work_center', 'work_center_id'), ('machine', 'machine_id'), ('production_order', 'production_order_id')]:
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    @action(detail=False, methods=['post'], url_path='create')
    def create_snapshot(self, request):
        snapshot = create_operational_kpi_snapshot(
            period_start=parse_datetime(request.data.get('period_start')),
            period_end=parse_datetime(request.data.get('period_end')),
            actor=request.user,
            plant=Plant.objects.get(pk=request.data.get('plant')) if request.data.get('plant') else None,
            work_center=WorkCenter.objects.get(pk=request.data.get('work_center')) if request.data.get('work_center') else None,
            machine=MachineAsset.objects.get(pk=request.data.get('machine')) if request.data.get('machine') else None,
            production_order=ProductionOrder.objects.get(pk=request.data.get('production_order')) if request.data.get('production_order') else None,
        )
        return Response(self.get_serializer(snapshot).data, status=status.HTTP_201_CREATED)


def _scheduling_error_response(exc):
    if isinstance(exc, SchedulingConflict):
        return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
    if isinstance(exc, EngineeringPermissionError):
        raise PermissionDenied(str(exc)) from exc
    if isinstance(exc, EngineeringLifecycleError):
        raise DRFValidationError(_validation_payload(exc)) from exc
    raise exc


class ResourceCalendarViewSet(viewsets.ModelViewSet):
    serializer_class = ResourceCalendarSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = ResourceCalendar.objects.select_related('planning_calendar')
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        calendar_type = self.request.query_params.get('calendar_type')
        if calendar_type:
            queryset = queryset.filter(calendar_type=calendar_type)
        return queryset


class ShiftViewSet(viewsets.ModelViewSet):
    serializer_class = ShiftSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = Shift.objects.select_related('resource_calendar')
        calendar = self.request.query_params.get('resource_calendar')
        if calendar:
            queryset = queryset.filter(resource_calendar_id=calendar)
        return queryset


class CalendarExceptionViewSet(viewsets.ModelViewSet):
    serializer_class = CalendarExceptionSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = CalendarException.objects.select_related('resource_calendar')
        calendar = self.request.query_params.get('resource_calendar')
        if calendar:
            queryset = queryset.filter(resource_calendar_id=calendar)
        exception_type = self.request.query_params.get('exception_type')
        if exception_type:
            queryset = queryset.filter(exception_type=exception_type)
        return queryset


class WorkCenterCapacityViewSet(viewsets.ModelViewSet):
    serializer_class = WorkCenterCapacitySerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = WorkCenterCapacity.objects.select_related('work_center', 'resource_calendar')
        work_center = self.request.query_params.get('work_center')
        if work_center:
            queryset = queryset.filter(work_center_id=work_center)
        return queryset


class MachineCapacityViewSet(viewsets.ModelViewSet):
    serializer_class = MachineCapacitySerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = MachineCapacity.objects.select_related('machine', 'resource_calendar')
        machine = self.request.query_params.get('machine')
        if machine:
            queryset = queryset.filter(machine_id=machine)
        return queryset


class LaborSkillViewSet(viewsets.ModelViewSet):
    serializer_class = LaborSkillSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = LaborSkill.objects.all()
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        return queryset


class WorkCenterLaborCapacityViewSet(viewsets.ModelViewSet):
    serializer_class = WorkCenterLaborCapacitySerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = WorkCenterLaborCapacity.objects.select_related('work_center', 'labor_skill', 'resource_calendar')
        work_center = self.request.query_params.get('work_center')
        if work_center:
            queryset = queryset.filter(work_center_id=work_center)
        return queryset


class SchedulingPolicyViewSet(viewsets.ModelViewSet):
    serializer_class = SchedulingPolicySerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = SchedulingPolicy.objects.all()
        active = _active_param(self.request)
        if active is not None:
            queryset = queryset.filter(active=active)
        return queryset


class SchedulingRunViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        queryset = SchedulingRun.objects.select_related('scheduling_policy', 'plant', 'work_center', 'started_by').prefetch_related('assignments', 'operation_snapshots', 'calendar_snapshots', 'capacity_buckets', 'exceptions')
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return SchedulingRunDetailSerializer
        return SchedulingRunSerializer

    def create(self, request, *args, **kwargs):
        horizon_start = parse_datetime(request.data.get('horizon_start') or '')
        horizon_end = parse_datetime(request.data.get('horizon_end') or '')
        if not horizon_start or not horizon_end:
            raise DRFValidationError({'horizon': 'horizon_start and horizon_end must use ISO datetime.'})
        if horizon_start.tzinfo is None:
            horizon_start = timezone.make_aware(horizon_start)
        if horizon_end.tzinfo is None:
            horizon_end = timezone.make_aware(horizon_end)
        try:
            run = run_finite_schedule(
                actor=request.user,
                scheduling_policy=SchedulingPolicy.objects.get(pk=request.data.get('scheduling_policy')),
                horizon_start=horizon_start,
                horizon_end=horizon_end,
                scenario_name=request.data.get('scenario_name') or 'Scenario',
                plant=Plant.objects.get(pk=request.data.get('plant')) if request.data.get('plant') else None,
                work_center=WorkCenter.objects.get(pk=request.data.get('work_center')) if request.data.get('work_center') else None,
                direction=request.data.get('direction') or None,
                allow_overload=request.data.get('allow_overload'),
            )
        except Exception as exc:
            return _scheduling_error_response(exc)
        return Response(SchedulingRunDetailSerializer(run, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        try:
            run = transition_scheduling_run(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), action='approve', override_reason=request.data.get('override_reason', ''))
        except Exception as exc:
            return _scheduling_error_response(exc)
        return Response(SchedulingRunDetailSerializer(run, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        try:
            run = transition_scheduling_run(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), action='reject')
        except Exception as exc:
            return _scheduling_error_response(exc)
        return Response(SchedulingRunDetailSerializer(run, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def apply(self, request, pk=None):
        try:
            run = apply_scheduling_run(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''))
        except Exception as exc:
            return _scheduling_error_response(exc)
        return Response(SchedulingRunDetailSerializer(run, context={'request': request}).data)


class ScheduledOperationAssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ScheduledOperationAssignmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = ScheduledOperationAssignment.objects.select_related('scheduling_run', 'operation_snapshot', 'production_order', 'manufacturing_operation', 'work_center', 'machine')
        run = self.request.query_params.get('scheduling_run')
        if run:
            queryset = queryset.filter(scheduling_run_id=run)
        machine = self.request.query_params.get('machine')
        if machine:
            queryset = queryset.filter(machine_id=machine)
        work_center = self.request.query_params.get('work_center')
        if work_center:
            queryset = queryset.filter(work_center_id=work_center)
        return queryset


class SchedulingOperationSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SchedulingOperationSnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = SchedulingOperationSnapshot.objects.select_related('scheduling_run', 'production_order', 'manufacturing_operation', 'work_center', 'selected_machine')
        run = self.request.query_params.get('scheduling_run')
        if run:
            queryset = queryset.filter(scheduling_run_id=run)
        return queryset


class SchedulingCalendarSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SchedulingCalendarSnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = SchedulingCalendarSnapshot.objects.select_related('scheduling_run', 'source_calendar', 'source_shift', 'source_exception')
        run = self.request.query_params.get('scheduling_run')
        if run:
            queryset = queryset.filter(scheduling_run_id=run)
        return queryset


class CapacityBucketViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CapacityBucketSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = CapacityBucket.objects.select_related('scheduling_run')
        run = self.request.query_params.get('scheduling_run')
        if run:
            queryset = queryset.filter(scheduling_run_id=run)
        if self.request.query_params.get('bottleneck_only'):
            queryset = queryset.filter(bottleneck=True)
        if self.request.query_params.get('overload_only'):
            queryset = queryset.filter(overload_minutes__gt=0)
        return queryset


class SchedulingExceptionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SchedulingExceptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = SchedulingException.objects.select_related('scheduling_run', 'production_order', 'manufacturing_operation')
        run = self.request.query_params.get('scheduling_run')
        if run:
            queryset = queryset.filter(scheduling_run_id=run)
        severity = self.request.query_params.get('severity')
        if severity:
            queryset = queryset.filter(severity=severity)
        if self.request.query_params.get('blocking'):
            queryset = queryset.filter(blocking=True)
        return queryset


def _maintenance_error_response(exc):
    if isinstance(exc, EngineeringPermissionError):
        raise PermissionDenied(str(exc)) from exc
    if isinstance(exc, MaintenanceConflict):
        return Response(_validation_payload(exc), status=status.HTTP_409_CONFLICT)
    if isinstance(exc, EngineeringLifecycleError):
        return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)
    raise exc


class MaintainableAssetViewSet(viewsets.ModelViewSet):
    serializer_class = MaintainableAssetSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        return MaintainableAsset.objects.select_related('plant', 'work_center', 'parent')


class MachineMaintenanceProfileViewSet(viewsets.ModelViewSet):
    serializer_class = MachineMaintenanceProfileSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        return MachineMaintenanceProfile.objects.select_related('machine', 'parent_asset')

    @action(detail=True, methods=['get'])
    def metrics(self, request, pk=None):
        profile = self.get_object()
        return Response(reliability_metrics(profile.machine, start=parse_datetime(request.query_params.get('start')) if request.query_params.get('start') else None, end=parse_datetime(request.query_params.get('end')) if request.query_params.get('end') else None))


class FailureCodeViewSet(viewsets.ModelViewSet):
    serializer_class = FailureCodeSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    queryset = FailureCode.objects.all()


class CauseCodeViewSet(viewsets.ModelViewSet):
    serializer_class = CauseCodeSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    queryset = CauseCode.objects.all()


class RemedyCodeViewSet(viewsets.ModelViewSet):
    serializer_class = RemedyCodeSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    queryset = RemedyCode.objects.all()


class AssetMeterViewSet(viewsets.ModelViewSet):
    serializer_class = AssetMeterSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        return AssetMeter.objects.select_related('machine', 'maintainable_asset').prefetch_related('readings')

    @action(detail=True, methods=['post'])
    def reading(self, request, pk=None):
        try:
            reading = record_meter_reading(actor=request.user, meter=self.get_object(), reading_value=request.data.get('reading_value'), reading_timestamp=parse_datetime(request.data.get('reading_timestamp')) if request.data.get('reading_timestamp') else None, idempotency_key=request.data.get('idempotency_key', ''), source=request.data.get('source', 'MANUAL'), notes=request.data.get('notes', ''))
            return Response(AssetMeterReadingSerializer(reading, context={'request': request}).data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            return _maintenance_error_response(exc)


class AssetMeterReadingViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AssetMeterReadingSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = AssetMeterReading.objects.select_related('meter', 'recorded_by')
        meter = self.request.query_params.get('meter')
        return queryset.filter(meter_id=meter) if meter else queryset


class MaintenanceTaskTemplateViewSet(viewsets.ModelViewSet):
    serializer_class = MaintenanceTaskTemplateSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        return MaintenanceTaskTemplate.objects.select_related('required_skill').prefetch_related('checklist_items')


class MaintenanceTaskChecklistItemViewSet(viewsets.ModelViewSet):
    serializer_class = MaintenanceTaskChecklistItemSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    queryset = MaintenanceTaskChecklistItem.objects.select_related('template')


class PreventiveMaintenancePlanViewSet(viewsets.ModelViewSet):
    serializer_class = PreventiveMaintenancePlanSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        return PreventiveMaintenancePlan.objects.select_related('machine', 'maintainable_asset', 'task_template', 'meter', 'created_by')

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['post'])
    def generate(self, request):
        try:
            created = generate_preventive_work_orders(actor=request.user, horizon_date=parse_date(request.data.get('horizon_date')) if request.data.get('horizon_date') else None)
            return Response(MaintenanceWorkOrderSerializer(created, many=True, context={'request': request}).data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            return _maintenance_error_response(exc)


class MaintenanceRequestViewSet(viewsets.ModelViewSet):
    serializer_class = MaintenanceRequestSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        return MaintenanceRequest.objects.select_related('machine', 'maintainable_asset', 'failure_code', 'requested_by', 'converted_work_order', 'production_order', 'operation_execution')

    def create(self, request, *args, **kwargs):
        try:
            req = create_maintenance_request(actor=request.user, title=request.data.get('title'), machine=MachineAsset.objects.get(pk=request.data.get('machine')) if request.data.get('machine') else None, maintainable_asset=MaintainableAsset.objects.get(pk=request.data.get('maintainable_asset')) if request.data.get('maintainable_asset') else None, description=request.data.get('description', ''), priority=request.data.get('priority', MaintenanceRequest.PRIORITY_NORMAL), failure_code=FailureCode.objects.get(pk=request.data.get('failure_code')) if request.data.get('failure_code') else None, production_order=ProductionOrder.objects.get(pk=request.data.get('production_order')) if request.data.get('production_order') else None, operation_execution=OperationExecution.objects.get(pk=request.data.get('operation_execution')) if request.data.get('operation_execution') else None)
            return Response(self.get_serializer(req).data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            return _maintenance_error_response(exc)

    @action(detail=True, methods=['post'])
    def convert(self, request, pk=None):
        try:
            req = self.get_object()
            wo = create_work_order(actor=request.user, title=request.data.get('title') or req.title, request=req, task_template=MaintenanceTaskTemplate.objects.get(pk=request.data.get('task_template')) if request.data.get('task_template') else None, work_order_type=request.data.get('work_order_type', MaintenanceWorkOrder.TYPE_CORRECTIVE), planned_start=parse_datetime(request.data.get('planned_start')) if request.data.get('planned_start') else None, planned_end=parse_datetime(request.data.get('planned_end')) if request.data.get('planned_end') else None, idempotency_key=request.data.get('idempotency_key', ''))
            return Response(MaintenanceWorkOrderSerializer(wo, context={'request': request}).data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            return _maintenance_error_response(exc)


class MaintenanceWorkOrderViewSet(viewsets.ModelViewSet):
    serializer_class = MaintenanceWorkOrderSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        return MaintenanceWorkOrder.objects.select_related('machine', 'maintainable_asset', 'request', 'preventive_plan', 'task_template', 'failure_code', 'cause_code', 'remedy_code', 'created_by', 'assigned_to', 'completed_by').prefetch_related('checklist_results', 'spare_part_requirements', 'spare_part_issues', 'events', 'downtime_events')

    def create(self, request, *args, **kwargs):
        try:
            wo = create_work_order(actor=request.user, title=request.data.get('title'), machine=MachineAsset.objects.get(pk=request.data.get('machine')) if request.data.get('machine') else None, maintainable_asset=MaintainableAsset.objects.get(pk=request.data.get('maintainable_asset')) if request.data.get('maintainable_asset') else None, task_template=MaintenanceTaskTemplate.objects.get(pk=request.data.get('task_template')) if request.data.get('task_template') else None, work_order_type=request.data.get('work_order_type', MaintenanceWorkOrder.TYPE_CORRECTIVE), priority=request.data.get('priority', MaintenanceRequest.PRIORITY_NORMAL), planned_start=parse_datetime(request.data.get('planned_start')) if request.data.get('planned_start') else None, planned_end=parse_datetime(request.data.get('planned_end')) if request.data.get('planned_end') else None, estimated_duration_hours=request.data.get('estimated_duration_hours'), downtime_required=request.data.get('downtime_required', True), description=request.data.get('description', ''), idempotency_key=request.data.get('idempotency_key', ''))
            return Response(self.get_serializer(wo).data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            return _maintenance_error_response(exc)

    def _transition(self, request, action_name):
        try:
            wo = transition_work_order(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), action=action_name, idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
            return Response(self.get_serializer(wo).data)
        except Exception as exc:
            return _maintenance_error_response(exc)

    @action(detail=True, methods=['post'])
    def plan(self, request, pk=None):
        return self._transition(request, 'plan')

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        return self._transition(request, 'release')

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        return self._transition(request, 'start')

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        return self._transition(request, 'pause')

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        return self._transition(request, 'resume')

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        return self._transition(request, 'complete')

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        return self._transition(request, 'close')

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        return self._transition(request, 'cancel')

    @action(detail=True, methods=['post'])
    def add_spare_part(self, request, pk=None):
        serializer = MaintenanceSparePartRequirementSerializer(data={**request.data, 'work_order': str(self.get_object().pk)})
        serializer.is_valid(raise_exception=True)
        req = serializer.save()
        return Response(MaintenanceSparePartRequirementSerializer(req, context={'request': request}).data, status=status.HTTP_201_CREATED)


class MaintenanceChecklistResultViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MaintenanceChecklistResultSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    queryset = MaintenanceChecklistResult.objects.select_related('work_order', 'template_item', 'recorded_by')

    @action(detail=True, methods=['post'])
    def record(self, request, pk=None):
        try:
            result = record_checklist_result(self.get_object(), actor=request.user, status=request.data.get('status'), expected_work_order_version=request.data.get('expected_work_order_version'), notes=request.data.get('notes', ''))
            return Response(self.get_serializer(result).data)
        except Exception as exc:
            return _maintenance_error_response(exc)


class MaintenanceSparePartRequirementViewSet(viewsets.ModelViewSet):
    serializer_class = MaintenanceSparePartRequirementSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    queryset = MaintenanceSparePartRequirement.objects.select_related('work_order', 'item_revision__item')

    @action(detail=True, methods=['post'])
    def issue(self, request, pk=None):
        try:
            issue = issue_spare_part(actor=request.user, requirement=self.get_object(), balance=InventoryBalance.objects.get(pk=request.data.get('balance')), quantity=request.data.get('quantity'), expected_balance_version=request.data.get('expected_balance_version'), expected_requirement_version=request.data.get('expected_requirement_version'), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
            return Response(MaintenanceSparePartIssueSerializer(issue, context={'request': request}).data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            return _maintenance_error_response(exc)


class MaintenanceSparePartIssueViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MaintenanceSparePartIssueSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    queryset = MaintenanceSparePartIssue.objects.select_related('work_order', 'requirement__item_revision__item', 'issue_transaction', 'return_transaction', 'issued_by')

    @action(detail=True, methods=['post'])
    def return_unused(self, request, pk=None):
        try:
            issue = return_spare_part(actor=request.user, issue=self.get_object(), quantity=request.data.get('quantity'), destination_warehouse=Warehouse.objects.get(pk=request.data.get('destination_warehouse')), destination_location=StorageLocation.objects.get(pk=request.data.get('destination_location')), stock_status=request.data.get('stock_status', InventoryBalance.STATUS_AVAILABLE), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
            return Response(self.get_serializer(issue).data)
        except Exception as exc:
            return _maintenance_error_response(exc)


class MaintenanceEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MaintenanceEventSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    queryset = MaintenanceEvent.objects.select_related('work_order', 'actor')


class MachineDowntimeEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MachineDowntimeEventSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]
    queryset = MachineDowntimeEvent.objects.select_related('machine', 'work_order', 'failure_code', 'cause_code', 'calendar_exception')


class MaintenanceDashboardViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def list(self, request):
        return Response(maintenance_dashboard())


class InspectionPlanViewSet(viewsets.ModelViewSet):
    queryset = InspectionPlan.objects.all()
    serializer_class = InspectionPlanSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]


class InspectionPlanRevisionViewSet(viewsets.ModelViewSet):
    serializer_class = InspectionPlanRevisionSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        return InspectionPlanRevision.objects.select_related('inspection_plan', 'instruction_document_revision', 'released_by', 'created_by').prefetch_related('characteristics')

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        try:
            revision = release_inspection_plan_revision(self.get_object(), actor=request.user)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(revision).data)


class InspectionCharacteristicViewSet(viewsets.ModelViewSet):
    serializer_class = InspectionCharacteristicSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = InspectionCharacteristic.objects.select_related('inspection_plan_revision__inspection_plan', 'instruction_document_revision')
        revision = self.request.query_params.get('inspection_plan_revision') or self.request.query_params.get('revision')
        if revision:
            queryset = queryset.filter(inspection_plan_revision_id=revision)
        return queryset


class InspectionApplicabilityViewSet(viewsets.ModelViewSet):
    serializer_class = InspectionApplicabilitySerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = InspectionApplicability.objects.select_related('inspection_plan_revision__inspection_plan', 'item_revision__item', 'process_definition', 'opc_node', 'tooling_definition')
        item_revision = self.request.query_params.get('item_revision')
        if item_revision:
            queryset = queryset.filter(item_revision_id=item_revision)
        applicability_type = self.request.query_params.get('applicability_type')
        if applicability_type:
            queryset = queryset.filter(applicability_type=applicability_type)
        return queryset


class InspectionExecutionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = InspectionExecutionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = InspectionExecution.objects.select_related(
            'requirement__inspection_plan_revision__inspection_plan',
            'requirement__operation',
            'production_order',
            'operation_execution',
            'assigned_inspector',
            'serial',
            'lot',
        ).prefetch_related(
            'samples',
            'measurements__characteristic',
            'measurements__sample',
            'quality_events',
            'quality_holds',
            'nonconformances__dispositions',
        )
        production_order = self.request.query_params.get('production_order') or self.request.query_params.get('order')
        if production_order:
            queryset = queryset.filter(production_order_id=production_order)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        result = self.request.query_params.get('result')
        if result:
            queryset = queryset.filter(result=result)
        operation = self.request.query_params.get('operation_execution')
        if operation:
            queryset = queryset.filter(operation_execution_id=operation)
        return queryset.order_by('production_order__order_number', 'requirement__sequence', 'created_at')

    def _respond(self, service, **kwargs):
        try:
            inspection, _result = service(self.get_object(), actor=self.request.user, **kwargs)
        except InspectionConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(inspection).data)

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        return self._respond(start_inspection, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'], url_path='record-measurement')
    def record_measurement(self, request, pk=None):
        sample = InspectionSample.objects.get(pk=request.data.get('sample') or request.data.get('sample_id'))
        characteristic = InspectionCharacteristic.objects.get(pk=request.data.get('characteristic') or request.data.get('characteristic_id'))
        correction_id = request.data.get('supersedes') or request.data.get('correction_for')
        return self._respond(
            record_measurement,
            expected_version=request.data.get('expected_version'),
            sample=sample,
            characteristic=characteristic,
            correction_for=InspectionMeasurement.objects.get(pk=correction_id) if correction_id else None,
            numeric_value=request.data.get('numeric_value'),
            boolean_value=request.data.get('boolean_value'),
            attribute_value=request.data.get('attribute_value', ''),
            text_value=request.data.get('text_value', ''),
            explicit_result=request.data.get('explicit_result', ''),
            idempotency_key=request.data.get('idempotency_key', ''),
            notes=request.data.get('notes', ''),
        )

    @action(detail=True, methods=['post'])
    def evaluate(self, request, pk=None):
        return self._respond(evaluate_inspection, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        return self._respond(complete_inspection, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'], url_path='place-hold')
    def place_hold(self, request, pk=None):
        inspection = self.get_object()
        try:
            place_quality_hold(actor=request.user, production_order=inspection.production_order, inspection=inspection, operation_execution=inspection.operation_execution, scope=request.data.get('scope', QualityHold.SCOPE_INSPECTION), reason=request.data.get('reason', ''), expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''))
        except InspectionConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        inspection.refresh_from_db()
        return Response(self.get_serializer(inspection).data)

    @action(detail=True, methods=['post'], url_path='create-ncr')
    def create_ncr(self, request, pk=None):
        inspection = self.get_object()
        measurement_id = request.data.get('measurement') or request.data.get('measurement_id')
        try:
            ncr = create_ncr(
                actor=request.user,
                production_order=inspection.production_order,
                operation_execution=inspection.operation_execution,
                inspection=inspection,
                measurement=InspectionMeasurement.objects.get(pk=measurement_id) if measurement_id else None,
                defect_code=request.data.get('defect_code', 'DEFECT'),
                defect_description=request.data.get('defect_description', request.data.get('notes', 'Nonconformance')),
                severity=request.data.get('severity', 'MINOR'),
                affected_quantity=request.data.get('affected_quantity', '1.000000'),
                idempotency_key=request.data.get('idempotency_key', ''),
            )
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(NonconformanceRecordSerializer(ncr, context={'request': request}).data, status=status.HTTP_201_CREATED)


class QualityHoldViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = QualityHoldSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = QualityHold.objects.select_related('production_order', 'operation_execution', 'inspection_execution', 'placed_by', 'released_by')
        production_order = self.request.query_params.get('production_order')
        if production_order:
            queryset = queryset.filter(production_order_id=production_order)
        active = self.request.query_params.get('active')
        if active is not None:
            queryset = queryset.filter(active=str(active).lower() not in {'0', 'false', 'no'})
        return queryset

    @action(detail=True, methods=['post'], url_path='release')
    def release(self, request, pk=None):
        try:
            hold = release_quality_hold(self.get_object(), actor=request.user, reason=request.data.get('reason', ''), idempotency_key=request.data.get('idempotency_key', ''))
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(hold).data)


class NonconformanceRecordViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NonconformanceRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = NonconformanceRecord.objects.select_related('production_order', 'operation_execution', 'inspection_execution', 'measurement', 'serial', 'lot', 'owner', 'created_by').prefetch_related('dispositions', 'inventory_transactions')
        production_order = self.request.query_params.get('production_order')
        if production_order:
            queryset = queryset.filter(production_order_id=production_order)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    def _respond(self, service, **kwargs):
        try:
            ncr = service(self.get_object(), actor=self.request.user, **kwargs)
        except NonconformanceConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(ncr).data)

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        return self._respond(transition_ncr, expected_version=request.data.get('expected_version'), action='review', idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        return self._respond(transition_ncr, expected_version=request.data.get('expected_version'), action='close', idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))

    @action(detail=True, methods=['post'], url_path='propose-disposition')
    def propose_disposition(self, request, pk=None):
        edge_id = request.data.get('target_rework_edge') or request.data.get('target_rework_edge_id')
        try:
            ncr, disposition = propose_disposition(
                self.get_object(),
                actor=request.user,
                expected_version=request.data.get('expected_version'),
                disposition_type=request.data.get('disposition_type'),
                quantity=request.data.get('quantity'),
                reason=request.data.get('reason', ''),
                instructions=request.data.get('instructions', ''),
                target_rework_edge=ManufacturingOperationPrecedence.objects.get(pk=edge_id) if edge_id else None,
                idempotency_key=request.data.get('idempotency_key', ''),
            )
        except NonconformanceConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(QualityDispositionSerializer(disposition, context={'request': request}).data, status=status.HTTP_201_CREATED)


class QualityDispositionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = QualityDispositionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = QualityDisposition.objects.select_related('ncr', 'target_rework_edge', 'proposed_by', 'approved_by', 'implemented_by')
        ncr = self.request.query_params.get('ncr')
        if ncr:
            queryset = queryset.filter(ncr_id=ncr)
        return queryset

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        try:
            _ncr, disposition = approve_disposition(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
        except NonconformanceConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(disposition).data)

    @action(detail=True, methods=['post'])
    def implement(self, request, pk=None):
        try:
            _ncr, disposition = implement_disposition(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''), notes=request.data.get('notes', ''))
        except NonconformanceConflict as exc:
            return Response(exc.message_dict, status=status.HTTP_409_CONFLICT)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(disposition).data)


class OPCDiagramViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = OPCDiagram.objects.select_related('item_revision__item', 'manufacturing_bom_revision__bom', 'submitted_by', 'approved_by', 'released_by', 'superseded_by').prefetch_related('nodes__material_allocations__bom_line__component_item_revision__item', 'nodes__document_requirements__document_revision__document', 'nodes__tooling_requirements__tooling_definition', 'edges', 'validation_evidence').annotate(
            node_count=Count('nodes', distinct=True),
            edge_count=Count('edges', distinct=True),
        )
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(part_code__icontains=search) | queryset.filter(title__icontains=search)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        item_revision_id = self.request.query_params.get('item_revision')
        if item_revision_id:
            queryset = queryset.filter(item_revision_id=item_revision_id)
        return queryset.distinct()

    def get_serializer_class(self):
        if self.action == 'list':
            return OPCDiagramSummarySerializer
        return OPCDiagramSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        fields = ['item_revision_id', 'title', 'part_code', 'part_name', 'revision', 'description', 'effective_from', 'effective_to']
        before = {field: getattr(serializer.instance, field) for field in fields}
        instance = serializer.save(updated_by=self.request.user)
        if _changed(instance, before, fields):
            refreshed = increment_graph_version(instance, actor=self.request.user)
            instance.graph_version = refreshed.graph_version
            instance.updated_at = refreshed.updated_at

    def retrieve(self, request, *args, **kwargs):
        diagram = self.get_object()
        serializer = OPCDiagramSerializer(diagram)
        return Response(serializer.data)


    def _handle_lifecycle(self, service, **kwargs):
        try:
            diagram = service(self.get_object(), actor=self.request.user, **kwargs)
        except OPCValidationConflict as exc:
            return Response(exc.payload, status=status.HTTP_409_CONFLICT)
        except OPCReleaseValidationError as exc:
            return Response({'validation': exc.validation_result}, status=status.HTTP_400_BAD_REQUEST)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(OPCDiagramSerializer(diagram).data)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        return self._handle_lifecycle(submit_opc_revision)

    @action(detail=True, methods=['post'], url_path='return-to-draft')
    def return_to_draft(self, request, pk=None):
        return self._handle_lifecycle(return_opc_revision_to_draft)

    @action(detail=True, methods=['post'], url_path='return-to-review')
    def return_to_review(self, request, pk=None):
        return self._handle_lifecycle(return_opc_revision_to_review)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._handle_lifecycle(approve_opc_revision)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        effective_from = request.data.get('effective_from')
        parsed_date = parse_date(effective_from) if effective_from else None
        if effective_from and not parsed_date:
            raise DRFValidationError({'effective_from': 'Use YYYY-MM-DD date format.'})
        return self._handle_lifecycle(
            release_opc_revision,
            effective_from=parsed_date,
            supersede_current=False,
            expected_graph_version=request.data.get('expected_graph_version'),
            acknowledged_issue_keys=request.data.get('acknowledged_issue_keys') or [],
            validation_policy_version=request.data.get('validation_policy_version'),
        )

    @action(detail=True, methods=['post'])
    def supersede(self, request, pk=None):
        effective_from = request.data.get('effective_from')
        parsed_date = parse_date(effective_from) if effective_from else None
        if effective_from and not parsed_date:
            raise DRFValidationError({'effective_from': 'Use YYYY-MM-DD date format.'})
        return self._handle_lifecycle(
            release_opc_revision,
            effective_from=parsed_date,
            supersede_current=True,
            expected_graph_version=request.data.get('expected_graph_version'),
            acknowledged_issue_keys=request.data.get('acknowledged_issue_keys') or [],
            validation_policy_version=request.data.get('validation_policy_version'),
        )

    @action(detail=True, methods=['post'])
    def obsolete(self, request, pk=None):
        return self._handle_lifecycle(obsolete_opc_revision)

    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        revision_code = (request.data.get('revision') or '').strip()
        if not revision_code:
            raise DRFValidationError({'revision': 'New OPC revision code is required.'})
        return self._handle_lifecycle(clone_opc_revision, revision_code=revision_code)

    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None):
        try:
            result = validate_opc_release_readiness(
                self.get_object(),
                mode=request.data.get('mode') or 'draft',
                expected_graph_version=request.data.get('expected_graph_version'),
                acknowledged_issue_keys=request.data.get('acknowledged_issue_keys') or [],
            )
        except OPCValidationConflict as exc:
            return Response(exc.payload, status=status.HTTP_409_CONFLICT)
        return Response(result)

    @action(detail=True, methods=['put'])
    def graph(self, request, pk=None):
        try:
            result = save_opc_graph(diagram_id=pk, payload=request.data, actor=request.user)
        except OPCGraphConflict as exc:
            return Response(exc.payload, status=status.HTTP_409_CONFLICT)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(
            {
                'diagram': OPCDiagramSerializer(result.diagram).data,
                'graph_version': result.diagram.graph_version,
                'node_id_map': result.node_id_map,
                'edge_id_map': result.edge_id_map,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='released')
    def released(self, request):
        item_revision_id = request.query_params.get('item_revision')
        if not item_revision_id:
            raise DRFValidationError({'item_revision': 'This query parameter is required.'})
        diagram = OPCDiagram.objects.filter(item_revision_id=item_revision_id, status=OPCDiagram.STATUS_RELEASED).order_by('-released_at', '-created_at').first()
        if not diagram:
            return Response(None, status=404)
        return Response(OPCDiagramSerializer(diagram).data)


class OPCNodeViewSet(viewsets.ModelViewSet):
    serializer_class = OPCNodeSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = OPCNode.objects.select_related('diagram')
        diagram_id = self.request.query_params.get('diagram')
        if diagram_id:
            queryset = queryset.filter(diagram_id=diagram_id)
        return queryset

    def perform_create(self, serializer):
        node = serializer.save()
        increment_graph_version(node.diagram, actor=self.request.user)

    def perform_update(self, serializer):
        node = serializer.save()
        increment_graph_version(node.diagram, actor=self.request.user)

    def perform_destroy(self, instance):
        try:
            ensure_node_editable(instance)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        diagram = instance.diagram
        instance.delete()
        increment_graph_version(diagram, actor=self.request.user)


class OPCEdgeViewSet(viewsets.ModelViewSet):
    serializer_class = OPCEdgeSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = OPCEdge.objects.select_related('diagram', 'source', 'target')
        diagram_id = self.request.query_params.get('diagram')
        if diagram_id:
            queryset = queryset.filter(diagram_id=diagram_id)
        return queryset

    def perform_create(self, serializer):
        edge = serializer.save()
        increment_graph_version(edge.diagram, actor=self.request.user)

    def perform_update(self, serializer):
        edge = serializer.save()
        increment_graph_version(edge.diagram, actor=self.request.user)

    def perform_destroy(self, instance):
        try:
            ensure_edge_editable(instance)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        diagram = instance.diagram
        instance.delete()
        increment_graph_version(diagram, actor=self.request.user)
