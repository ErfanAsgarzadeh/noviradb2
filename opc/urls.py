from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CalendarExceptionViewSet,
    CapacityBucketViewSet,
    ControlledDocumentRevisionViewSet,
    ControlledDocumentViewSet,
    EngineeringChangeObjectLinkViewSet,
    EngineeringChangeOrderViewSet,
    EngineeringChangeRequestViewSet,
    EngineeringImpactViewSet,
    InspectionApplicabilityViewSet,
    InspectionCharacteristicViewSet,
    InspectionExecutionViewSet,
    InspectionPlanRevisionViewSet,
    InspectionPlanViewSet,
    InventoryBalanceViewSet,
    InventoryReservationViewSet,
    InventoryTransactionViewSet,
    ItemPlanningPolicyViewSet,
    LaborSkillViewSet,
    MachineAssetViewSet,
    MachineCapacityViewSet,
    MachineDowntimeEventViewSet,
    MachineMaintenanceProfileViewSet,
    MaintainableAssetViewSet,
    MaintenanceChecklistResultViewSet,
    MaintenanceDashboardViewSet,
    MaintenanceEventViewSet,
    MaintenanceRequestViewSet,
    MaintenanceSparePartIssueViewSet,
    MaintenanceSparePartRequirementViewSet,
    MaintenanceTaskChecklistItemViewSet,
    MaintenanceTaskTemplateViewSet,
    MaintenanceWorkOrderViewSet,
    MaterialControlViewSet,
    MRPPeggingViewSet,
    MRPRecommendationViewSet,
    MRPRunViewSet,
    NonconformanceRecordViewSet,
    OPCDiagramViewSet,
    OPCEdgeViewSet,
    OPCMaterialCandidateViewSet,
    OPCNodeViewSet,
    OperationExecutionViewSet,
    PlantViewSet,
    PlanningCalendarViewSet,
    PlanningDemandViewSet,
    ProductionLotViewSet,
    ProductionOrderViewSet,
    ProductionSerialViewSet,
    ApprovedSupplierItemViewSet,
    CostRateViewSet,
    CostSnapshotViewSet,
    CurrencyRatePolicyViewSet,
    OperationalKPISnapshotViewSet,
    ProcurementEventViewSet,
    PurchaseOrderDeliveryScheduleViewSet,
    PurchaseOrderLineViewSet,
    PurchaseOrderViewSet,
    PurchaseReceiptLineViewSet,
    PurchaseReceiptViewSet,
    PurchaseReturnViewSet,
    PurchaseRequisitionViewSet,
    QuotationComparisonSnapshotViewSet,
    QualityDispositionViewSet,
    QualityHoldViewSet,
    ProcessDefinitionViewSet,
    RequestForQuotationViewSet,
    RFQLineViewSet,
    RFQSupplierInvitationViewSet,
    PreventiveMaintenancePlanViewSet,
    AssetMeterViewSet,
    AssetMeterReadingViewSet,
    FailureCodeViewSet,
    CauseCodeViewSet,
    RemedyCodeViewSet,
    ResourceCalendarViewSet,
    ScheduledOperationAssignmentViewSet,
    SchedulingCalendarSnapshotViewSet,
    SchedulingExceptionViewSet,
    SchedulingOperationSnapshotViewSet,
    SchedulingPolicyViewSet,
    SchedulingRunViewSet,
    ScheduledSupplyViewSet,
    ShiftViewSet,
    StorageLocationViewSet,
    SourcingDecisionLineViewSet,
    SourcingDecisionViewSet,
    SupplierCapabilityViewSet,
    SupplierContactViewSet,
    SupplierPerformanceSnapshotViewSet,
    SupplierQuotationLineViewSet,
    SupplierQuotationViewSet,
    SupplierSiteViewSet,
    SupplierViewSet,
    ToolingDefinitionViewSet,
    WarehouseViewSet,
    WorkCenterViewSet,
    WorkCenterCapacityViewSet,
    WorkCenterLaborCapacityViewSet,
)

router = DefaultRouter()
router.register('plants', PlantViewSet, basename='opc-plant')
router.register('work-centers', WorkCenterViewSet, basename='opc-work-center')
router.register('machine-assets', MachineAssetViewSet, basename='opc-machine-asset')
router.register('process-definitions', ProcessDefinitionViewSet, basename='opc-process-definition')
router.register('tooling-definitions', ToolingDefinitionViewSet, basename='opc-tooling-definition')
router.register('controlled-documents', ControlledDocumentViewSet, basename='opc-controlled-document')
router.register('document-revisions', ControlledDocumentRevisionViewSet, basename='opc-document-revision')
router.register('material-candidates', OPCMaterialCandidateViewSet, basename='opc-material-candidate')
router.register('change-requests', EngineeringChangeRequestViewSet, basename='opc-change-request')
router.register('change-orders', EngineeringChangeOrderViewSet, basename='opc-change-order')
router.register('change-object-links', EngineeringChangeObjectLinkViewSet, basename='opc-change-object-link')
router.register('impact-analysis', EngineeringImpactViewSet, basename='opc-impact-analysis')
router.register('production-orders', ProductionOrderViewSet, basename='opc-production-order')
router.register('operation-executions', OperationExecutionViewSet, basename='opc-operation-execution')
router.register('production-lots', ProductionLotViewSet, basename='opc-production-lot')
router.register('production-serials', ProductionSerialViewSet, basename='opc-production-serial')
router.register('warehouses', WarehouseViewSet, basename='opc-warehouse')
router.register('storage-locations', StorageLocationViewSet, basename='opc-storage-location')
router.register('inventory-balances', InventoryBalanceViewSet, basename='opc-inventory-balance')
router.register('inventory-transactions', InventoryTransactionViewSet, basename='opc-inventory-transaction')
router.register('inventory-reservations', InventoryReservationViewSet, basename='opc-inventory-reservation')
router.register('material-control', MaterialControlViewSet, basename='opc-material-control')
router.register('planning-calendars', PlanningCalendarViewSet, basename='opc-planning-calendar')
router.register('planning-policies', ItemPlanningPolicyViewSet, basename='opc-planning-policy')
router.register('planning-demands', PlanningDemandViewSet, basename='opc-planning-demand')
router.register('scheduled-supplies', ScheduledSupplyViewSet, basename='opc-scheduled-supply')
router.register('mrp-runs', MRPRunViewSet, basename='opc-mrp-run')
router.register('mrp-recommendations', MRPRecommendationViewSet, basename='opc-mrp-recommendation')
router.register('mrp-pegging', MRPPeggingViewSet, basename='opc-mrp-pegging')
router.register('purchase-requisitions', PurchaseRequisitionViewSet, basename='opc-purchase-requisition')
router.register('suppliers', SupplierViewSet, basename='opc-supplier')
router.register('supplier-sites', SupplierSiteViewSet, basename='opc-supplier-site')
router.register('supplier-contacts', SupplierContactViewSet, basename='opc-supplier-contact')
router.register('supplier-capabilities', SupplierCapabilityViewSet, basename='opc-supplier-capability')
router.register('approved-supplier-items', ApprovedSupplierItemViewSet, basename='opc-approved-supplier-item')
router.register('rfqs', RequestForQuotationViewSet, basename='opc-rfq')
router.register('rfq-lines', RFQLineViewSet, basename='opc-rfq-line')
router.register('rfq-invitations', RFQSupplierInvitationViewSet, basename='opc-rfq-invitation')
router.register('supplier-quotations', SupplierQuotationViewSet, basename='opc-supplier-quotation')
router.register('supplier-quotation-lines', SupplierQuotationLineViewSet, basename='opc-supplier-quotation-line')
router.register('quotation-comparisons', QuotationComparisonSnapshotViewSet, basename='opc-quotation-comparison')
router.register('sourcing-decisions', SourcingDecisionViewSet, basename='opc-sourcing-decision')
router.register('sourcing-decision-lines', SourcingDecisionLineViewSet, basename='opc-sourcing-decision-line')
router.register('purchase-orders', PurchaseOrderViewSet, basename='opc-purchase-order')
router.register('purchase-order-lines', PurchaseOrderLineViewSet, basename='opc-purchase-order-line')
router.register('purchase-order-schedules', PurchaseOrderDeliveryScheduleViewSet, basename='opc-purchase-order-schedule')
router.register('purchase-receipts', PurchaseReceiptViewSet, basename='opc-purchase-receipt')
router.register('purchase-receipt-lines', PurchaseReceiptLineViewSet, basename='opc-purchase-receipt-line')
router.register('purchase-returns', PurchaseReturnViewSet, basename='opc-purchase-return')
router.register('procurement-events', ProcurementEventViewSet, basename='opc-procurement-event')
router.register('cost-rates', CostRateViewSet, basename='opc-cost-rate')
router.register('currency-rate-policies', CurrencyRatePolicyViewSet, basename='opc-currency-rate-policy')
router.register('cost-snapshots', CostSnapshotViewSet, basename='opc-cost-snapshot')
router.register('supplier-performance-snapshots', SupplierPerformanceSnapshotViewSet, basename='opc-supplier-performance-snapshot')
router.register('operational-kpi-snapshots', OperationalKPISnapshotViewSet, basename='opc-operational-kpi-snapshot')
router.register('resource-calendars', ResourceCalendarViewSet, basename='opc-resource-calendar')
router.register('shifts', ShiftViewSet, basename='opc-shift')
router.register('calendar-exceptions', CalendarExceptionViewSet, basename='opc-calendar-exception')
router.register('work-center-capacities', WorkCenterCapacityViewSet, basename='opc-work-center-capacity')
router.register('machine-capacities', MachineCapacityViewSet, basename='opc-machine-capacity')
router.register('labor-skills', LaborSkillViewSet, basename='opc-labor-skill')
router.register('work-center-labor-capacities', WorkCenterLaborCapacityViewSet, basename='opc-work-center-labor-capacity')
router.register('scheduling-policies', SchedulingPolicyViewSet, basename='opc-scheduling-policy')
router.register('scheduling-runs', SchedulingRunViewSet, basename='opc-scheduling-run')
router.register('scheduled-operation-assignments', ScheduledOperationAssignmentViewSet, basename='opc-scheduled-operation-assignment')
router.register('scheduling-operation-snapshots', SchedulingOperationSnapshotViewSet, basename='opc-scheduling-operation-snapshot')
router.register('scheduling-calendar-snapshots', SchedulingCalendarSnapshotViewSet, basename='opc-scheduling-calendar-snapshot')
router.register('capacity-buckets', CapacityBucketViewSet, basename='opc-capacity-bucket')
router.register('scheduling-exceptions', SchedulingExceptionViewSet, basename='opc-scheduling-exception')
router.register('maintenance-dashboard', MaintenanceDashboardViewSet, basename='opc-maintenance-dashboard')
router.register('maintainable-assets', MaintainableAssetViewSet, basename='opc-maintainable-asset')
router.register('machine-maintenance-profiles', MachineMaintenanceProfileViewSet, basename='opc-machine-maintenance-profile')
router.register('failure-codes', FailureCodeViewSet, basename='opc-failure-code')
router.register('cause-codes', CauseCodeViewSet, basename='opc-cause-code')
router.register('remedy-codes', RemedyCodeViewSet, basename='opc-remedy-code')
router.register('asset-meters', AssetMeterViewSet, basename='opc-asset-meter')
router.register('asset-meter-readings', AssetMeterReadingViewSet, basename='opc-asset-meter-reading')
router.register('maintenance-task-templates', MaintenanceTaskTemplateViewSet, basename='opc-maintenance-task-template')
router.register('maintenance-task-checklist-items', MaintenanceTaskChecklistItemViewSet, basename='opc-maintenance-task-checklist-item')
router.register('preventive-maintenance-plans', PreventiveMaintenancePlanViewSet, basename='opc-preventive-maintenance-plan')
router.register('maintenance-requests', MaintenanceRequestViewSet, basename='opc-maintenance-request')
router.register('maintenance-work-orders', MaintenanceWorkOrderViewSet, basename='opc-maintenance-work-order')
router.register('maintenance-checklist-results', MaintenanceChecklistResultViewSet, basename='opc-maintenance-checklist-result')
router.register('maintenance-spare-part-requirements', MaintenanceSparePartRequirementViewSet, basename='opc-maintenance-spare-part-requirement')
router.register('maintenance-spare-part-issues', MaintenanceSparePartIssueViewSet, basename='opc-maintenance-spare-part-issue')
router.register('maintenance-events', MaintenanceEventViewSet, basename='opc-maintenance-event')
router.register('machine-downtime-events', MachineDowntimeEventViewSet, basename='opc-machine-downtime-event')
router.register('inspection-plans', InspectionPlanViewSet, basename='opc-inspection-plan')
router.register('inspection-plan-revisions', InspectionPlanRevisionViewSet, basename='opc-inspection-plan-revision')
router.register('inspection-characteristics', InspectionCharacteristicViewSet, basename='opc-inspection-characteristic')
router.register('inspection-applicabilities', InspectionApplicabilityViewSet, basename='opc-inspection-applicability')
router.register('inspection-executions', InspectionExecutionViewSet, basename='opc-inspection-execution')
router.register('quality-holds', QualityHoldViewSet, basename='opc-quality-hold')
router.register('nonconformances', NonconformanceRecordViewSet, basename='opc-nonconformance')
router.register('quality-dispositions', QualityDispositionViewSet, basename='opc-quality-disposition')
router.register('diagrams', OPCDiagramViewSet, basename='opc-diagram')
router.register('nodes', OPCNodeViewSet, basename='opc-node')
router.register('edges', OPCEdgeViewSet, basename='opc-edge')

urlpatterns = [
    path('', include(router.urls)),
]
