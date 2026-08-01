import csv
import io

from django.http import HttpResponse
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from auditlog.services import log_event
from opc.models import Supplier

from .models import (
    BackupEvidence,
    CutoverRehearsal,
    FinalProductionDecision,
    ExportJob,
    ImportJob,
    IntegrationOutboxEvent,
    MasterDataBatch,
    Notification,
    NotificationPreference,
    NotificationSubscription,
    OperationalChangeRecord,
    OperationalDefect,
    PilotSignoffChecklist,
    PilotSignoffRecord,
    PilotSignoffRequirement,
    PilotScope,
    PilotUserProvisioning,
    ProductionPilot,
    ReconciliationSnapshot,
    ReleaseBaseline,
    TrainingAssignment,
    TrainingCourse,
    UATCampaign,
    UATExecution,
    UATScenario,
)
from .serializers import (
    BackupEvidenceSerializer,
    CutoverRehearsalSerializer,
    FinalProductionDecisionSerializer,
    ExportJobSerializer,
    ImportJobSerializer,
    IntegrationOutboxEventSerializer,
    MasterDataBatchSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
    NotificationSubscriptionSerializer,
    OperationalChangeRecordSerializer,
    OperationalDefectSerializer,
    PilotScopeSerializer,
    PilotSignoffChecklistSerializer,
    PilotSignoffRecordSerializer,
    PilotSignoffRequirementSerializer,
    PilotUserProvisioningSerializer,
    ProductionPilotSerializer,
    ReconciliationSnapshotSerializer,
    ReleaseBaselineSerializer,
    TrainingAssignmentSerializer,
    TrainingCourseSerializer,
    UATCampaignSerializer,
    UATExecutionSerializer,
    UATScenarioSerializer,
)
from .services import (
    access_review,
    create_default_signoff_checklist,
    create_notification,
    create_outbox_event,
    evaluate_signoff_checklist,
    final_technical_status,
    freeze_release_baseline,
    health_payload,
    mark_outbox_attempt,
    parse_csv_import,
    pilot_gate_summary,
    record_signoff,
    resolve_identity,
    sign_identity,
    spreadsheet_safe,
    transition_pilot,
    validate_production_configuration,
)


@api_view(['GET'])
@permission_classes([])
def liveness(request):
    return Response({'status': 'alive', 'request_id': getattr(request, 'request_id', '')})


@api_view(['GET'])
@permission_classes([])
def readiness(request):
    payload = health_payload()
    return Response(payload, status=status.HTTP_200_OK if payload['status'] == 'ok' else status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(['GET'])
@permission_classes([])
def health(request):
    return Response(health_payload())


@api_view(['GET'])
@permission_classes([IsAdminUser])
def production_config(request):
    return Response(validate_production_configuration())


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)

    @action(detail=True, methods=['post'])
    def read(self, request, pk=None):
        row = self.get_object()
        row.read_at = timezone.now()
        row.save(update_fields=['read_at'])
        return Response(self.get_serializer(row).data)

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        row = self.get_object()
        row.acknowledged_at = timezone.now()
        row.save(update_fields=['acknowledged_at'])
        return Response(self.get_serializer(row).data)


class NotificationPreferenceViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationPreferenceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return NotificationPreference.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class NotificationSubscriptionViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return NotificationSubscription.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class BarcodeIdentityViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='sign')
    def sign(self, request):
        if not request.user.is_staff:
            return Response({'detail': 'Only privileged users can mint operational identity payloads.'}, status=status.HTTP_403_FORBIDDEN)
        token = sign_identity(request.data.get('object_type'), request.data.get('object_id'))
        return Response({'payload_version': 'NVR1', 'token': token})

    @action(detail=False, methods=['post'], url_path='resolve')
    def resolve(self, request):
        try:
            resolved = resolve_identity(request.data.get('token', ''), actor=request.user, request=request)
        except Exception:
            return Response({'detail': 'Invalid or unauthorized operational identity.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(resolved)


class ImportJobViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ImportJobSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ImportJob.objects.filter(created_by=self.request.user).prefetch_related('rows')

    @action(detail=False, methods=['post'], url_path='csv')
    def csv(self, request):
        uploaded = request.FILES.get('file')
        content = uploaded.read() if uploaded else (request.data.get('content', '') or '').encode('utf-8')
        job = parse_csv_import(
            actor=request.user,
            schema_name=request.data.get('schema_name', 'supplier-basic'),
            schema_version=request.data.get('schema_version', 'v1'),
            content=content,
            idempotency_key=request.data.get('idempotency_key', ''),
            dry_run=str(request.data.get('dry_run', 'true')).lower() not in {'0', 'false', 'no'},
            file_name=uploaded.name if uploaded else 'inline.csv',
        )
        return Response(self.get_serializer(job).data, status=status.HTTP_201_CREATED)


class ExportJobViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ExportJobSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ExportJob.objects.filter(requested_by=self.request.user)

    @action(detail=False, methods=['get'], url_path='suppliers-csv')
    def suppliers_csv(self, request):
        if not request.user.is_staff:
            return Response({'detail': 'Export requires privileged access.'}, status=status.HTTP_403_FORBIDDEN)
        limit = min(int(request.query_params.get('limit', 500)), 5000)
        rows = list(Supplier.objects.order_by('supplier_code')[:limit])
        job = ExportJob.objects.create(export_type='suppliers-csv', row_count=len(rows), filters={'limit': limit}, requested_by=request.user)
        log_event('export_suppliers_csv', target=job, category='data', request=request)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['supplier_code', 'name', 'status'])
        for row in rows:
            writer.writerow([spreadsheet_safe(row.supplier_code), spreadsheet_safe(row.name), spreadsheet_safe(row.status)])
        response = HttpResponse(output.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="suppliers.csv"'
        return response


class IntegrationOutboxViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = IntegrationOutboxEventSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not self.request.user.is_staff:
            return IntegrationOutboxEvent.objects.none()
        return IntegrationOutboxEvent.objects.all()

    @action(detail=False, methods=['post'], url_path='create-test')
    def create_test(self, request):
        if not request.user.is_staff:
            return Response({'detail': 'Outbox test events require privileged access.'}, status=status.HTTP_403_FORBIDDEN)
        event = create_outbox_event(
            event_type=request.data.get('event_type', 'release.test'),
            aggregate_type=request.data.get('aggregate_type', 'ReleaseReadiness'),
            aggregate_id=request.data.get('aggregate_id', 'phase16'),
            payload=request.data.get('payload', {}),
            correlation_id=getattr(request, 'correlation_id', ''),
            command_id=getattr(request, 'command_id', ''),
        )
        return Response(self.get_serializer(event).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='attempt')
    def attempt(self, request, pk=None):
        if not request.user.is_staff:
            return Response({'detail': 'Outbox delivery attempts require privileged access.'}, status=status.HTTP_403_FORBIDDEN)
        event = mark_outbox_attempt(self.get_object(), success=bool(request.data.get('success')), error=request.data.get('error', ''))
        return Response(self.get_serializer(event).data)


class ReleaseDashboardViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        return Response({
            'health': health_payload(),
            'notifications_unread': Notification.objects.filter(recipient=request.user, read_at__isnull=True).count(),
            'outbox_pending': IntegrationOutboxEvent.objects.filter(status=IntegrationOutboxEvent.STATUS_PENDING).count() if request.user.is_staff else None,
            'email_provider': 'configured' if getattr(__import__('django.conf').conf.settings, 'NOVIRA_EMAIL_PROVIDER', '') else 'not_configured',
            'active_pilot': ProductionPilot.objects.order_by('-created_at').values('pilot_number', 'status').first(),
            'critical_defects_open': OperationalDefect.objects.filter(status=OperationalDefect.STATUS_OPEN, severity=OperationalDefect.SEV_CRITICAL).count(),
            'high_defects_open': OperationalDefect.objects.filter(status=OperationalDefect.STATUS_OPEN, severity=OperationalDefect.SEV_HIGH).count(),
        })


class AdminModelViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]


class ReleaseBaselineViewSet(AdminModelViewSet):
    queryset = ReleaseBaseline.objects.all()
    serializer_class = ReleaseBaselineSerializer

    @action(detail=False, methods=['post'], url_path='freeze')
    def freeze(self, request):
        baseline = freeze_release_baseline(actor=request.user, release_name=request.data.get('release_name', 'Novira Final Program'), evidence=request.data.get('evidence', {}))
        return Response(self.get_serializer(baseline).data, status=status.HTTP_201_CREATED)


class OperationalChangeRecordViewSet(AdminModelViewSet):
    queryset = OperationalChangeRecord.objects.all()
    serializer_class = OperationalChangeRecordSerializer

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)


class PilotSignoffChecklistViewSet(AdminModelViewSet):
    queryset = PilotSignoffChecklist.objects.prefetch_related('requirements__records').all()
    serializer_class = PilotSignoffChecklistSerializer

    @action(detail=False, methods=['post'], url_path='create-default')
    def create_default(self, request):
        baseline = ReleaseBaseline.objects.filter(pk=request.data.get('release_baseline')).first() if request.data.get('release_baseline') else None
        checklist = create_default_signoff_checklist(actor=request.user, release_baseline=baseline, title=request.data.get('title', 'Production pilot sign-off'))
        return Response(self.get_serializer(checklist).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='evaluation')
    def evaluation(self, request, pk=None):
        return Response(evaluate_signoff_checklist(self.get_object()))


class PilotSignoffRequirementViewSet(AdminModelViewSet):
    queryset = PilotSignoffRequirement.objects.select_related('checklist', 'assigned_user').prefetch_related('records').all()
    serializer_class = PilotSignoffRequirementSerializer

    @action(detail=True, methods=['post'], url_path='record')
    def record(self, request, pk=None):
        try:
            row = record_signoff(
                requirement=self.get_object(),
                actor=request.user,
                decision=request.data.get('decision'),
                comment=request.data.get('comment', ''),
                evidence_reference=request.data.get('evidence_reference', ''),
                conditions=request.data.get('conditions', []),
                supersedes=PilotSignoffRecord.objects.filter(pk=request.data.get('supersedes')).first() if request.data.get('supersedes') else None,
            )
        except (PermissionDenied, ValidationError) as exc:
            return Response(getattr(exc, 'message_dict', {'detail': str(exc)}), status=status.HTTP_400_BAD_REQUEST)
        return Response(PilotSignoffRecordSerializer(row).data, status=status.HTTP_201_CREATED)


class ProductionPilotViewSet(AdminModelViewSet):
    queryset = ProductionPilot.objects.select_related('release_baseline', 'checklist').all()
    serializer_class = ProductionPilotSerializer

    @action(detail=True, methods=['post'], url_path='transition')
    def transition(self, request, pk=None):
        try:
            pilot = transition_pilot(pilot=self.get_object(), actor=request.user, action=request.data.get('action'), expected_version=request.data.get('expected_version'))
        except (PermissionDenied, ValidationError) as exc:
            return Response(getattr(exc, 'message_dict', {'detail': str(exc)}), status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(pilot).data)

    @action(detail=True, methods=['get'], url_path='gate')
    def gate(self, request, pk=None):
        return Response(pilot_gate_summary(self.get_object()))

    @action(detail=True, methods=['get'], url_path='access-review')
    def access(self, request, pk=None):
        return Response(access_review(self.get_object()))

    @action(detail=True, methods=['get'], url_path='technical-status')
    def technical_status(self, request, pk=None):
        return Response(final_technical_status(self.get_object()))


class PilotScopeViewSet(AdminModelViewSet):
    queryset = PilotScope.objects.select_related('pilot').all()
    serializer_class = PilotScopeSerializer


class PilotUserProvisioningViewSet(AdminModelViewSet):
    queryset = PilotUserProvisioning.objects.select_related('pilot', 'user').all()
    serializer_class = PilotUserProvisioningSerializer


class MasterDataBatchViewSet(AdminModelViewSet):
    queryset = MasterDataBatch.objects.select_related('pilot').all()
    serializer_class = MasterDataBatchSerializer


class UATCampaignViewSet(AdminModelViewSet):
    queryset = UATCampaign.objects.select_related('pilot').all()
    serializer_class = UATCampaignSerializer


class UATScenarioViewSet(AdminModelViewSet):
    queryset = UATScenario.objects.select_related('campaign').all()
    serializer_class = UATScenarioSerializer


class UATExecutionViewSet(AdminModelViewSet):
    queryset = UATExecution.objects.select_related('scenario', 'human_actor').all()
    serializer_class = UATExecutionSerializer


class TrainingCourseViewSet(AdminModelViewSet):
    queryset = TrainingCourse.objects.all()
    serializer_class = TrainingCourseSerializer


class TrainingAssignmentViewSet(AdminModelViewSet):
    queryset = TrainingAssignment.objects.select_related('course', 'user', 'pilot').all()
    serializer_class = TrainingAssignmentSerializer


class OperationalDefectViewSet(AdminModelViewSet):
    queryset = OperationalDefect.objects.select_related('pilot').all()
    serializer_class = OperationalDefectSerializer


class ReconciliationSnapshotViewSet(AdminModelViewSet):
    queryset = ReconciliationSnapshot.objects.select_related('pilot').all()
    serializer_class = ReconciliationSnapshotSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class CutoverRehearsalViewSet(AdminModelViewSet):
    queryset = CutoverRehearsal.objects.select_related('pilot').all()
    serializer_class = CutoverRehearsalSerializer


class BackupEvidenceViewSet(AdminModelViewSet):
    queryset = BackupEvidence.objects.all()
    serializer_class = BackupEvidenceSerializer


class FinalProductionDecisionViewSet(AdminModelViewSet):
    queryset = FinalProductionDecision.objects.select_related('pilot', 'human_actor').all()
    serializer_class = FinalProductionDecisionSerializer

    def perform_create(self, serializer):
        serializer.save(human_actor=self.request.user)
