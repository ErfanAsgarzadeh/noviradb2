from rest_framework import serializers

from .models import (
    BackupEvidence,
    CutoverRehearsal,
    FinalProductionDecision,
    ExportJob,
    ImportJob,
    ImportJobRow,
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


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ['id', 'user', 'in_app_enabled', 'email_enabled', 'categories', 'created_at', 'updated_at']
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


class NotificationSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationSubscription
        fields = ['id', 'user', 'category', 'object_type', 'object_id', 'active', 'created_at']
        read_only_fields = ['id', 'user', 'created_at']


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'recipient', 'category', 'severity', 'title', 'body', 'object_type', 'object_id', 'dedupe_key', 'read_at', 'acknowledged_at', 'escalation_at', 'email_provider_state', 'created_at']
        read_only_fields = fields


class ImportJobRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportJobRow
        fields = ['id', 'row_number', 'payload', 'errors', 'warnings']


class ImportJobSerializer(serializers.ModelSerializer):
    rows = ImportJobRowSerializer(many=True, read_only=True)

    class Meta:
        model = ImportJob
        fields = ['id', 'schema_version', 'schema_name', 'idempotency_key', 'status', 'dry_run', 'file_name', 'row_count', 'valid_count', 'error_count', 'summary', 'created_by', 'created_at', 'rows']
        read_only_fields = fields


class ExportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExportJob
        fields = ['id', 'export_type', 'row_count', 'filters', 'requested_by', 'created_at']
        read_only_fields = fields


class IntegrationOutboxEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntegrationOutboxEvent
        fields = ['id', 'event_id', 'event_type', 'schema_version', 'aggregate_type', 'aggregate_id', 'payload', 'status', 'attempts', 'last_error', 'next_attempt_at', 'correlation_id', 'command_id', 'created_at', 'delivered_at']
        read_only_fields = fields


class ReleaseBaselineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReleaseBaseline
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'frozen_at']


class OperationalChangeRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = OperationalChangeRecord
        fields = '__all__'
        read_only_fields = ['id', 'requested_by', 'created_at', 'updated_at']


class PilotSignoffRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = PilotSignoffRecord
        fields = '__all__'
        read_only_fields = ['id', 'human_actor', 'requirement_version', 'server_recorded_identity', 'recorded_at']


class PilotSignoffRequirementSerializer(serializers.ModelSerializer):
    records = PilotSignoffRecordSerializer(many=True, read_only=True)

    class Meta:
        model = PilotSignoffRequirement
        fields = '__all__'


class PilotSignoffChecklistSerializer(serializers.ModelSerializer):
    requirements = PilotSignoffRequirementSerializer(many=True, read_only=True)

    class Meta:
        model = PilotSignoffChecklist
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at']


class PilotScopeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PilotScope
        fields = '__all__'


class PilotUserProvisioningSerializer(serializers.ModelSerializer):
    class Meta:
        model = PilotUserProvisioning
        fields = '__all__'


class ProductionPilotSerializer(serializers.ModelSerializer):
    scope = PilotScopeSerializer(read_only=True)

    class Meta:
        model = ProductionPilot
        fields = '__all__'
        read_only_fields = ['id', 'activated_at', 'activated_by', 'created_at', 'updated_at']


class MasterDataBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = MasterDataBatch
        fields = '__all__'
        read_only_fields = ['id', 'created_at']


class UATCampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = UATCampaign
        fields = '__all__'


class UATScenarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = UATScenario
        fields = '__all__'


class UATExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = UATExecution
        fields = '__all__'
        read_only_fields = ['id', 'recorded_at']


class TrainingCourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainingCourse
        fields = '__all__'


class TrainingAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainingAssignment
        fields = '__all__'
        read_only_fields = ['id', 'assigned_at']


class OperationalDefectSerializer(serializers.ModelSerializer):
    class Meta:
        model = OperationalDefect
        fields = '__all__'
        read_only_fields = ['id', 'created_at']


class ReconciliationSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReconciliationSnapshot
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at']


class CutoverRehearsalSerializer(serializers.ModelSerializer):
    class Meta:
        model = CutoverRehearsal
        fields = '__all__'


class BackupEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BackupEvidence
        fields = '__all__'
        read_only_fields = ['id', 'created_at']


class FinalProductionDecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinalProductionDecision
        fields = '__all__'
        read_only_fields = ['id', 'human_actor', 'recorded_at']
