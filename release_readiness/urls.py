from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    BackupEvidenceViewSet,
    BarcodeIdentityViewSet,
    CutoverRehearsalViewSet,
    FinalProductionDecisionViewSet,
    ExportJobViewSet,
    ImportJobViewSet,
    IntegrationOutboxViewSet,
    MasterDataBatchViewSet,
    NotificationPreferenceViewSet,
    NotificationSubscriptionViewSet,
    NotificationViewSet,
    OperationalChangeRecordViewSet,
    OperationalDefectViewSet,
    PilotScopeViewSet,
    PilotSignoffChecklistViewSet,
    PilotSignoffRequirementViewSet,
    PilotUserProvisioningViewSet,
    ProductionPilotViewSet,
    ReconciliationSnapshotViewSet,
    ReleaseBaselineViewSet,
    ReleaseDashboardViewSet,
    TrainingAssignmentViewSet,
    TrainingCourseViewSet,
    UATCampaignViewSet,
    UATExecutionViewSet,
    UATScenarioViewSet,
    health,
    liveness,
    production_config,
    readiness,
)

router = DefaultRouter()
router.register('notifications', NotificationViewSet, basename='release-notification')
router.register('notification-preferences', NotificationPreferenceViewSet, basename='release-notification-preference')
router.register('notification-subscriptions', NotificationSubscriptionViewSet, basename='release-notification-subscription')
router.register('barcode-identities', BarcodeIdentityViewSet, basename='release-barcode-identity')
router.register('imports', ImportJobViewSet, basename='release-import')
router.register('exports', ExportJobViewSet, basename='release-export')
router.register('outbox', IntegrationOutboxViewSet, basename='release-outbox')
router.register('dashboard', ReleaseDashboardViewSet, basename='release-dashboard')
router.register('release-baselines', ReleaseBaselineViewSet, basename='release-baseline')
router.register('change-records', OperationalChangeRecordViewSet, basename='release-change-record')
router.register('signoff-checklists', PilotSignoffChecklistViewSet, basename='release-signoff-checklist')
router.register('signoff-requirements', PilotSignoffRequirementViewSet, basename='release-signoff-requirement')
router.register('pilots', ProductionPilotViewSet, basename='release-pilot')
router.register('pilot-scopes', PilotScopeViewSet, basename='release-pilot-scope')
router.register('pilot-users', PilotUserProvisioningViewSet, basename='release-pilot-user')
router.register('master-data-batches', MasterDataBatchViewSet, basename='release-master-data-batch')
router.register('uat-campaigns', UATCampaignViewSet, basename='release-uat-campaign')
router.register('uat-scenarios', UATScenarioViewSet, basename='release-uat-scenario')
router.register('uat-executions', UATExecutionViewSet, basename='release-uat-execution')
router.register('training-courses', TrainingCourseViewSet, basename='release-training-course')
router.register('training-assignments', TrainingAssignmentViewSet, basename='release-training-assignment')
router.register('defects', OperationalDefectViewSet, basename='release-defect')
router.register('reconciliations', ReconciliationSnapshotViewSet, basename='release-reconciliation')
router.register('cutover-rehearsals', CutoverRehearsalViewSet, basename='release-cutover-rehearsal')
router.register('backup-evidence', BackupEvidenceViewSet, basename='release-backup-evidence')
router.register('final-decisions', FinalProductionDecisionViewSet, basename='release-final-decision')

urlpatterns = [
    path('health/', health, name='release-health'),
    path('liveness/', liveness, name='release-liveness'),
    path('readiness/', readiness, name='release-readiness'),
    path('production-config/', production_config, name='release-production-config'),
    path('', include(router.urls)),
]
