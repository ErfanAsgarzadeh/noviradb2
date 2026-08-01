import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class NotificationPreference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_preference')
    in_app_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=False)
    categories = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class NotificationSubscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_subscriptions')
    category = models.CharField(max_length=80)
    object_type = models.CharField(max_length=120, blank=True)
    object_id = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'category', 'object_type', 'object_id')]


class Notification(models.Model):
    SEVERITY_INFO = 'INFO'
    SEVERITY_WARNING = 'WARNING'
    SEVERITY_CRITICAL = 'CRITICAL'
    SEVERITY_CHOICES = [(SEVERITY_INFO, 'Info'), (SEVERITY_WARNING, 'Warning'), (SEVERITY_CRITICAL, 'Critical')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    category = models.CharField(max_length=80)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default=SEVERITY_INFO)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    object_type = models.CharField(max_length=120, blank=True)
    object_id = models.CharField(max_length=120, blank=True)
    dedupe_key = models.CharField(max_length=160, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    escalation_at = models.DateTimeField(null=True, blank=True)
    email_provider_state = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['recipient', 'dedupe_key'], condition=~models.Q(dedupe_key=''), name='unique_notification_dedupe'),
        ]
        indexes = [
            models.Index(fields=['recipient', 'read_at']),
            models.Index(fields=['recipient', '-created_at']),
            models.Index(fields=['category', 'severity']),
        ]


class BarcodeResolutionAudit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='barcode_resolution_audits')
    payload_version = models.CharField(max_length=20)
    object_type = models.CharField(max_length=120)
    object_id = models.CharField(max_length=120)
    success = models.BooleanField(default=True)
    reason = models.CharField(max_length=120, blank=True)
    request_id = models.CharField(max_length=80, blank=True)
    correlation_id = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk and BarcodeResolutionAudit.objects.filter(pk=self.pk).exists():
            raise ValidationError({'barcode_audit': 'Barcode resolution audit rows are immutable.'})
        super().save(*args, **kwargs)


class ImportJob(models.Model):
    STATUS_DRY_RUN = 'DRY_RUN'
    STATUS_VALIDATED = 'VALIDATED'
    STATUS_APPLIED = 'APPLIED'
    STATUS_FAILED = 'FAILED'
    STATUS_CHOICES = [(STATUS_DRY_RUN, 'Dry run'), (STATUS_VALIDATED, 'Validated'), (STATUS_APPLIED, 'Applied'), (STATUS_FAILED, 'Failed')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schema_version = models.CharField(max_length=40)
    schema_name = models.CharField(max_length=80)
    idempotency_key = models.CharField(max_length=160)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRY_RUN)
    dry_run = models.BooleanField(default=True)
    file_name = models.CharField(max_length=255, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    valid_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    summary = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='import_jobs')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('schema_name', 'idempotency_key')]
        ordering = ['-created_at']


class ImportJobRow(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(ImportJob, on_delete=models.CASCADE, related_name='rows')
    row_number = models.PositiveIntegerField()
    payload = models.JSONField(default=dict, blank=True)
    errors = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['job', 'row_number']
        unique_together = [('job', 'row_number')]


class ExportJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    export_type = models.CharField(max_length=80)
    row_count = models.PositiveIntegerField(default=0)
    filters = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='export_jobs')
    created_at = models.DateTimeField(auto_now_add=True)


class IntegrationOutboxEvent(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_DELIVERED = 'DELIVERED'
    STATUS_FAILED = 'FAILED'
    STATUS_DEAD_LETTER = 'DEAD_LETTER'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_DELIVERED, 'Delivered'), (STATUS_FAILED, 'Failed'), (STATUS_DEAD_LETTER, 'Dead letter')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_id = models.CharField(max_length=120, unique=True)
    event_type = models.CharField(max_length=120)
    schema_version = models.CharField(max_length=40, default='v1')
    aggregate_type = models.CharField(max_length=120)
    aggregate_id = models.CharField(max_length=120)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    correlation_id = models.CharField(max_length=80, blank=True)
    command_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        indexes = [models.Index(fields=['status', 'next_attempt_at']), models.Index(fields=['aggregate_type', 'aggregate_id'])]

    def save(self, *args, **kwargs):
        if self.pk:
            original = IntegrationOutboxEvent.objects.filter(pk=self.pk).values('event_id', 'event_type', 'aggregate_type', 'aggregate_id', 'payload').first()
            if original and any(original[field] != getattr(self, field) for field in original):
                raise ValidationError({'outbox': 'Outbox event identity and payload are immutable.'})
        super().save(*args, **kwargs)


def _immutable_update(model, pk, label):
    if pk and model.objects.filter(pk=pk).exists():
        raise ValidationError({label: f'{label} rows are immutable; create a superseding record instead.'})


class ReleaseBaseline(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_FROZEN = 'FROZEN'
    STATUS_SUPERSEDED = 'SUPERSEDED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_FROZEN, 'Frozen'), (STATUS_SUPERSEDED, 'Superseded')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    release_name = models.CharField(max_length=160)
    release_identifier = models.CharField(max_length=160, unique=True)
    release_date = models.DateField(default=timezone.localdate)
    migration_leaves = models.JSONField(default=list, blank=True)
    selected_file_checksums = models.JSONField(default=dict, blank=True)
    backend_artifact_checksum = models.CharField(max_length=128, blank=True)
    frontend_build_checksum = models.CharField(max_length=128, blank=True)
    schema_signature = models.CharField(max_length=128)
    postgres_version = models.CharField(max_length=80, blank=True)
    backend_manifest_result = models.CharField(max_length=255, blank=True)
    frontend_manifest_result = models.CharField(max_length=255, blank=True)
    playwright_result = models.CharField(max_length=255, blank=True)
    backup_artifact_identity = models.CharField(max_length=255, blank=True)
    restore_rehearsal_identity = models.CharField(max_length=255, blank=True)
    known_warnings = models.JSONField(default=list, blank=True)
    residual_risks = models.JSONField(default=list, blank=True)
    pilot_constraints = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='release_baselines')
    created_at = models.DateTimeField(auto_now_add=True)
    frozen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class OperationalChangeRecord(models.Model):
    TYPE_CONFIG = 'CONFIG'
    TYPE_DATA = 'DATA'
    TYPE_SCOPE = 'SCOPE'
    TYPE_HOTFIX = 'HOTFIX'
    TYPE_CHOICES = [(TYPE_CONFIG, 'Configuration'), (TYPE_DATA, 'Data'), (TYPE_SCOPE, 'Scope'), (TYPE_HOTFIX, 'Hotfix')]
    STATUS_DRAFT = 'DRAFT'
    STATUS_REVIEW_PENDING = 'REVIEW_PENDING'
    STATUS_APPROVED = 'APPROVED'
    STATUS_IMPLEMENTING = 'IMPLEMENTING'
    STATUS_VALIDATING = 'VALIDATING'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_ROLLED_BACK = 'ROLLED_BACK'
    STATUS_REJECTED = 'REJECTED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'), (STATUS_REVIEW_PENDING, 'Review pending'), (STATUS_APPROVED, 'Approved'),
        (STATUS_IMPLEMENTING, 'Implementing'), (STATUS_VALIDATING, 'Validating'), (STATUS_COMPLETED, 'Completed'),
        (STATUS_ROLLED_BACK, 'Rolled back'), (STATUS_REJECTED, 'Rejected'), (STATUS_CANCELLED, 'Cancelled'),
    ]
    RISK_LOW = 'LOW'
    RISK_MEDIUM = 'MEDIUM'
    RISK_HIGH = 'HIGH'
    RISK_CRITICAL = 'CRITICAL'
    RISK_CHOICES = [(RISK_LOW, 'Low'), (RISK_MEDIUM, 'Medium'), (RISK_HIGH, 'High'), (RISK_CRITICAL, 'Critical')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    change_number = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    change_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    affected_domains = models.JSONField(default=list, blank=True)
    reason = models.TextField(blank=True)
    risk_classification = models.CharField(max_length=20, choices=RISK_CHOICES, default=RISK_LOW)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='requested_operational_changes')
    technical_reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='technical_operational_reviews')
    operational_approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='operational_change_approvals')
    planned_window = models.CharField(max_length=255, blank=True)
    implementation_status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    validation_evidence = models.JSONField(default=dict, blank=True)
    rollback_plan = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PilotSignoffChecklist(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_ACTIVE = 'ACTIVE'
    STATUS_SUPERSEDED = 'SUPERSEDED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_ACTIVE, 'Active'), (STATUS_SUPERSEDED, 'Superseded')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    checklist_number = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=255)
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    release_baseline = models.ForeignKey(ReleaseBaseline, null=True, blank=True, on_delete=models.PROTECT, related_name='signoff_checklists')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_signoff_checklists')
    created_at = models.DateTimeField(auto_now_add=True)


class PilotSignoffRequirement(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_APPROVED = 'APPROVED'
    STATUS_APPROVED_WITH_CONDITIONS = 'APPROVED_WITH_CONDITIONS'
    STATUS_REJECTED = 'REJECTED'
    STATUS_NOT_APPLICABLE = 'NOT_APPLICABLE'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'), (STATUS_APPROVED, 'Approved'),
        (STATUS_APPROVED_WITH_CONDITIONS, 'Approved with conditions'),
        (STATUS_REJECTED, 'Rejected'), (STATUS_NOT_APPLICABLE, 'Not applicable'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    checklist = models.ForeignKey(PilotSignoffChecklist, on_delete=models.CASCADE, related_name='requirements')
    area = models.CharField(max_length=120)
    responsible_role = models.CharField(max_length=120)
    assigned_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_signoff_requirements')
    requirement_text = models.TextField()
    mandatory = models.BooleanField(default=True)
    evidence_reference = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_PENDING)
    due_date = models.DateField(null=True, blank=True)
    blocking = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=0)
    conditions = models.JSONField(default=list, blank=True)

    class Meta:
        unique_together = [('checklist', 'area')]
        indexes = [models.Index(fields=['checklist', 'status']), models.Index(fields=['area', 'responsible_role'])]


class PilotSignoffRecord(models.Model):
    DECISION_APPROVED = 'APPROVED'
    DECISION_APPROVED_WITH_CONDITIONS = 'APPROVED_WITH_CONDITIONS'
    DECISION_REJECTED = 'REJECTED'
    DECISION_NOT_APPLICABLE = 'NOT_APPLICABLE'
    DECISION_CHOICES = [
        (DECISION_APPROVED, 'Approved'), (DECISION_APPROVED_WITH_CONDITIONS, 'Approved with conditions'),
        (DECISION_REJECTED, 'Rejected'), (DECISION_NOT_APPLICABLE, 'Not applicable'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requirement = models.ForeignKey(PilotSignoffRequirement, on_delete=models.PROTECT, related_name='records')
    supersedes = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='superseded_by')
    human_actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='pilot_signoff_records')
    decision = models.CharField(max_length=40, choices=DECISION_CHOICES)
    comment = models.TextField(blank=True)
    evidence_reference = models.CharField(max_length=255, blank=True)
    conditions = models.JSONField(default=list, blank=True)
    requirement_version = models.PositiveIntegerField()
    server_recorded_identity = models.CharField(max_length=255, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']

    def save(self, *args, **kwargs):
        _immutable_update(PilotSignoffRecord, self.pk, 'signoff_record')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'signoff_record': 'Sign-off records cannot be deleted.'})


class ProductionPilot(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_PREPARING = 'PREPARING'
    STATUS_SIGNOFF_PENDING = 'SIGNOFF_PENDING'
    STATUS_READY = 'READY'
    STATUS_ACTIVE = 'ACTIVE'
    STATUS_PAUSED = 'PAUSED'
    STATUS_STABILIZING = 'STABILIZING'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_FAILED = 'FAILED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'), (STATUS_PREPARING, 'Preparing'), (STATUS_SIGNOFF_PENDING, 'Signoff pending'),
        (STATUS_READY, 'Ready'), (STATUS_ACTIVE, 'Active'), (STATUS_PAUSED, 'Paused'),
        (STATUS_STABILIZING, 'Stabilizing'), (STATUS_COMPLETED, 'Completed'), (STATUS_FAILED, 'Failed'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pilot_number = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    release_baseline = models.ForeignKey(ReleaseBaseline, on_delete=models.PROTECT, related_name='production_pilots')
    checklist = models.ForeignKey(PilotSignoffChecklist, null=True, blank=True, on_delete=models.PROTECT, related_name='production_pilots')
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    activation_version = models.PositiveIntegerField(default=0)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='activated_pilots')
    incident_owner_role = models.CharField(max_length=120, blank=True)
    rollback_plan = models.TextField(blank=True)
    technical_gate_passed = models.BooleanField(default=False)
    uat_gate_passed = models.BooleanField(default=False)
    master_data_gate_passed = models.BooleanField(default=False)
    backup_gate_passed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PilotScope(models.Model):
    DATA_SYNTHETIC = 'SYNTHETIC'
    DATA_REFERENCE = 'REFERENCE'
    DATA_PILOT_LIVE = 'PILOT_LIVE'
    DATA_POST_PILOT = 'POST_PILOT'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pilot = models.OneToOneField(ProductionPilot, on_delete=models.CASCADE, related_name='scope')
    plants = models.JSONField(default=list, blank=True)
    warehouses = models.JSONField(default=list, blank=True)
    projects = models.JSONField(default=list, blank=True)
    item_families = models.JSONField(default=list, blank=True)
    suppliers = models.JSONField(default=list, blank=True)
    work_centers = models.JSONField(default=list, blank=True)
    machines = models.JSONField(default=list, blank=True)
    user_groups = models.JSONField(default=list, blank=True)
    date_range = models.JSONField(default=dict, blank=True)
    transaction_volume_limits = models.JSONField(default=dict, blank=True)
    enabled_domains = models.JSONField(default=list, blank=True)
    excluded_domains = models.JSONField(default=list, blank=True)
    data_origin = models.CharField(max_length=40, default=DATA_SYNTHETIC)
    version = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)


class PilotUserProvisioning(models.Model):
    ACCOUNT_PENDING = 'PENDING'
    ACCOUNT_ACTIVE = 'ACTIVE'
    ACCOUNT_DISABLED = 'DISABLED'
    TRAINING_NOT_ASSIGNED = 'NOT_ASSIGNED'
    TRAINING_ASSIGNED = 'ASSIGNED'
    TRAINING_COMPLETE = 'COMPLETE'
    TRAINING_EXPIRED = 'EXPIRED'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pilot = models.ForeignKey(ProductionPilot, on_delete=models.CASCADE, related_name='user_provisioning')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='pilot_provisioning')
    organizational_role = models.CharField(max_length=120)
    plant_scope = models.JSONField(default=list, blank=True)
    project_scope = models.JSONField(default=list, blank=True)
    warehouse_scope = models.JSONField(default=list, blank=True)
    supplier_scope = models.JSONField(default=list, blank=True)
    cost_visibility = models.CharField(max_length=40, default='NONE')
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    training_state = models.CharField(max_length=40, default=TRAINING_NOT_ASSIGNED)
    account_state = models.CharField(max_length=40, default=ACCOUNT_PENDING)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_pilot_users')
    elevated_access = models.BooleanField(default=False)
    conflicting_roles = models.JSONField(default=list, blank=True)

    class Meta:
        unique_together = [('pilot', 'user', 'organizational_role')]


class MasterDataBatch(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_DRY_RUN_PASSED = 'DRY_RUN_PASSED'
    STATUS_DRY_RUN_FAILED = 'DRY_RUN_FAILED'
    STATUS_APPROVED = 'APPROVED'
    STATUS_APPLIED = 'APPLIED'
    STATUS_RECONCILED = 'RECONCILED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_DRY_RUN_PASSED, 'Dry run passed'), (STATUS_DRY_RUN_FAILED, 'Dry run failed'), (STATUS_APPROVED, 'Approved'), (STATUS_APPLIED, 'Applied'), (STATUS_RECONCILED, 'Reconciled')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch_number = models.CharField(max_length=80, unique=True)
    pilot = models.ForeignKey(ProductionPilot, null=True, blank=True, on_delete=models.PROTECT, related_name='master_data_batches')
    domain = models.CharField(max_length=120)
    schema_version = models.CharField(max_length=40)
    source_system = models.CharField(max_length=120, blank=True)
    owner_role = models.CharField(max_length=120)
    approver_role = models.CharField(max_length=120)
    idempotency_key = models.CharField(max_length=160)
    dry_run_summary = models.JSONField(default=dict, blank=True)
    reconciliation_summary = models.JSONField(default=dict, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_master_data_batches')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('domain', 'idempotency_key')]


class UATCampaign(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pilot = models.ForeignKey(ProductionPilot, on_delete=models.CASCADE, related_name='uat_campaigns')
    campaign_number = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=255)
    status = models.CharField(max_length=40, default='DRAFT')
    technical_gate_passed = models.BooleanField(default=False)
    human_approval_recorded = models.BooleanField(default=False)


class UATScenario(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(UATCampaign, on_delete=models.CASCADE, related_name='scenarios')
    scenario_number = models.CharField(max_length=80)
    domain = models.CharField(max_length=120)
    title = models.CharField(max_length=255)
    mandatory = models.BooleanField(default=True)
    automated_evidence_reference = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [('campaign', 'scenario_number')]


class UATExecution(models.Model):
    RESULT_PASS = 'PASS'
    RESULT_FAIL = 'FAIL'
    RESULT_BLOCKED = 'BLOCKED'
    RESULT_CHOICES = [(RESULT_PASS, 'Pass'), (RESULT_FAIL, 'Fail'), (RESULT_BLOCKED, 'Blocked')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scenario = models.ForeignKey(UATScenario, on_delete=models.PROTECT, related_name='executions')
    human_actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='uat_executions')
    result = models.CharField(max_length=20, choices=RESULT_CHOICES)
    evidence_reference = models.CharField(max_length=255, blank=True)
    comment = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        _immutable_update(UATExecution, self.pk, 'uat_execution')
        super().save(*args, **kwargs)


class TrainingCourse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course_code = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=255)
    role = models.CharField(max_length=120)
    required = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)


class TrainingAssignment(models.Model):
    STATUS_ASSIGNED = 'ASSIGNED'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_EXPIRED = 'EXPIRED'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(TrainingCourse, on_delete=models.PROTECT, related_name='assignments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='training_assignments')
    pilot = models.ForeignKey(ProductionPilot, null=True, blank=True, on_delete=models.CASCADE, related_name='training_assignments')
    status = models.CharField(max_length=30, default=STATUS_ASSIGNED)
    assigned_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    evidence_reference = models.CharField(max_length=255, blank=True)


class OperationalDefect(models.Model):
    SEV_CRITICAL = 'CRITICAL'
    SEV_HIGH = 'HIGH'
    SEV_MEDIUM = 'MEDIUM'
    SEV_LOW = 'LOW'
    STATUS_OPEN = 'OPEN'
    STATUS_VERIFIED = 'VERIFIED'
    STATUS_DEFERRED = 'DEFERRED'
    STATUS_CLOSED = 'CLOSED'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    defect_number = models.CharField(max_length=80, unique=True)
    pilot = models.ForeignKey(ProductionPilot, null=True, blank=True, on_delete=models.PROTECT, related_name='defects')
    severity = models.CharField(max_length=20, default=SEV_MEDIUM)
    status = models.CharField(max_length=30, default=STATUS_OPEN)
    title = models.CharField(max_length=255)
    owner_role = models.CharField(max_length=120, blank=True)
    workaround = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ReconciliationSnapshot(models.Model):
    RESULT_CLEAN = 'CLEAN'
    RESULT_MISMATCH = 'MISMATCH'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pilot = models.ForeignKey(ProductionPilot, on_delete=models.CASCADE, related_name='reconciliation_snapshots')
    snapshot_number = models.CharField(max_length=80, unique=True)
    snapshot_type = models.CharField(max_length=80)
    result = models.CharField(max_length=30, default=RESULT_CLEAN)
    metrics = models.JSONField(default=dict, blank=True)
    unexplained_differences = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_reconciliations')
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        _immutable_update(ReconciliationSnapshot, self.pk, 'reconciliation_snapshot')
        super().save(*args, **kwargs)


class CutoverRehearsal(models.Model):
    RESULT_PASS = 'PASS'
    RESULT_FAIL = 'FAIL'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pilot = models.ForeignKey(ProductionPilot, null=True, blank=True, on_delete=models.PROTECT, related_name='cutover_rehearsals')
    rehearsal_number = models.CharField(max_length=80, unique=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    steps = models.JSONField(default=list, blank=True)
    backup_reference = models.CharField(max_length=255, blank=True)
    reconciliation_reference = models.CharField(max_length=255, blank=True)
    smoke_test_reference = models.CharField(max_length=255, blank=True)
    activation_simulation = models.BooleanField(default=False)
    rollback_simulation = models.BooleanField(default=False)
    restore_reference = models.CharField(max_length=255, blank=True)
    result = models.CharField(max_length=20, default=RESULT_FAIL)
    issues = models.JSONField(default=list, blank=True)


class BackupEvidence(models.Model):
    STATUS_SUCCESS = 'SUCCESS'
    STATUS_FAILED = 'FAILED'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evidence_number = models.CharField(max_length=80, unique=True)
    database_name = models.CharField(max_length=120)
    artifact_identity = models.CharField(max_length=255)
    checksum = models.CharField(max_length=128)
    table_count = models.PositiveIntegerField()
    index_count = models.PositiveIntegerField()
    restored_database_name = models.CharField(max_length=120, blank=True)
    restore_verified = models.BooleanField(default=False)
    status = models.CharField(max_length=20, default=STATUS_SUCCESS)
    created_at = models.DateTimeField(auto_now_add=True)


class FinalProductionDecision(models.Model):
    DECISION_GO = 'GO'
    DECISION_CONDITIONAL_GO = 'CONDITIONAL_GO'
    DECISION_NO_GO = 'NO_GO'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pilot = models.ForeignKey(ProductionPilot, on_delete=models.PROTECT, related_name='final_decisions')
    supersedes = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='superseded_by')
    human_actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='final_production_decisions')
    decision = models.CharField(max_length=40)
    conditions = models.JSONField(default=list, blank=True)
    evidence_reference = models.CharField(max_length=255, blank=True)
    comment = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        _immutable_update(FinalProductionDecision, self.pk, 'final_decision')
        super().save(*args, **kwargs)
