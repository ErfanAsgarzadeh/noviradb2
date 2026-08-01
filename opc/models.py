import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from ktcPlanning.validators import validate_chat_file


def controlled_document_upload_to(instance, filename):
    document_number = instance.document.document_number if instance.document_id else 'unassigned'
    revision = instance.revision or 'draft'
    return f'controlled_documents/{document_number}/{revision}/{filename}'


class Plant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']
        indexes = [models.Index(fields=['active', 'code'])]

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        if not self.code:
            raise ValidationError({'code': 'Plant code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.code} - {self.name}'


class WorkCenter(models.Model):
    CAPACITY_GENERAL = 'GENERAL'
    CAPACITY_MANUAL = 'MANUAL'
    CAPACITY_MACHINE = 'MACHINE'
    CAPACITY_OUTSOURCE = 'OUTSOURCE'
    CAPACITY_CHOICES = [
        (CAPACITY_GENERAL, 'General'),
        (CAPACITY_MANUAL, 'Manual'),
        (CAPACITY_MACHINE, 'Machine'),
        (CAPACITY_OUTSOURCE, 'Outsource coordination'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plant = models.ForeignKey(Plant, on_delete=models.PROTECT, related_name='work_centers')
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    capacity_classification = models.CharField(max_length=20, choices=CAPACITY_CHOICES, default=CAPACITY_GENERAL)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['plant__code', 'code']
        unique_together = [('plant', 'code')]
        indexes = [models.Index(fields=['plant', 'active']), models.Index(fields=['code'])]

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        if not self.code:
            raise ValidationError({'code': 'Work center code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.plant.code} / {self.code} - {self.name}'


class MachineAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plant = models.ForeignKey(Plant, on_delete=models.PROTECT, related_name='machine_assets')
    work_center = models.ForeignKey(WorkCenter, on_delete=models.PROTECT, related_name='machine_assets')
    asset_code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    machine_type = models.CharField(max_length=120, blank=True)
    manufacturer = models.CharField(max_length=120, blank=True)
    model = models.CharField(max_length=120, blank=True)
    serial_number = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['asset_code']
        indexes = [models.Index(fields=['plant', 'work_center', 'active'])]

    def clean(self):
        super().clean()
        self.asset_code = (self.asset_code or '').strip().upper()
        if not self.asset_code:
            raise ValidationError({'asset_code': 'Machine asset code is required.'})
        if self.work_center_id and self.plant_id and self.work_center.plant_id != self.plant_id:
            raise ValidationError({'work_center': 'Machine work center must belong to the selected plant.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.asset_code} - {self.name}'


class ProcessDefinition(models.Model):
    EXECUTION_INTERNAL = 'INTERNAL'
    EXECUTION_EXTERNAL = 'EXTERNAL'
    EXECUTION_FLEXIBLE = 'FLEXIBLE'
    EXECUTION_CHOICES = [
        (EXECUTION_INTERNAL, 'Internal'),
        (EXECUTION_EXTERNAL, 'External'),
        (EXECUTION_FLEXIBLE, 'Internal or external'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    process_code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    execution_classification = models.CharField(max_length=20, choices=EXECUTION_CHOICES, default=EXECUTION_INTERNAL)
    default_setup_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    default_run_time_per_unit_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    special_process = models.BooleanField(default=False)
    inspection_required = models.BooleanField(default=False)
    preferred_work_center_category = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['process_code']
        indexes = [models.Index(fields=['active', 'execution_classification'])]

    def clean(self):
        super().clean()
        self.process_code = (self.process_code or '').strip().upper()
        if not self.process_code:
            raise ValidationError({'process_code': 'Process code is required.'})
        for field in ('default_setup_time_hours', 'default_run_time_per_unit_hours'):
            value = getattr(self, field)
            if value is not None and value < 0:
                raise ValidationError({field: 'Default timing values cannot be negative.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.process_code} - {self.name}'


class ToolingDefinition(models.Model):
    CATEGORY_FIXTURE = 'FIXTURE'
    CATEGORY_GAUGE = 'GAUGE'
    CATEGORY_CUTTER = 'CUTTER'
    CATEGORY_DIE = 'DIE'
    CATEGORY_OTHER = 'OTHER'
    CATEGORY_CHOICES = [
        (CATEGORY_FIXTURE, 'Fixture'),
        (CATEGORY_GAUGE, 'Gauge'),
        (CATEGORY_CUTTER, 'Cutter'),
        (CATEGORY_DIE, 'Die'),
        (CATEGORY_OTHER, 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tooling_code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=CATEGORY_FIXTURE)
    reusable = models.BooleanField(default=True)
    consumable = models.BooleanField(default=False)
    calibration_required = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['tooling_code']
        indexes = [models.Index(fields=['active', 'category'])]

    def clean(self):
        super().clean()
        self.tooling_code = (self.tooling_code or '').strip().upper()
        if not self.tooling_code:
            raise ValidationError({'tooling_code': 'Tooling code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.tooling_code} - {self.name}'


class MaintainableAsset(models.Model):
    TYPE_AREA = 'AREA'
    TYPE_SYSTEM = 'SYSTEM'
    TYPE_SUBASSEMBLY = 'SUBASSEMBLY'
    TYPE_TOOLING = 'TOOLING'
    TYPE_FACILITY = 'FACILITY'
    TYPE_OTHER = 'OTHER'
    TYPE_CHOICES = [
        (TYPE_AREA, 'Area'),
        (TYPE_SYSTEM, 'System'),
        (TYPE_SUBASSEMBLY, 'Subassembly'),
        (TYPE_TOOLING, 'Tooling'),
        (TYPE_FACILITY, 'Facility'),
        (TYPE_OTHER, 'Other'),
    ]
    CRITICALITY_LOW = 'LOW'
    CRITICALITY_MEDIUM = 'MEDIUM'
    CRITICALITY_HIGH = 'HIGH'
    CRITICALITY_CRITICAL = 'CRITICAL'
    CRITICALITY_CHOICES = [
        (CRITICALITY_LOW, 'Low'),
        (CRITICALITY_MEDIUM, 'Medium'),
        (CRITICALITY_HIGH, 'High'),
        (CRITICALITY_CRITICAL, 'Critical'),
    ]
    STATUS_AVAILABLE = 'AVAILABLE'
    STATUS_RUNNING = 'RUNNING'
    STATUS_IDLE = 'IDLE'
    STATUS_PLANNED_DOWN = 'PLANNED_DOWN'
    STATUS_BREAKDOWN = 'BREAKDOWN'
    STATUS_MAINTENANCE = 'MAINTENANCE'
    STATUS_RETIRED = 'RETIRED'
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, 'Available'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_IDLE, 'Idle'),
        (STATUS_PLANNED_DOWN, 'Planned down'),
        (STATUS_BREAKDOWN, 'Breakdown'),
        (STATUS_MAINTENANCE, 'Maintenance'),
        (STATUS_RETIRED, 'Retired'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset_code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    asset_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_OTHER)
    plant = models.ForeignKey(Plant, null=True, blank=True, on_delete=models.PROTECT, related_name='maintainable_assets')
    work_center = models.ForeignKey(WorkCenter, null=True, blank=True, on_delete=models.PROTECT, related_name='maintainable_assets')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    location_code = models.CharField(max_length=80, blank=True)
    classification = models.CharField(max_length=120, blank=True)
    criticality = models.CharField(max_length=20, choices=CRITICALITY_CHOICES, default=CRITICALITY_MEDIUM)
    operational_status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['asset_code']
        indexes = [models.Index(fields=['plant', 'work_center', 'active']), models.Index(fields=['parent', 'active'])]

    def clean(self):
        super().clean()
        self.asset_code = (self.asset_code or '').strip().upper()
        if not self.asset_code:
            raise ValidationError({'asset_code': 'Asset code is required.'})
        if self.parent_id == self.pk and self.pk:
            raise ValidationError({'parent': 'Asset cannot be its own parent.'})
        if self.work_center_id and self.plant_id and self.work_center.plant_id != self.plant_id:
            raise ValidationError({'work_center': 'Work center belongs to a different plant.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.asset_code} - {self.name}'


class MachineMaintenanceProfile(models.Model):
    CRITICALITY_CHOICES = MaintainableAsset.CRITICALITY_CHOICES
    STATUS_CHOICES = MaintainableAsset.STATUS_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    machine = models.OneToOneField(MachineAsset, on_delete=models.PROTECT, related_name='maintenance_profile')
    parent_asset = models.ForeignKey(MaintainableAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='machine_profiles')
    classification = models.CharField(max_length=120, blank=True)
    criticality = models.CharField(max_length=20, choices=CRITICALITY_CHOICES, default=MaintainableAsset.CRITICALITY_MEDIUM)
    operational_status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=MaintainableAsset.STATUS_AVAILABLE)
    maintenance_location = models.CharField(max_length=120, blank=True)
    risk_notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    profile_version = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['machine__asset_code']
        indexes = [models.Index(fields=['criticality', 'operational_status']), models.Index(fields=['active'])]

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class AssetMeter(models.Model):
    TYPE_RUNTIME_HOURS = 'RUNTIME_HOURS'
    TYPE_CYCLES = 'CYCLES'
    TYPE_DISTANCE = 'DISTANCE'
    TYPE_ENERGY = 'ENERGY'
    TYPE_CUSTOM = 'CUSTOM'
    TYPE_CHOICES = [
        (TYPE_RUNTIME_HOURS, 'Runtime hours'),
        (TYPE_CYCLES, 'Cycles'),
        (TYPE_DISTANCE, 'Distance'),
        (TYPE_ENERGY, 'Energy'),
        (TYPE_CUSTOM, 'Custom'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='meters')
    maintainable_asset = models.ForeignKey(MaintainableAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='meters')
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=255)
    meter_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_RUNTIME_HOURS)
    unit = models.CharField(max_length=30, default='H')
    rollover_value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']
        unique_together = [('machine', 'code'), ('maintainable_asset', 'code')]

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        if not self.machine_id and not self.maintainable_asset_id:
            raise ValidationError({'asset': 'Meter requires a machine or maintainable asset.'})
        if self.machine_id and self.maintainable_asset_id:
            raise ValidationError({'asset': 'Meter can reference either a machine or a maintainable asset, not both.'})
        if not self.code:
            raise ValidationError({'code': 'Meter code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class AssetMeterReading(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meter = models.ForeignKey(AssetMeter, on_delete=models.PROTECT, related_name='readings')
    reading_value = models.DecimalField(max_digits=18, decimal_places=6)
    reading_timestamp = models.DateTimeField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='asset_meter_readings')
    source = models.CharField(max_length=80, default='MANUAL')
    idempotency_key = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-reading_timestamp', '-created_at']
        unique_together = [('meter', 'idempotency_key')]
        indexes = [models.Index(fields=['meter', 'reading_timestamp'])]

    def clean(self):
        super().clean()
        if self.reading_value < 0:
            raise ValidationError({'reading_value': 'Meter reading cannot be negative.'})

    def save(self, *args, **kwargs):
        if not self.idempotency_key:
            self.idempotency_key = f'auto:{uuid.uuid4()}'
        if self.pk and AssetMeterReading.objects.filter(pk=self.pk).exists():
            raise ValidationError({'meter_reading': 'Meter readings are append-only and immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'meter_reading': 'Meter readings cannot be deleted.'})


class FailureCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def save(self, *args, **kwargs):
        self.code = (self.code or '').strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)


class CauseCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def save(self, *args, **kwargs):
        self.code = (self.code or '').strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)


class RemedyCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def save(self, *args, **kwargs):
        self.code = (self.code or '').strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)


class MaintenanceTaskTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    estimated_duration_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    required_skill = models.ForeignKey('LaborSkill', null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_task_templates')
    safety_notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']

    def save(self, *args, **kwargs):
        self.code = (self.code or '').strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)


class MaintenanceTaskChecklistItem(models.Model):
    template = models.ForeignKey(MaintenanceTaskTemplate, on_delete=models.CASCADE, related_name='checklist_items')
    sequence = models.PositiveIntegerField(default=1)
    label = models.CharField(max_length=255)
    mandatory = models.BooleanField(default=True)
    expected_result = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['sequence', 'id']
        unique_together = [('template', 'sequence')]


class PreventiveMaintenancePlan(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_ACTIVE = 'ACTIVE'
    STATUS_PAUSED = 'PAUSED'
    STATUS_RETIRED = 'RETIRED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_ACTIVE, 'Active'), (STATUS_PAUSED, 'Paused'), (STATUS_RETIRED, 'Retired')]
    TRIGGER_CALENDAR = 'CALENDAR'
    TRIGGER_METER = 'METER'
    TRIGGER_THRESHOLD = 'THRESHOLD'
    TRIGGER_CHOICES = [(TRIGGER_CALENDAR, 'Calendar'), (TRIGGER_METER, 'Meter'), (TRIGGER_THRESHOLD, 'Threshold')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan_number = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='pm_plans')
    maintainable_asset = models.ForeignKey(MaintainableAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='pm_plans')
    task_template = models.ForeignKey(MaintenanceTaskTemplate, on_delete=models.PROTECT, related_name='pm_plans')
    trigger_type = models.CharField(max_length=30, choices=TRIGGER_CHOICES, default=TRIGGER_CALENDAR)
    interval_days = models.PositiveIntegerField(null=True, blank=True)
    meter = models.ForeignKey(AssetMeter, null=True, blank=True, on_delete=models.PROTECT, related_name='pm_plans')
    meter_interval = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    threshold_value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    next_due_date = models.DateField(null=True, blank=True)
    next_due_meter_value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    generate_horizon_days = models.PositiveIntegerField(default=14)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    plan_version = models.PositiveBigIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='created_pm_plans')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['plan_number']
        indexes = [models.Index(fields=['status', 'next_due_date']), models.Index(fields=['machine', 'status'])]

    def clean(self):
        super().clean()
        if not self.machine_id and not self.maintainable_asset_id:
            raise ValidationError({'asset': 'PM plan requires a machine or maintainable asset.'})
        if self.machine_id and self.maintainable_asset_id:
            raise ValidationError({'asset': 'PM plan can reference either a machine or maintainable asset, not both.'})
        if self.trigger_type == self.TRIGGER_CALENDAR and not self.interval_days:
            raise ValidationError({'interval_days': 'Calendar PM requires an interval in days.'})
        if self.trigger_type in {self.TRIGGER_METER, self.TRIGGER_THRESHOLD} and not self.meter_id:
            raise ValidationError({'meter': 'Meter or threshold PM requires a meter.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MaintenanceRequest(models.Model):
    STATUS_OPEN = 'OPEN'
    STATUS_TRIAGED = 'TRIAGED'
    STATUS_CONVERTED = 'CONVERTED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_OPEN, 'Open'), (STATUS_TRIAGED, 'Triaged'), (STATUS_CONVERTED, 'Converted'), (STATUS_REJECTED, 'Rejected'), (STATUS_CANCELLED, 'Cancelled')]
    PRIORITY_LOW = 'LOW'
    PRIORITY_NORMAL = 'NORMAL'
    PRIORITY_HIGH = 'HIGH'
    PRIORITY_URGENT = 'URGENT'
    PRIORITY_CHOICES = [(PRIORITY_LOW, 'Low'), (PRIORITY_NORMAL, 'Normal'), (PRIORITY_HIGH, 'High'), (PRIORITY_URGENT, 'Urgent')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_number = models.CharField(max_length=80, unique=True)
    machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_requests')
    maintainable_asset = models.ForeignKey(MaintainableAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_requests')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    failure_code = models.ForeignKey(FailureCode, null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_requests')
    production_order = models.ForeignKey('ProductionOrder', null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_requests')
    operation_execution = models.ForeignKey('OperationExecution', null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_requests')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    request_version = models.PositiveBigIntegerField(default=0)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='maintenance_requests')
    requested_at = models.DateTimeField(auto_now_add=True)
    converted_work_order = models.ForeignKey('MaintenanceWorkOrder', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-requested_at', 'request_number']

    def clean(self):
        super().clean()
        if not self.machine_id and not self.maintainable_asset_id:
            raise ValidationError({'asset': 'Maintenance request requires a machine or maintainable asset.'})
        if self.machine_id and self.maintainable_asset_id:
            raise ValidationError({'asset': 'Maintenance request can reference either a machine or maintainable asset, not both.'})


class MaintenanceWorkOrder(models.Model):
    TYPE_PREVENTIVE = 'PREVENTIVE'
    TYPE_CORRECTIVE = 'CORRECTIVE'
    TYPE_BREAKDOWN = 'BREAKDOWN'
    TYPE_INSPECTION = 'INSPECTION'
    TYPE_CHOICES = [(TYPE_PREVENTIVE, 'Preventive'), (TYPE_CORRECTIVE, 'Corrective'), (TYPE_BREAKDOWN, 'Breakdown'), (TYPE_INSPECTION, 'Inspection')]
    STATUS_DRAFT = 'DRAFT'
    STATUS_PLANNED = 'PLANNED'
    STATUS_RELEASED = 'RELEASED'
    STATUS_IN_PROGRESS = 'IN_PROGRESS'
    STATUS_PAUSED = 'PAUSED'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_CLOSED = 'CLOSED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PLANNED, 'Planned'),
        (STATUS_RELEASED, 'Released'),
        (STATUS_IN_PROGRESS, 'In progress'),
        (STATUS_PAUSED, 'Paused'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_CLOSED, 'Closed'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order_number = models.CharField(max_length=80, unique=True)
    work_order_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_CORRECTIVE)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_work_orders')
    maintainable_asset = models.ForeignKey(MaintainableAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_work_orders')
    request = models.ForeignKey(MaintenanceRequest, null=True, blank=True, on_delete=models.PROTECT, related_name='work_orders')
    preventive_plan = models.ForeignKey(PreventiveMaintenancePlan, null=True, blank=True, on_delete=models.PROTECT, related_name='work_orders')
    task_template = models.ForeignKey(MaintenanceTaskTemplate, null=True, blank=True, on_delete=models.PROTECT, related_name='work_orders')
    priority = models.CharField(max_length=20, choices=MaintenanceRequest.PRIORITY_CHOICES, default=MaintenanceRequest.PRIORITY_NORMAL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    planned_start = models.DateTimeField(null=True, blank=True)
    planned_end = models.DateTimeField(null=True, blank=True)
    estimated_duration_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)
    failure_code = models.ForeignKey(FailureCode, null=True, blank=True, on_delete=models.PROTECT, related_name='work_orders')
    cause_code = models.ForeignKey(CauseCode, null=True, blank=True, on_delete=models.PROTECT, related_name='work_orders')
    remedy_code = models.ForeignKey(RemedyCode, null=True, blank=True, on_delete=models.PROTECT, related_name='work_orders')
    downtime_required = models.BooleanField(default=True)
    downtime_exception = models.ForeignKey('CalendarException', null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_work_orders')
    work_order_version = models.PositiveBigIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='created_maintenance_work_orders')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='assigned_maintenance_work_orders')
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='completed_maintenance_work_orders')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', 'work_order_number']
        indexes = [models.Index(fields=['status', 'priority']), models.Index(fields=['machine', 'status']), models.Index(fields=['planned_start', 'planned_end'])]

    def clean(self):
        super().clean()
        if not self.machine_id and not self.maintainable_asset_id:
            raise ValidationError({'asset': 'Maintenance work order requires a machine or maintainable asset.'})
        if self.machine_id and self.maintainable_asset_id:
            raise ValidationError({'asset': 'Maintenance work order can reference either a machine or maintainable asset, not both.'})
        if self.planned_start and self.planned_end and self.planned_end <= self.planned_start:
            raise ValidationError({'planned_end': 'Planned end must be after planned start.'})
        if self.actual_start and self.actual_end and self.actual_end < self.actual_start:
            raise ValidationError({'actual_end': 'Actual end cannot be before actual start.'})


class MaintenanceWorkOrderSkillRequirement(models.Model):
    work_order = models.ForeignKey(MaintenanceWorkOrder, on_delete=models.CASCADE, related_name='skill_requirements')
    skill = models.ForeignKey('LaborSkill', on_delete=models.PROTECT, related_name='maintenance_work_order_requirements')
    required_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    required_count = models.PositiveIntegerField(default=1)


class MaintenanceChecklistResult(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_PASS = 'PASS'
    STATUS_FAIL = 'FAIL'
    STATUS_NA = 'NA'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_PASS, 'Pass'), (STATUS_FAIL, 'Fail'), (STATUS_NA, 'N/A')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(MaintenanceWorkOrder, on_delete=models.CASCADE, related_name='checklist_results')
    template_item = models.ForeignKey(MaintenanceTaskChecklistItem, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    sequence = models.PositiveIntegerField(default=1)
    label = models.CharField(max_length=255)
    mandatory = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_checklist_results')
    recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['sequence', 'id']
        unique_together = [('work_order', 'sequence')]


class MaintenanceSparePartRequirement(models.Model):
    STATUS_PLANNED = 'PLANNED'
    STATUS_RESERVED = 'RESERVED'
    STATUS_ISSUED = 'ISSUED'
    STATUS_RETURNED = 'RETURNED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_PLANNED, 'Planned'), (STATUS_RESERVED, 'Reserved'), (STATUS_ISSUED, 'Issued'), (STATUS_RETURNED, 'Returned'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(MaintenanceWorkOrder, on_delete=models.CASCADE, related_name='spare_part_requirements')
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='maintenance_spare_requirements')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30, default='EA')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PLANNED)
    issued_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    returned_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    requirement_version = models.PositiveBigIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['work_order__work_order_number', 'item_revision__item__item_code']


class MaintenanceSparePartIssue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(MaintenanceWorkOrder, on_delete=models.PROTECT, related_name='spare_part_issues')
    requirement = models.ForeignKey(MaintenanceSparePartRequirement, on_delete=models.PROTECT, related_name='issues')
    issue_transaction = models.ForeignKey('InventoryTransaction', on_delete=models.PROTECT, related_name='maintenance_spare_issues')
    return_transaction = models.ForeignKey('InventoryTransaction', null=True, blank=True, on_delete=models.PROTECT, related_name='maintenance_spare_returns')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    returned_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='maintenance_spare_part_issues')
    issued_at = models.DateTimeField(auto_now_add=True)


class MaintenanceEvent(models.Model):
    TYPE_CREATED = 'CREATED'
    TYPE_PLANNED = 'PLANNED'
    TYPE_RELEASED = 'RELEASED'
    TYPE_STARTED = 'STARTED'
    TYPE_PAUSED = 'PAUSED'
    TYPE_RESUMED = 'RESUMED'
    TYPE_COMPLETED = 'COMPLETED'
    TYPE_CLOSED = 'CLOSED'
    TYPE_CANCELLED = 'CANCELLED'
    TYPE_CHECKLIST = 'CHECKLIST'
    TYPE_PART_ISSUED = 'PART_ISSUED'
    TYPE_PART_RETURNED = 'PART_RETURNED'
    TYPE_METER_READING = 'METER_READING'
    TYPE_CHOICES = [(value, value.title()) for value in [TYPE_CREATED, TYPE_PLANNED, TYPE_RELEASED, TYPE_STARTED, TYPE_PAUSED, TYPE_RESUMED, TYPE_COMPLETED, TYPE_CLOSED, TYPE_CANCELLED, TYPE_CHECKLIST, TYPE_PART_ISSUED, TYPE_PART_RETURNED, TYPE_METER_READING]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(MaintenanceWorkOrder, on_delete=models.PROTECT, related_name='events')
    event_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    event_timestamp = models.DateTimeField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='maintenance_events')
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30, blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['event_timestamp', 'created_at']
        unique_together = [('work_order', 'event_type', 'idempotency_key')]

    def save(self, *args, **kwargs):
        if not self.idempotency_key:
            self.idempotency_key = f'auto:{uuid.uuid4()}'
        if self.pk and MaintenanceEvent.objects.filter(pk=self.pk).exists():
            raise ValidationError({'event': 'Maintenance events are append-only and immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'event': 'Maintenance events cannot be deleted.'})


class MachineDowntimeEvent(models.Model):
    TYPE_PLANNED = 'PLANNED'
    TYPE_UNPLANNED = 'UNPLANNED'
    TYPE_BREAKDOWN = 'BREAKDOWN'
    TYPE_CHOICES = [(TYPE_PLANNED, 'Planned'), (TYPE_UNPLANNED, 'Unplanned'), (TYPE_BREAKDOWN, 'Breakdown')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    machine = models.ForeignKey(MachineAsset, on_delete=models.PROTECT, related_name='downtime_events')
    work_order = models.ForeignKey(MaintenanceWorkOrder, null=True, blank=True, on_delete=models.PROTECT, related_name='downtime_events')
    downtime_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField(null=True, blank=True)
    planned = models.BooleanField(default=False)
    failure_code = models.ForeignKey(FailureCode, null=True, blank=True, on_delete=models.PROTECT, related_name='downtime_events')
    cause_code = models.ForeignKey(CauseCode, null=True, blank=True, on_delete=models.PROTECT, related_name='downtime_events')
    calendar_exception = models.ForeignKey('CalendarException', null=True, blank=True, on_delete=models.PROTECT, related_name='downtime_events')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_datetime']
        indexes = [models.Index(fields=['machine', 'start_datetime', 'end_datetime']), models.Index(fields=['planned', 'downtime_type'])]

    def clean(self):
        super().clean()
        if self.end_datetime and self.end_datetime <= self.start_datetime:
            raise ValidationError({'end_datetime': 'Downtime end must be after start.'})


class OPCDiagram(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_UNDER_REVIEW = 'UNDER_REVIEW'
    STATUS_APPROVED = 'APPROVED'
    STATUS_RELEASED = 'RELEASED'
    STATUS_SUPERSEDED = 'SUPERSEDED'
    STATUS_OBSOLETE = 'OBSOLETE'
    STATUS_ACTIVE_LEGACY = 'ACTIVE'
    STATUS_ARCHIVED_LEGACY = 'ARCHIVED'
    LOCKED_STATUSES = {STATUS_RELEASED, STATUS_SUPERSEDED, STATUS_OBSOLETE}
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_UNDER_REVIEW, 'Under review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_RELEASED, 'Released'),
        (STATUS_SUPERSEDED, 'Superseded'),
        (STATUS_OBSOLETE, 'Obsolete'),
        (STATUS_ACTIVE_LEGACY, 'Active (legacy)'),
        (STATUS_ARCHIVED_LEGACY, 'Archived (legacy)'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', null=True, blank=True, on_delete=models.PROTECT, related_name='opc_diagrams')
    manufacturing_bom_revision = models.ForeignKey('enterprise_items.BOMRevision', null=True, blank=True, on_delete=models.PROTECT, related_name='opc_diagrams')
    title = models.CharField(max_length=255)
    part_code = models.CharField(max_length=120, db_index=True)
    part_name = models.CharField(max_length=255, blank=True)
    revision = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='submitted_opc_diagrams')
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_opc_diagrams')
    approved_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='released_opc_diagrams')
    released_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='supersedes')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_opc_diagrams',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='updated_opc_diagrams',
    )
    graph_version = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        unique_together = [('item_revision', 'revision')]
        indexes = [
            models.Index(fields=['part_code', 'status']),
            models.Index(fields=['item_revision', 'status']),
            models.Index(fields=['updated_at']),
        ]

    def clean(self):
        super().clean()
        if self.manufacturing_bom_revision_id:
            if self.manufacturing_bom_revision.bom.bom_type != 'MANUFACTURING':
                raise ValidationError({'manufacturing_bom_revision': 'OPC may reference only a manufacturing BOM revision.'})
            if self.item_revision_id and self.manufacturing_bom_revision.bom.parent_item_revision_id != self.item_revision_id:
                raise ValidationError({'manufacturing_bom_revision': 'Manufacturing BOM must belong to the OPC ItemRevision.'})
        if self.revision:
            self.revision = self.revision.strip()
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective end date cannot precede effective start date.'})
        if self.item_revision_id and self.revision:
            queryset = OPCDiagram.objects.filter(item_revision_id=self.item_revision_id, revision__iexact=self.revision)
            if self.pk:
                queryset = queryset.exclude(pk=self.pk)
            if queryset.exists():
                raise ValidationError({'revision': 'OPC revision code must be unique for this item revision.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.part_code} - {self.title}'


class OPCNode(models.Model):
    EXECUTION_TYPES = [
        ('INTERNAL', 'Internal'),
        ('EXTERNAL', 'External'),
        ('INSPECTION', 'Inspection'),
        ('TRANSPORT', 'Transport'),
        ('MIXED', 'Mixed'),
    ]
    NODE_TYPES = [
        ('MATERIAL', 'Raw material'),
        ('OPERATION', 'Operation'),
        ('INSPECTION', 'Inspection'),
        ('STORAGE', 'Storage'),
        ('TRANSPORT', 'Transport'),
        ('DELAY', 'Delay'),
        ('OUTPUT', 'Final part'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    diagram = models.ForeignKey(OPCDiagram, on_delete=models.CASCADE, related_name='nodes')
    node_type = models.CharField(max_length=24, choices=NODE_TYPES)
    label = models.CharField(max_length=255)
    part_code = models.CharField(max_length=120, blank=True)
    process_code = models.CharField(max_length=120, blank=True)
    station = models.CharField(max_length=120, blank=True)
    execution_type = models.CharField(max_length=20, choices=EXECUTION_TYPES, default='INTERNAL')
    operation_number = models.PositiveIntegerField(null=True, blank=True)
    process_definition = models.ForeignKey(ProcessDefinition, null=True, blank=True, on_delete=models.PROTECT, related_name='opc_nodes')
    plant = models.ForeignKey(Plant, null=True, blank=True, on_delete=models.PROTECT, related_name='opc_nodes')
    work_center = models.ForeignKey(WorkCenter, null=True, blank=True, on_delete=models.PROTECT, related_name='opc_nodes')
    machine_asset = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='opc_nodes')
    setup_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    run_time_per_unit_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    queue_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    move_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    inspection_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    external_lead_time_days = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    buffer_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    description = models.TextField(blank=True)
    sequence = models.PositiveIntegerField(default=1)
    x = models.FloatField(default=120)
    y = models.FloatField(default=120)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sequence', 'created_at']
        indexes = [
            models.Index(fields=['diagram', 'sequence']),
            models.Index(fields=['diagram', 'operation_number']),
            models.Index(fields=['node_type']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['diagram', 'operation_number'],
                condition=Q(operation_number__isnull=False),
                name='opc_unique_operation_number_per_diagram',
            ),
        ]

    def clean(self):
        super().clean()
        for field in ('setup_time_hours', 'run_time_per_unit_hours', 'queue_time_hours', 'move_time_hours', 'inspection_time_hours', 'external_lead_time_days', 'buffer_time_hours'):
            value = getattr(self, field)
            if value is not None and value < 0:
                raise ValidationError({field: 'Duration values cannot be negative.'})
        if self.node_type == 'INSPECTION':
            self.execution_type = 'INSPECTION'
        if self.node_type == 'TRANSPORT':
            self.execution_type = 'TRANSPORT'
        if self.operation_number is not None and self.operation_number <= 0:
            raise ValidationError({'operation_number': 'Operation number must be positive.'})
        if self.work_center_id and self.plant_id and self.work_center.plant_id != self.plant_id:
            raise ValidationError({'work_center': 'Work center must belong to the selected plant.'})
        if self.machine_asset_id:
            if not self.work_center_id:
                raise ValidationError({'machine_asset': 'Machine assignment requires a work center.'})
            if self.machine_asset.work_center_id != self.work_center_id:
                raise ValidationError({'machine_asset': 'Machine asset must belong to the selected work center.'})
            if self.plant_id and self.machine_asset.plant_id != self.plant_id:
                raise ValidationError({'machine_asset': 'Machine asset must belong to the selected plant.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.diagram.part_code} / {self.label}'


class OPCEdge(models.Model):
    EDGE_TYPES = [
        ('FLOW', 'Process flow'),
        ('OPTIONAL', 'Optional flow'),
        ('REWORK', 'Rework'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    diagram = models.ForeignKey(OPCDiagram, on_delete=models.CASCADE, related_name='edges')
    source = models.ForeignKey(OPCNode, on_delete=models.CASCADE, related_name='outgoing_edges')
    target = models.ForeignKey(OPCNode, on_delete=models.CASCADE, related_name='incoming_edges')
    edge_type = models.CharField(max_length=20, choices=EDGE_TYPES, default='FLOW')
    label = models.CharField(max_length=120, blank=True)
    sequence = models.PositiveIntegerField(default=1)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sequence', 'created_at']
        unique_together = [('diagram', 'source', 'target')]
        indexes = [
            models.Index(fields=['diagram', 'sequence']),
        ]

    def __str__(self):
        return f'{self.source_id} -> {self.target_id}'


class OPCToolingRequirement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node = models.ForeignKey(OPCNode, on_delete=models.CASCADE, related_name='tooling_requirements')
    tooling_definition = models.ForeignKey(ToolingDefinition, on_delete=models.PROTECT, related_name='opc_requirements')
    quantity = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    mandatory = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    sequence = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sequence', 'created_at']
        unique_together = [('node', 'tooling_definition')]
        indexes = [models.Index(fields=['node', 'sequence'])]

    def clean(self):
        super().clean()
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({'quantity': 'Tooling quantity must be positive.'})
        if self.node_id and self.node.node_type not in {'OPERATION', 'INSPECTION', 'TRANSPORT'}:
            raise ValidationError({'node': 'Tooling can only be assigned to operation-capable OPC nodes.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.node_id} / {self.tooling_definition.tooling_code}'


class OPCOperationMaterialAllocation(models.Model):
    TYPE_CONSUME = 'CONSUME'
    TYPE_ISSUE = 'ISSUE'
    TYPE_ASSEMBLE = 'ASSEMBLE'
    TYPE_SUPPLIED = 'SUPPLIED'
    TYPE_CONSUMABLE = 'CONSUMABLE'
    ALLOCATION_TYPE_CHOICES = [
        (TYPE_CONSUME, 'Consume'),
        (TYPE_ISSUE, 'Issue'),
        (TYPE_ASSEMBLE, 'Assemble'),
        (TYPE_SUPPLIED, 'Supplied'),
        (TYPE_CONSUMABLE, 'Consumable'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node = models.ForeignKey(OPCNode, on_delete=models.CASCADE, related_name='material_allocations')
    bom_line = models.ForeignKey('enterprise_items.BOMLine', null=True, blank=True, on_delete=models.PROTECT, related_name='opc_allocations')
    component_item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='opc_material_allocations')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30, default='EA')
    scrap_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    allocation_type = models.CharField(max_length=20, choices=ALLOCATION_TYPE_CHOICES, default=TYPE_CONSUME)
    issue_at_operation = models.BooleanField(default=True)
    backflush = models.BooleanField(default=False)
    sequence = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sequence', 'created_at']
        unique_together = [('node', 'bom_line', 'allocation_type')]
        indexes = [models.Index(fields=['node', 'sequence']), models.Index(fields=['bom_line']), models.Index(fields=['component_item_revision'])]

    def clean(self):
        super().clean()
        if self.node_id and self.node.node_type not in {'OPERATION', 'INSPECTION', 'TRANSPORT'}:
            raise ValidationError({'node': 'Material allocations can only be assigned to operation-capable OPC nodes.'})
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({'quantity': 'Allocation quantity must be greater than zero.'})
        if self.scrap_percent is not None and self.scrap_percent < 0:
            raise ValidationError({'scrap_percent': 'Scrap percentage cannot be negative.'})
        if self.unit:
            self.unit = self.unit.strip().upper()
        if self.bom_line_id:
            if self.bom_line.bom_revision.bom.bom_type != 'MANUFACTURING':
                raise ValidationError({'bom_line': 'Allocation must reference a manufacturing BOM line.'})
            if self.component_item_revision_id and self.component_item_revision_id != self.bom_line.component_item_revision_id:
                raise ValidationError({'component_item_revision': 'Component revision must match the selected BOM line.'})
            if self.node_id and self.node.diagram.item_revision_id and self.bom_line.bom_revision.bom.parent_item_revision_id != self.node.diagram.item_revision_id:
                raise ValidationError({'bom_line': 'BOM line must belong to the OPC ItemRevision context.'})
        if self.node_id and self.node.diagram.status in OPCDiagram.LOCKED_STATUSES:
            raise ValidationError({'node': 'Released OPC revisions are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.node_id} / {self.component_item_revision_id}'


class ControlledDocument(models.Model):
    TYPE_DRAWING = 'DRAWING'
    TYPE_SPECIFICATION = 'SPECIFICATION'
    TYPE_WORK_INSTRUCTION = 'WORK_INSTRUCTION'
    TYPE_INSPECTION_INSTRUCTION = 'INSPECTION_INSTRUCTION'
    TYPE_PROCESS_SHEET = 'PROCESS_SHEET'
    TYPE_SAFETY_INSTRUCTION = 'SAFETY_INSTRUCTION'
    TYPE_TOOLING_INSTRUCTION = 'TOOLING_INSTRUCTION'
    TYPE_CERTIFICATE_TEMPLATE = 'CERTIFICATE_TEMPLATE'
    TYPE_OTHER = 'OTHER'
    DOCUMENT_TYPE_CHOICES = [
        (TYPE_DRAWING, 'Drawing'),
        (TYPE_SPECIFICATION, 'Specification'),
        (TYPE_WORK_INSTRUCTION, 'Work instruction'),
        (TYPE_INSPECTION_INSTRUCTION, 'Inspection instruction'),
        (TYPE_PROCESS_SHEET, 'Process sheet'),
        (TYPE_SAFETY_INSTRUCTION, 'Safety instruction'),
        (TYPE_TOOLING_INSTRUCTION, 'Tooling instruction'),
        (TYPE_CERTIFICATE_TEMPLATE, 'Certificate template'),
        (TYPE_OTHER, 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_number = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=255)
    document_type = models.CharField(max_length=40, choices=DOCUMENT_TYPE_CHOICES, default=TYPE_OTHER)
    description = models.TextField(blank=True)
    owner_department = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_controlled_documents')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['document_number']
        indexes = [models.Index(fields=['document_type', 'active'])]

    def clean(self):
        super().clean()
        if self.document_number:
            self.document_number = self.document_number.strip().upper()
        if not self.document_number:
            raise ValidationError({'document_number': 'Document number is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.document_number} - {self.title}'


class ControlledDocumentRevision(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_UNDER_REVIEW = 'UNDER_REVIEW'
    STATUS_APPROVED = 'APPROVED'
    STATUS_RELEASED = 'RELEASED'
    STATUS_SUPERSEDED = 'SUPERSEDED'
    STATUS_OBSOLETE = 'OBSOLETE'
    LOCKED_STATUSES = {STATUS_RELEASED, STATUS_SUPERSEDED, STATUS_OBSOLETE}
    STATUS_CHOICES = OPCDiagram.STATUS_CHOICES[:6]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(ControlledDocument, on_delete=models.PROTECT, related_name='revisions')
    revision = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to=controlled_document_upload_to, validators=[validate_chat_file])
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    mime_type = models.CharField(max_length=120, blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    description = models.TextField(blank=True)
    change_summary = models.TextField(blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='released_controlled_document_revisions')
    released_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='supersedes')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_controlled_document_revisions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['document__document_number', '-created_at']
        unique_together = [('document', 'revision')]
        indexes = [models.Index(fields=['document', 'status']), models.Index(fields=['effective_from', 'effective_to'])]

    def clean(self):
        super().clean()
        if self.revision:
            self.revision = self.revision.strip().upper()
        if not self.revision:
            raise ValidationError({'revision': 'Document revision is required.'})
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective end date cannot precede effective start date.'})
        if self.pk:
            original = ControlledDocumentRevision.objects.filter(pk=self.pk).values('status', 'file', 'revision', 'document_id').first()
            if original and original['status'] in self.LOCKED_STATUSES:
                if original['file'] != self.file.name or original['revision'] != self.revision or original['document_id'] != self.document_id:
                    raise ValidationError({'status': 'Released document revisions are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        storage = self.file.storage if self.file else None
        name = self.file.name if self.file else None
        super().delete(*args, **kwargs)
        if storage and name and storage.exists(name):
            storage.delete(name)

    def __str__(self):
        return f'{self.document.document_number} / {self.revision}'


class OPCNodeDocumentRequirement(models.Model):
    PURPOSE_WORK_INSTRUCTION = 'WORK_INSTRUCTION'
    PURPOSE_DRAWING = 'DRAWING'
    PURPOSE_SPECIFICATION = 'SPECIFICATION'
    PURPOSE_INSPECTION = 'INSPECTION'
    PURPOSE_PROCESS_SHEET = 'PROCESS_SHEET'
    PURPOSE_SAFETY = 'SAFETY'
    PURPOSE_OTHER = 'OTHER'
    PURPOSE_CHOICES = [
        (PURPOSE_WORK_INSTRUCTION, 'Work instruction'),
        (PURPOSE_DRAWING, 'Drawing'),
        (PURPOSE_SPECIFICATION, 'Specification'),
        (PURPOSE_INSPECTION, 'Inspection'),
        (PURPOSE_PROCESS_SHEET, 'Process sheet'),
        (PURPOSE_SAFETY, 'Safety'),
        (PURPOSE_OTHER, 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node = models.ForeignKey(OPCNode, on_delete=models.CASCADE, related_name='document_requirements')
    document_revision = models.ForeignKey(ControlledDocumentRevision, on_delete=models.PROTECT, related_name='opc_node_requirements')
    purpose = models.CharField(max_length=40, choices=PURPOSE_CHOICES, default=PURPOSE_WORK_INSTRUCTION)
    mandatory = models.BooleanField(default=True)
    sequence = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sequence', 'created_at']
        unique_together = [('node', 'document_revision', 'purpose')]
        indexes = [models.Index(fields=['node', 'sequence']), models.Index(fields=['document_revision'])]

    def clean(self):
        super().clean()
        if self.node_id and self.node.node_type not in {'OPERATION', 'INSPECTION', 'TRANSPORT'}:
            raise ValidationError({'node': 'Documents can only be assigned to operation-capable OPC nodes.'})
        if self.document_revision_id and self.document_revision.status != ControlledDocumentRevision.STATUS_RELEASED:
            raise ValidationError({'document_revision': 'OPC node documents must reference released document revisions.'})
        if self.node_id and self.node.diagram.status in OPCDiagram.LOCKED_STATUSES:
            raise ValidationError({'node': 'Released OPC revisions are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.node_id} / {self.document_revision_id}'


class EngineeringChangeRequest(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_SUBMITTED = 'SUBMITTED'
    STATUS_ACCEPTED = 'ACCEPTED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_SUBMITTED, 'Submitted'), (STATUS_ACCEPTED, 'Accepted'), (STATUS_REJECTED, 'Rejected')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    change_number = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_engineering_change_requests')
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        if self.change_number:
            self.change_number = self.change_number.strip().upper()
        if not self.change_number:
            raise ValidationError({'change_number': 'Change request number is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.change_number


class EngineeringChangeOrder(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_UNDER_REVIEW = 'UNDER_REVIEW'
    STATUS_APPROVED = 'APPROVED'
    STATUS_IMPLEMENTED = 'IMPLEMENTED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_UNDER_REVIEW, 'Under review'), (STATUS_APPROVED, 'Approved'), (STATUS_IMPLEMENTED, 'Implemented'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    change_number = models.CharField(max_length=80, unique=True)
    request = models.ForeignKey(EngineeringChangeRequest, null=True, blank=True, on_delete=models.PROTECT, related_name='orders')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    effectivity_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_engineering_change_orders')
    approved_at = models.DateTimeField(null=True, blank=True)
    implemented_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        if self.change_number:
            self.change_number = self.change_number.strip().upper()
        if not self.change_number:
            raise ValidationError({'change_number': 'Change order number is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.change_number


class EngineeringChangeObjectLink(models.Model):
    OBJECT_ITEM_REVISION = 'ITEM_REVISION'
    OBJECT_BOM_REVISION = 'BOM_REVISION'
    OBJECT_OPC_DIAGRAM = 'OPC_DIAGRAM'
    OBJECT_DOCUMENT_REVISION = 'DOCUMENT_REVISION'
    OBJECT_CHOICES = [
        (OBJECT_ITEM_REVISION, 'Item revision'),
        (OBJECT_BOM_REVISION, 'BOM revision'),
        (OBJECT_OPC_DIAGRAM, 'OPC diagram'),
        (OBJECT_DOCUMENT_REVISION, 'Document revision'),
    ]
    ROLE_AFFECTED = 'AFFECTED'
    ROLE_REPLACEMENT = 'REPLACEMENT'
    ROLE_REFERENCE = 'REFERENCE'
    ROLE_CHOICES = [(ROLE_AFFECTED, 'Affected'), (ROLE_REPLACEMENT, 'Replacement'), (ROLE_REFERENCE, 'Reference')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(EngineeringChangeRequest, null=True, blank=True, on_delete=models.CASCADE, related_name='object_links')
    order = models.ForeignKey(EngineeringChangeOrder, null=True, blank=True, on_delete=models.CASCADE, related_name='object_links')
    object_type = models.CharField(max_length=40, choices=OBJECT_CHOICES)
    object_id = models.UUIDField()
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_AFFECTED)
    before_object_type = models.CharField(max_length=40, choices=OBJECT_CHOICES, blank=True)
    before_object_id = models.UUIDField(null=True, blank=True)
    after_object_type = models.CharField(max_length=40, choices=OBJECT_CHOICES, blank=True)
    after_object_id = models.UUIDField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['object_type', 'object_id', 'role']
        indexes = [models.Index(fields=['object_type', 'object_id']), models.Index(fields=['role'])]

    def clean(self):
        super().clean()
        if not self.request_id and not self.order_id:
            raise ValidationError({'request': 'Change link requires an ECR or ECO.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ProductionOrder(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_PLANNED = 'PLANNED'
    STATUS_RELEASED = 'RELEASED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PLANNED, 'Planned'),
        (STATUS_RELEASED, 'Released'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]
    LOCKED_STATUSES = {STATUS_RELEASED, STATUS_CANCELLED}

    PRIORITY_LOW = 'LOW'
    PRIORITY_NORMAL = 'NORMAL'
    PRIORITY_HIGH = 'HIGH'
    PRIORITY_URGENT = 'URGENT'
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, 'Low'),
        (PRIORITY_NORMAL, 'Normal'),
        (PRIORITY_HIGH, 'High'),
        (PRIORITY_URGENT, 'Urgent'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(max_length=80, unique=True, blank=True)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='production_orders')
    planned_quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30, default='EA')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    planned_start = models.DateTimeField(null=True, blank=True)
    planned_end = models.DateTimeField(null=True, blank=True)
    requested_completion_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    source_opc_diagram = models.ForeignKey(OPCDiagram, on_delete=models.PROTECT, related_name='production_orders')
    source_graph_version = models.PositiveBigIntegerField(default=0)
    source_validation_evidence = models.ForeignKey('OPCValidationEvidence', null=True, blank=True, on_delete=models.PROTECT, related_name='production_orders')
    source_manufacturing_bom_revision = models.ForeignKey('enterprise_items.BOMRevision', on_delete=models.PROTECT, related_name='production_orders')
    source_snapshot = models.JSONField(default=dict, blank=True)
    source_snapshot_checksum = models.CharField(max_length=64, blank=True)
    generator_policy_version = models.CharField(max_length=80, default='production-order-v1')
    generated_at = models.DateTimeField(null=True, blank=True)
    order_version = models.PositiveBigIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_production_orders')
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='released_production_orders')
    released_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='cancelled_production_orders')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'priority']),
            models.Index(fields=['item_revision', 'status']),
            models.Index(fields=['source_opc_diagram', 'source_graph_version']),
        ]

    def clean(self):
        super().clean()
        if self.order_number:
            self.order_number = self.order_number.strip().upper()
        self.unit = (self.unit or 'EA').strip().upper()
        if self.planned_quantity is not None and self.planned_quantity <= 0:
            raise ValidationError({'planned_quantity': 'Planned quantity must be positive.'})
        if self.planned_start and self.planned_end and self.planned_end < self.planned_start:
            raise ValidationError({'planned_end': 'Planned end cannot precede planned start.'})
        if self.source_opc_diagram_id and self.item_revision_id and self.source_opc_diagram.item_revision_id != self.item_revision_id:
            raise ValidationError({'source_opc_diagram': 'Source OPC must belong to the target ItemRevision.'})
        if self.source_opc_diagram_id and self.source_opc_diagram.status != OPCDiagram.STATUS_RELEASED:
            raise ValidationError({'source_opc_diagram': 'Production orders must reference a released OPC diagram.'})
        if self.source_manufacturing_bom_revision_id:
            mbom = self.source_manufacturing_bom_revision
            if mbom.status != 'RELEASED':
                raise ValidationError({'source_manufacturing_bom_revision': 'Production orders must reference a released MBOM revision.'})
            if mbom.bom.bom_type != 'MANUFACTURING':
                raise ValidationError({'source_manufacturing_bom_revision': 'Source BOM must be a manufacturing BOM.'})
            if self.item_revision_id and mbom.bom.parent_item_revision_id != self.item_revision_id:
                raise ValidationError({'source_manufacturing_bom_revision': 'Source MBOM must belong to the target ItemRevision.'})
        if self.pk:
            original = ProductionOrder.objects.filter(pk=self.pk).values(
                'status', 'item_revision_id', 'source_opc_diagram_id',
                'source_graph_version', 'source_manufacturing_bom_revision_id',
                'source_snapshot_checksum',
            ).first()
            if original and original['status'] in self.LOCKED_STATUSES:
                immutable_changed = (
                    original['item_revision_id'] != self.item_revision_id
                    or original['source_opc_diagram_id'] != self.source_opc_diagram_id
                    or original['source_graph_version'] != self.source_graph_version
                    or original['source_manufacturing_bom_revision_id'] != self.source_manufacturing_bom_revision_id
                    or original['source_snapshot_checksum'] != self.source_snapshot_checksum
                )
                if immutable_changed:
                    raise ValidationError({'status': 'Released production order baselines are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.order_number or str(self.pk)


class ManufacturingOperation(models.Model):
    STATUS_PLANNED = 'PLANNED'
    STATUS_RELEASED = 'RELEASED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_PLANNED, 'Planned'), (STATUS_RELEASED, 'Released'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='operations')
    source_opc_node = models.ForeignKey(OPCNode, on_delete=models.PROTECT, related_name='manufacturing_operations')
    source_operation_number = models.PositiveIntegerField(null=True, blank=True)
    operation_number = models.PositiveIntegerField(null=True, blank=True)
    sequence = models.PositiveIntegerField(default=1)
    node_type = models.CharField(max_length=20)
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    execution_type = models.CharField(max_length=20)
    process_definition = models.ForeignKey(ProcessDefinition, null=True, blank=True, on_delete=models.PROTECT, related_name='manufacturing_operations')
    process_code_snapshot = models.CharField(max_length=80, blank=True)
    plant = models.ForeignKey(Plant, null=True, blank=True, on_delete=models.PROTECT, related_name='manufacturing_operations')
    plant_code_snapshot = models.CharField(max_length=50, blank=True)
    work_center = models.ForeignKey(WorkCenter, null=True, blank=True, on_delete=models.PROTECT, related_name='manufacturing_operations')
    work_center_code_snapshot = models.CharField(max_length=50, blank=True)
    selected_machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='manufacturing_operations')
    machine_code_snapshot = models.CharField(max_length=80, blank=True)
    setup_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    run_time_per_unit_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    queue_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    move_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    inspection_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    external_lead_time_days = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    buffer_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    planned_quantity = models.DecimalField(max_digits=18, decimal_places=6)
    planned_start = models.DateTimeField(null=True, blank=True)
    planned_end = models.DateTimeField(null=True, blank=True)
    scheduled_machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='scheduled_manufacturing_operations')
    scheduling_dispatch_priority = models.PositiveIntegerField(default=100)
    applied_scheduling_run = models.ForeignKey('SchedulingRun', null=True, blank=True, on_delete=models.PROTECT, related_name='applied_operations')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PLANNED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['operation_number', 'sequence', 'source_opc_node_id']
        unique_together = [('production_order', 'source_opc_node'), ('production_order', 'operation_number')]
        indexes = [models.Index(fields=['production_order', 'sequence'])]

    def clean(self):
        super().clean()
        if self.production_order_id and self.production_order.status in ProductionOrder.LOCKED_STATUSES and self.pk:
            original = ManufacturingOperation.objects.filter(pk=self.pk).values('label', 'process_code_snapshot', 'work_center_id', 'selected_machine_id').first()
            if original and (
                original['label'] != self.label
                or original['process_code_snapshot'] != self.process_code_snapshot
                or original['work_center_id'] != self.work_center_id
                or original['selected_machine_id'] != self.selected_machine_id
            ):
                raise ValidationError({'production_order': 'Released production order operations are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ResourceCalendar(models.Model):
    TYPE_WORK_CENTER = 'WORK_CENTER'
    TYPE_MACHINE = 'MACHINE'
    TYPE_LABOR = 'LABOR'
    TYPE_GENERAL = 'GENERAL'
    TYPE_CHOICES = [(TYPE_WORK_CENTER, 'Work center'), (TYPE_MACHINE, 'Machine'), (TYPE_LABOR, 'Labor'), (TYPE_GENERAL, 'General')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    timezone = models.CharField(max_length=80, default='UTC')
    calendar_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_GENERAL)
    planning_calendar = models.ForeignKey('PlanningCalendar', null=True, blank=True, on_delete=models.PROTECT, related_name='resource_calendars')
    effective_start = models.DateField(null=True, blank=True)
    effective_end = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']
        indexes = [models.Index(fields=['calendar_type', 'active'])]

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        if not self.code:
            raise ValidationError({'code': 'Resource calendar code is required.'})
        if self.effective_start and self.effective_end and self.effective_end < self.effective_start:
            raise ValidationError({'effective_end': 'Effective end cannot precede start.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Shift(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource_calendar = models.ForeignKey(ResourceCalendar, on_delete=models.CASCADE, related_name='shifts')
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=255, blank=True)
    weekday = models.PositiveSmallIntegerField()
    local_start_time = models.TimeField()
    local_end_time = models.TimeField()
    capacity_factor = models.DecimalField(max_digits=8, decimal_places=4, default=1)
    effective_start = models.DateField(null=True, blank=True)
    effective_end = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    sequence = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['resource_calendar__code', 'weekday', 'sequence', 'local_start_time']
        unique_together = [('resource_calendar', 'code')]

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        if self.weekday > 6:
            raise ValidationError({'weekday': 'Weekday must be 0-6.'})
        if self.local_start_time == self.local_end_time:
            raise ValidationError({'local_end_time': 'Shift duration must be positive.'})
        if self.capacity_factor <= 0:
            raise ValidationError({'capacity_factor': 'Capacity factor must be positive.'})
        if self.effective_start and self.effective_end and self.effective_end < self.effective_start:
            raise ValidationError({'effective_end': 'Effective end cannot precede start.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        from .scheduling_services import validate_shift_overlap
        validate_shift_overlap(self)
        super().save(*args, **kwargs)


class CalendarException(models.Model):
    TYPE_HOLIDAY = 'HOLIDAY'
    TYPE_PLANNED_DOWNTIME = 'PLANNED_DOWNTIME'
    TYPE_OVERTIME = 'OVERTIME'
    TYPE_CAPACITY_REDUCTION = 'CAPACITY_REDUCTION'
    TYPE_CAPACITY_INCREASE = 'CAPACITY_INCREASE'
    TYPE_BLOCKED = 'BLOCKED'
    TYPE_CHOICES = [(TYPE_HOLIDAY, 'Holiday'), (TYPE_PLANNED_DOWNTIME, 'Planned downtime'), (TYPE_OVERTIME, 'Overtime'), (TYPE_CAPACITY_REDUCTION, 'Capacity reduction'), (TYPE_CAPACITY_INCREASE, 'Capacity increase'), (TYPE_BLOCKED, 'Blocked')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource_calendar = models.ForeignKey(ResourceCalendar, on_delete=models.CASCADE, related_name='exceptions')
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    exception_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    capacity_factor = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    reason = models.CharField(max_length=255, blank=True)
    source_reference = models.CharField(max_length=160, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['start_datetime']
        indexes = [models.Index(fields=['resource_calendar', 'start_datetime', 'end_datetime'])]

    def clean(self):
        super().clean()
        if self.end_datetime <= self.start_datetime:
            raise ValidationError({'end_datetime': 'Exception end must be after start.'})
        if self.capacity_factor < 0:
            raise ValidationError({'capacity_factor': 'Capacity factor cannot be negative.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class WorkCenterCapacity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_center = models.ForeignKey(WorkCenter, on_delete=models.PROTECT, related_name='capacity_policies')
    resource_calendar = models.ForeignKey(ResourceCalendar, on_delete=models.PROTECT, related_name='work_center_capacities')
    parallel_capacity_units = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    default_efficiency_factor = models.DecimalField(max_digits=8, decimal_places=4, default=1)
    queue_capacity_metadata = models.JSONField(default=dict, blank=True)
    effective_start = models.DateField(null=True, blank=True)
    effective_end = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['work_center__code', '-active']

    def clean(self):
        super().clean()
        if self.parallel_capacity_units <= 0:
            raise ValidationError({'parallel_capacity_units': 'Parallel capacity must be positive.'})
        if self.default_efficiency_factor <= 0:
            raise ValidationError({'default_efficiency_factor': 'Efficiency factor must be positive.'})
        if self.resource_calendar_id and self.resource_calendar.calendar_type not in {ResourceCalendar.TYPE_WORK_CENTER, ResourceCalendar.TYPE_GENERAL}:
            raise ValidationError({'resource_calendar': 'Work-center capacity requires a work-center or general calendar.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MachineCapacity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    machine = models.ForeignKey(MachineAsset, on_delete=models.PROTECT, related_name='capacity_policies')
    resource_calendar = models.ForeignKey(ResourceCalendar, on_delete=models.PROTECT, related_name='machine_capacities')
    exclusive_capacity = models.BooleanField(default=True)
    efficiency_factor = models.DecimalField(max_digits=8, decimal_places=4, default=1)
    setup_family = models.CharField(max_length=120, blank=True)
    effective_start = models.DateField(null=True, blank=True)
    effective_end = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['machine__asset_code', '-active']

    def clean(self):
        super().clean()
        if self.efficiency_factor <= 0:
            raise ValidationError({'efficiency_factor': 'Efficiency factor must be positive.'})
        if self.resource_calendar_id and self.resource_calendar.calendar_type not in {ResourceCalendar.TYPE_MACHINE, ResourceCalendar.TYPE_GENERAL}:
            raise ValidationError({'resource_calendar': 'Machine capacity requires a machine or general calendar.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class LaborSkill(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    active = models.BooleanField(default=True)

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class WorkCenterLaborCapacity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_center = models.ForeignKey(WorkCenter, on_delete=models.PROTECT, related_name='labor_capacities')
    labor_skill = models.ForeignKey(LaborSkill, on_delete=models.PROTECT, related_name='work_center_capacities')
    resource_calendar = models.ForeignKey(ResourceCalendar, on_delete=models.PROTECT, related_name='labor_capacities')
    available_units = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    active = models.BooleanField(default=True)

    def clean(self):
        super().clean()
        if self.available_units <= 0:
            raise ValidationError({'available_units': 'Labor capacity must be positive.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SchedulingPolicy(models.Model):
    DIRECTION_FORWARD = 'FORWARD'
    DIRECTION_BACKWARD = 'BACKWARD'
    DIRECTION_CHOICES = [(DIRECTION_FORWARD, 'Forward'), (DIRECTION_BACKWARD, 'Backward')]
    RULE_PRIORITY_DUE = 'PRIORITY_THEN_DUE_DATE'
    RULE_EARLIEST_DUE = 'EARLIEST_DUE_DATE'
    RULE_SHORTEST = 'SHORTEST_PROCESSING_TIME'
    RULE_LONGEST = 'LONGEST_PROCESSING_TIME'
    RULE_FIFO = 'FIFO'
    RULE_CRITICAL_RATIO = 'CRITICAL_RATIO'
    RULE_CHOICES = [(RULE_PRIORITY_DUE, 'Priority then due date'), (RULE_EARLIEST_DUE, 'Earliest due date'), (RULE_SHORTEST, 'Shortest processing time'), (RULE_LONGEST, 'Longest processing time'), (RULE_FIFO, 'FIFO'), (RULE_CRITICAL_RATIO, 'Critical ratio')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES, default=DIRECTION_FORWARD)
    dispatch_rule = models.CharField(max_length=40, choices=RULE_CHOICES, default=RULE_PRIORITY_DUE)
    allow_overtime = models.BooleanField(default=False)
    allow_overload = models.BooleanField(default=False)
    freeze_fence_days = models.PositiveIntegerField(default=0)
    default_queue_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    default_move_time_hours = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    lateness_weight = models.DecimalField(max_digits=8, decimal_places=4, default=1)
    priority_weight = models.DecimalField(max_digits=8, decimal_places=4, default=1)
    policy_version = models.CharField(max_length=80, default='finite-scheduling-v1')
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        for field in ('default_queue_time_hours', 'default_move_time_hours'):
            if getattr(self, field) < 0:
                raise ValidationError({field: 'Default time cannot be negative.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SchedulingRun(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_RUNNING = 'RUNNING'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_FAILED = 'FAILED'
    STATUS_APPROVED = 'APPROVED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_APPLIED = 'APPLIED'
    STATUS_SUPERSEDED = 'SUPERSEDED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_RUNNING, 'Running'), (STATUS_COMPLETED, 'Completed'), (STATUS_FAILED, 'Failed'), (STATUS_APPROVED, 'Approved'), (STATUS_REJECTED, 'Rejected'), (STATUS_APPLIED, 'Applied'), (STATUS_SUPERSEDED, 'Superseded'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run_number = models.CharField(max_length=80, unique=True, blank=True)
    scenario_name = models.CharField(max_length=160)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    scheduling_policy = models.ForeignKey(SchedulingPolicy, on_delete=models.PROTECT, related_name='scheduling_runs')
    horizon_start = models.DateTimeField()
    horizon_end = models.DateTimeField()
    cutoff_timestamp = models.DateTimeField()
    plant = models.ForeignKey(Plant, null=True, blank=True, on_delete=models.PROTECT, related_name='scheduling_runs')
    work_center = models.ForeignKey(WorkCenter, null=True, blank=True, on_delete=models.PROTECT, related_name='scheduling_runs')
    direction = models.CharField(max_length=20, choices=SchedulingPolicy.DIRECTION_CHOICES, default=SchedulingPolicy.DIRECTION_FORWARD)
    input_checksum = models.CharField(max_length=64, blank=True)
    result_checksum = models.CharField(max_length=64, blank=True)
    policy_version = models.CharField(max_length=80, default='finite-scheduling-v1')
    parameter_snapshot = models.JSONField(default=dict, blank=True)
    freshness_snapshot = models.JSONField(default=dict, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    error_summary = models.TextField(blank=True)
    run_version = models.PositiveBigIntegerField(default=0)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='approved_scheduling_runs')
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='applied_scheduling_runs')
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='scheduling_runs')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    application_idempotency_key = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'horizon_start', 'horizon_end'])]

    def clean(self):
        super().clean()
        if self.horizon_end <= self.horizon_start:
            raise ValidationError({'horizon_end': 'Horizon end must be after start.'})

    def save(self, *args, **kwargs):
        if self.pk and SchedulingRun.objects.filter(pk=self.pk, status__in=[self.STATUS_COMPLETED, self.STATUS_APPROVED, self.STATUS_APPLIED]).exists():
            original = SchedulingRun.objects.get(pk=self.pk)
            mutable = {'status', 'approved_by', 'approved_at', 'applied_by', 'applied_at', 'run_version', 'application_idempotency_key'}
            for field in self._meta.fields:
                if field.name not in mutable and getattr(original, field.name) != getattr(self, field.name):
                    raise ValidationError({'scheduling_run': 'Completed scheduling run inputs and results are immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)


class SchedulingOperationSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheduling_run = models.ForeignKey(SchedulingRun, on_delete=models.CASCADE, related_name='operation_snapshots')
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name='scheduling_snapshots')
    manufacturing_operation = models.ForeignKey(ManufacturingOperation, on_delete=models.PROTECT, related_name='scheduling_snapshots')
    operation_number = models.PositiveIntegerField(null=True, blank=True)
    priority = models.CharField(max_length=20, default=ProductionOrder.PRIORITY_NORMAL)
    required_completion = models.DateTimeField(null=True, blank=True)
    current_planned_start = models.DateTimeField(null=True, blank=True)
    current_planned_end = models.DateTimeField(null=True, blank=True)
    fixed_start = models.BooleanField(default=False)
    fixed_end = models.BooleanField(default=False)
    fixed_reason = models.CharField(max_length=160, blank=True)
    source_order_version = models.PositiveBigIntegerField(default=0)
    source_execution_version = models.PositiveBigIntegerField(null=True, blank=True)
    work_center = models.ForeignKey(WorkCenter, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    selected_machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    eligible_machine_ids = models.JSONField(default=list, blank=True)
    calculated_duration_minutes = models.PositiveIntegerField(default=0)
    setup_duration_minutes = models.PositiveIntegerField(default=0)
    run_duration_minutes = models.PositiveIntegerField(default=0)
    queue_duration_minutes = models.PositiveIntegerField(default=0)
    move_duration_minutes = models.PositiveIntegerField(default=0)
    buffer_duration_minutes = models.PositiveIntegerField(default=0)
    execution_type = models.CharField(max_length=20, blank=True)
    status_snapshot = models.CharField(max_length=40, blank=True)
    source_metadata = models.JSONField(default=dict, blank=True)
    sequence = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['sequence', 'operation_number']

    def save(self, *args, **kwargs):
        if self.pk and SchedulingOperationSnapshot.objects.filter(pk=self.pk).exists():
            raise ValidationError({'snapshot': 'Scheduling operation snapshots are immutable.'})
        super().save(*args, **kwargs)


class SchedulingCalendarSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheduling_run = models.ForeignKey(SchedulingRun, on_delete=models.CASCADE, related_name='calendar_snapshots')
    resource_type = models.CharField(max_length=30)
    resource_id = models.CharField(max_length=80)
    interval_start = models.DateTimeField()
    interval_end = models.DateTimeField()
    capacity_units = models.DecimalField(max_digits=12, decimal_places=4)
    source_calendar = models.ForeignKey(ResourceCalendar, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    source_shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    source_exception = models.ForeignKey(CalendarException, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    availability_status = models.CharField(max_length=30, default='AVAILABLE')

    class Meta:
        ordering = ['resource_type', 'resource_id', 'interval_start']

    def save(self, *args, **kwargs):
        if self.pk and SchedulingCalendarSnapshot.objects.filter(pk=self.pk).exists():
            raise ValidationError({'snapshot': 'Scheduling calendar snapshots are immutable.'})
        super().save(*args, **kwargs)


class ScheduledOperationAssignment(models.Model):
    STATUS_PROPOSED = 'PROPOSED'
    STATUS_APPROVED = 'APPROVED'
    STATUS_APPLIED = 'APPLIED'
    STATUS_CHOICES = [(STATUS_PROPOSED, 'Proposed'), (STATUS_APPROVED, 'Approved'), (STATUS_APPLIED, 'Applied')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheduling_run = models.ForeignKey(SchedulingRun, on_delete=models.CASCADE, related_name='assignments')
    operation_snapshot = models.ForeignKey(SchedulingOperationSnapshot, on_delete=models.PROTECT, related_name='assignments')
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name='scheduled_assignments')
    manufacturing_operation = models.ForeignKey(ManufacturingOperation, on_delete=models.PROTECT, related_name='scheduled_assignments')
    planned_start = models.DateTimeField()
    planned_end = models.DateTimeField()
    setup_start = models.DateTimeField(null=True, blank=True)
    setup_end = models.DateTimeField(null=True, blank=True)
    run_start = models.DateTimeField(null=True, blank=True)
    run_end = models.DateTimeField(null=True, blank=True)
    work_center = models.ForeignKey(WorkCenter, null=True, blank=True, on_delete=models.PROTECT, related_name='scheduled_assignments')
    machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='scheduled_assignments')
    sequence_on_resource = models.PositiveIntegerField(default=1)
    dispatch_priority = models.PositiveIntegerField(default=100)
    lateness_minutes = models.IntegerField(default=0)
    slack_minutes = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PROPOSED)
    explanation = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['planned_start', 'sequence_on_resource']
        indexes = [models.Index(fields=['machine', 'planned_start', 'planned_end']), models.Index(fields=['work_center', 'planned_start', 'planned_end'])]

    def save(self, *args, **kwargs):
        if self.pk and ScheduledOperationAssignment.objects.filter(pk=self.pk).exists():
            original = ScheduledOperationAssignment.objects.get(pk=self.pk)
            if original.status == self.STATUS_APPLIED and self.status == self.STATUS_APPLIED:
                raise ValidationError({'assignment': 'Applied assignments are immutable.'})
            mutable = {'status'}
            for field in self._meta.fields:
                if field.name not in mutable and getattr(original, field.name) != getattr(self, field.name):
                    raise ValidationError({'assignment': 'Scheduling assignments are immutable except status transitions.'})
        super().save(*args, **kwargs)


class CapacityBucket(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheduling_run = models.ForeignKey(SchedulingRun, on_delete=models.CASCADE, related_name='capacity_buckets')
    resource_type = models.CharField(max_length=30)
    resource_id = models.CharField(max_length=80)
    bucket_start = models.DateTimeField()
    bucket_end = models.DateTimeField()
    available_minutes = models.PositiveIntegerField(default=0)
    fixed_load_minutes = models.PositiveIntegerField(default=0)
    proposed_load_minutes = models.PositiveIntegerField(default=0)
    utilization = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    overload_minutes = models.PositiveIntegerField(default=0)
    idle_minutes = models.PositiveIntegerField(default=0)
    bottleneck = models.BooleanField(default=False)
    affected_orders = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['resource_type', 'resource_id', 'bucket_start']


class SchedulingException(models.Model):
    SEVERITY_INFO = 'INFO'
    SEVERITY_WARNING = 'WARNING'
    SEVERITY_ERROR = 'ERROR'
    SEVERITY_CHOICES = [(SEVERITY_INFO, 'Info'), (SEVERITY_WARNING, 'Warning'), (SEVERITY_ERROR, 'Error')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheduling_run = models.ForeignKey(SchedulingRun, on_delete=models.CASCADE, related_name='exceptions')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    code = models.CharField(max_length=80)
    production_order = models.ForeignKey(ProductionOrder, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    manufacturing_operation = models.ForeignKey(ManufacturingOperation, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    resource_type = models.CharField(max_length=30, blank=True)
    resource_id = models.CharField(max_length=80, blank=True)
    interval_start = models.DateTimeField(null=True, blank=True)
    interval_end = models.DateTimeField(null=True, blank=True)
    message = models.TextField()
    blocking = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    sequence = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['sequence']


class ManufacturingOperationPrecedence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='precedences')
    predecessor = models.ForeignKey(ManufacturingOperation, on_delete=models.CASCADE, related_name='successor_links')
    successor = models.ForeignKey(ManufacturingOperation, on_delete=models.CASCADE, related_name='predecessor_links')
    source_opc_edge = models.ForeignKey(OPCEdge, on_delete=models.PROTECT, related_name='manufacturing_precedences')
    edge_type = models.CharField(max_length=20)
    label_snapshot = models.CharField(max_length=255, blank=True)
    sequence = models.PositiveIntegerField(default=1)
    optional = models.BooleanField(default=False)
    rework = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sequence', 'source_opc_edge_id']
        unique_together = [('production_order', 'source_opc_edge'), ('predecessor', 'successor', 'edge_type')]
        indexes = [models.Index(fields=['production_order', 'sequence'])]

    def clean(self):
        super().clean()
        if self.predecessor_id and self.successor_id and self.predecessor.production_order_id != self.successor.production_order_id:
            raise ValidationError({'successor': 'Precedence operations must belong to the same production order.'})
        if self.production_order_id and self.predecessor_id and self.predecessor.production_order_id != self.production_order_id:
            raise ValidationError({'predecessor': 'Predecessor belongs to a different production order.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ManufacturingOperationEligibleMachine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(ManufacturingOperation, on_delete=models.CASCADE, related_name='eligible_machines')
    machine_asset = models.ForeignKey(MachineAsset, on_delete=models.PROTECT, related_name='eligible_operation_snapshots')
    machine_code_snapshot = models.CharField(max_length=80)
    machine_name_snapshot = models.CharField(max_length=255, blank=True)
    preferred = models.BooleanField(default=False)
    reason = models.CharField(max_length=120, default='SOURCE_OPC_ASSIGNMENT')
    sequence = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['sequence', 'machine_code_snapshot']
        unique_together = [('operation', 'machine_asset')]


class ProductionMaterialRequirement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='material_requirements')
    operation = models.ForeignKey(ManufacturingOperation, on_delete=models.CASCADE, related_name='material_requirements')
    source_allocation = models.ForeignKey(OPCOperationMaterialAllocation, on_delete=models.PROTECT, related_name='production_material_requirements')
    source_mbom_line = models.ForeignKey('enterprise_items.BOMLine', null=True, blank=True, on_delete=models.PROTECT, related_name='production_material_requirements')
    component_item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='production_material_requirements')
    component_code_snapshot = models.CharField(max_length=80)
    component_revision_snapshot = models.CharField(max_length=30)
    quantity_per_unit = models.DecimalField(max_digits=18, decimal_places=6)
    planned_order_quantity = models.DecimalField(max_digits=18, decimal_places=6)
    total_net_quantity = models.DecimalField(max_digits=18, decimal_places=6)
    scrap_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    total_planned_quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30, default='EA')
    allocation_type = models.CharField(max_length=20)
    sequence = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['operation__sequence', 'sequence', 'source_allocation_id']
        unique_together = [('production_order', 'source_allocation')]


class ProductionToolingRequirement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='tooling_requirements')
    operation = models.ForeignKey(ManufacturingOperation, on_delete=models.CASCADE, related_name='tooling_requirements')
    source_tooling_requirement = models.ForeignKey(OPCToolingRequirement, on_delete=models.PROTECT, related_name='production_tooling_requirements')
    tooling_definition = models.ForeignKey(ToolingDefinition, on_delete=models.PROTECT, related_name='production_tooling_requirements')
    tooling_code_snapshot = models.CharField(max_length=80)
    tooling_name_snapshot = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    mandatory = models.BooleanField(default=True)
    sequence = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['operation__sequence', 'sequence', 'source_tooling_requirement_id']
        unique_together = [('production_order', 'source_tooling_requirement')]


class ProductionDocumentRequirement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='document_requirements')
    operation = models.ForeignKey(ManufacturingOperation, on_delete=models.CASCADE, related_name='document_requirements')
    source_document_requirement = models.ForeignKey(OPCNodeDocumentRequirement, on_delete=models.PROTECT, related_name='production_document_requirements')
    controlled_document_revision = models.ForeignKey(ControlledDocumentRevision, on_delete=models.PROTECT, related_name='production_document_requirements')
    document_number_snapshot = models.CharField(max_length=80)
    document_revision_snapshot = models.CharField(max_length=30)
    title_snapshot = models.CharField(max_length=255)
    purpose = models.CharField(max_length=40)
    mandatory = models.BooleanField(default=True)
    sequence = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['operation__sequence', 'sequence', 'source_document_requirement_id']
        unique_together = [('production_order', 'source_document_requirement')]


class ProductionOrderValidationEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='validation_evidence')
    order_version = models.PositiveBigIntegerField()
    policy_version = models.CharField(max_length=80, default='production-order-release-v1')
    validated_at = models.DateTimeField()
    released_at = models.DateTimeField(null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='production_order_validation_evidence')
    valid = models.BooleanField(default=False)
    release_ready = models.BooleanField(default=False)
    counts = models.JSONField(default=dict, blank=True)
    issues = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['production_order', 'order_version', 'policy_version'])]


class OperationExecution(models.Model):
    STATUS_NOT_READY = 'NOT_READY'
    STATUS_READY = 'READY'
    STATUS_DISPATCHED = 'DISPATCHED'
    STATUS_RUNNING = 'RUNNING'
    STATUS_PAUSED = 'PAUSED'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_BLOCKED = 'BLOCKED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_NOT_READY, 'Not ready'),
        (STATUS_READY, 'Ready'),
        (STATUS_DISPATCHED, 'Dispatched'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_PAUSED, 'Paused'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_BLOCKED, 'Blocked'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='operation_executions')
    manufacturing_operation = models.OneToOneField(ManufacturingOperation, on_delete=models.PROTECT, related_name='execution')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NOT_READY)
    execution_version = models.PositiveBigIntegerField(default=0)
    dispatch_priority = models.PositiveIntegerField(default=100)
    ready = models.BooleanField(default=False)
    block_reason = models.TextField(blank=True)
    assigned_operator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='assigned_operation_executions')
    assigned_machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='operation_executions')
    planned_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    produced_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    accepted_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    rejected_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    scrapped_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    rework_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    actual_setup_seconds = models.PositiveIntegerField(default=0)
    actual_run_seconds = models.PositiveIntegerField(default=0)
    active_interval = models.CharField(max_length=10, blank=True)
    active_interval_started_at = models.DateTimeField(null=True, blank=True)
    first_started_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    current_cycle_number = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['production_order__order_number', 'manufacturing_operation__sequence']
        indexes = [models.Index(fields=['production_order', 'status']), models.Index(fields=['ready', 'status'])]

    def clean(self):
        super().clean()
        for field in ('produced_quantity', 'accepted_quantity', 'rejected_quantity', 'scrapped_quantity', 'rework_quantity'):
            if getattr(self, field) is not None and getattr(self, field) < 0:
                raise ValidationError({field: 'Execution quantities cannot be negative.'})
        if self.accepted_quantity + self.rejected_quantity + self.scrapped_quantity > self.produced_quantity:
            raise ValidationError({'accepted_quantity': 'Accepted, rejected and scrapped quantity cannot exceed produced quantity.'})
        if self.production_order_id and self.manufacturing_operation_id and self.manufacturing_operation.production_order_id != self.production_order_id:
            raise ValidationError({'manufacturing_operation': 'Operation belongs to a different production order.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class OperationExecutionCycle(models.Model):
    STATUS_OPEN = 'OPEN'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_OPEN, 'Open'), (STATUS_COMPLETED, 'Completed'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation_execution = models.ForeignKey(OperationExecution, on_delete=models.CASCADE, related_name='cycles')
    cycle_number = models.PositiveIntegerField(default=1)
    reason = models.TextField(blank=True)
    source_rework_edge = models.ForeignKey(ManufacturingOperationPrecedence, null=True, blank=True, on_delete=models.PROTECT, related_name='execution_cycles')
    quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_operation_execution_cycles')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['operation_execution', 'cycle_number']
        unique_together = [('operation_execution', 'cycle_number')]


class ManufacturingExecutionEvent(models.Model):
    EVENT_DISPATCH = 'DISPATCH'
    EVENT_ASSIGN_OPERATOR = 'ASSIGN_OPERATOR'
    EVENT_ASSIGN_MACHINE = 'ASSIGN_MACHINE'
    EVENT_START = 'START'
    EVENT_PAUSE = 'PAUSE'
    EVENT_RESUME = 'RESUME'
    EVENT_RECORD_OUTPUT = 'RECORD_OUTPUT'
    EVENT_COMPLETE = 'COMPLETE'
    EVENT_BLOCK = 'BLOCK'
    EVENT_UNBLOCK = 'UNBLOCK'
    EVENT_CANCEL = 'CANCEL'
    EVENT_CONSUME_MATERIAL = 'CONSUME_MATERIAL'
    EVENT_REVERSE_MATERIAL = 'REVERSE_MATERIAL'
    EVENT_TOOLING_USAGE = 'TOOLING_USAGE'
    EVENT_DOCUMENT_ACK = 'DOCUMENT_ACK'
    EVENT_REWORK = 'REWORK'
    EVENT_GENEALOGY_LINK = 'GENEALOGY_LINK'
    EVENT_CHOICES = [
        (EVENT_DISPATCH, 'Dispatch'),
        (EVENT_ASSIGN_OPERATOR, 'Assign operator'),
        (EVENT_ASSIGN_MACHINE, 'Assign machine'),
        (EVENT_START, 'Start'),
        (EVENT_PAUSE, 'Pause'),
        (EVENT_RESUME, 'Resume'),
        (EVENT_RECORD_OUTPUT, 'Record output'),
        (EVENT_COMPLETE, 'Complete'),
        (EVENT_BLOCK, 'Block'),
        (EVENT_UNBLOCK, 'Unblock'),
        (EVENT_CANCEL, 'Cancel'),
        (EVENT_CONSUME_MATERIAL, 'Consume material'),
        (EVENT_REVERSE_MATERIAL, 'Reverse material'),
        (EVENT_TOOLING_USAGE, 'Tooling usage'),
        (EVENT_DOCUMENT_ACK, 'Document acknowledgement'),
        (EVENT_REWORK, 'Rework'),
        (EVENT_GENEALOGY_LINK, 'Genealogy link'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='execution_events')
    operation_execution = models.ForeignKey(OperationExecution, on_delete=models.CASCADE, related_name='events')
    cycle = models.ForeignKey(OperationExecutionCycle, null=True, blank=True, on_delete=models.PROTECT, related_name='events')
    event_sequence = models.PositiveBigIntegerField()
    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='manufacturing_execution_events')
    assigned_operator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='manufacturing_execution_operator_events')
    machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='manufacturing_execution_events')
    event_timestamp = models.DateTimeField()
    server_recorded_at = models.DateTimeField(auto_now_add=True)
    produced_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    accepted_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    rejected_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    scrapped_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    rework_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True)
    previous_execution_version = models.PositiveBigIntegerField()
    new_execution_version = models.PositiveBigIntegerField()
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['operation_execution', 'event_sequence']
        unique_together = [('operation_execution', 'event_sequence'), ('operation_execution', 'idempotency_key')]
        indexes = [models.Index(fields=['production_order', 'event_type']), models.Index(fields=['operation_execution', 'event_type'])]

    def save(self, *args, **kwargs):
        if self.pk and ManufacturingExecutionEvent.objects.filter(pk=self.pk).exists():
            raise ValidationError({'event': 'Execution events are append-only and immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'event': 'Execution events cannot be deleted.'})


class ProductionLot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lot_number = models.CharField(max_length=120)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='production_lots')
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name='lots')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_production_lots')
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['lot_number']
        unique_together = [('item_revision', 'lot_number')]


class ProductionSerial(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    serial_number = models.CharField(max_length=120)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='production_serials')
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name='serials')
    lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='serials')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_production_serials')
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['serial_number']
        unique_together = [('item_revision', 'serial_number')]


class ProductionMaterialConsumption(models.Model):
    TYPE_CONSUME = 'CONSUME'
    TYPE_REVERSAL = 'REVERSAL'
    TYPE_CHOICES = [(TYPE_CONSUME, 'Consume'), (TYPE_REVERSAL, 'Reversal')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='material_consumptions')
    operation_execution = models.ForeignKey(OperationExecution, on_delete=models.CASCADE, related_name='material_consumptions')
    cycle = models.ForeignKey(OperationExecutionCycle, on_delete=models.PROTECT, related_name='material_consumptions')
    requirement = models.ForeignKey(ProductionMaterialRequirement, on_delete=models.PROTECT, related_name='consumptions')
    component_item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='production_consumptions')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30)
    consumption_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_CONSUME)
    lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='material_consumptions')
    serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='material_consumptions')
    event = models.OneToOneField(ManufacturingExecutionEvent, on_delete=models.PROTECT, related_name='material_consumption')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='production_material_consumptions')
    consumed_at = models.DateTimeField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['consumed_at']
        indexes = [models.Index(fields=['production_order', 'requirement'])]


class ProductionToolingUsage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='tooling_usages')
    operation_execution = models.ForeignKey(OperationExecution, on_delete=models.CASCADE, related_name='tooling_usages')
    requirement = models.ForeignKey(ProductionToolingRequirement, on_delete=models.PROTECT, related_name='usages')
    tooling_definition = models.ForeignKey(ToolingDefinition, on_delete=models.PROTECT, related_name='production_usages')
    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='tooling_usages')
    event = models.OneToOneField(ManufacturingExecutionEvent, on_delete=models.PROTECT, related_name='tooling_usage')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='production_tooling_usages')
    used_at = models.DateTimeField()
    notes = models.TextField(blank=True)


class ProductionDocumentAcknowledgement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='document_acknowledgements')
    operation_execution = models.ForeignKey(OperationExecution, on_delete=models.CASCADE, related_name='document_acknowledgements')
    requirement = models.ForeignKey(ProductionDocumentRequirement, on_delete=models.PROTECT, related_name='acknowledgements')
    controlled_document_revision = models.ForeignKey(ControlledDocumentRevision, on_delete=models.PROTECT, related_name='production_acknowledgements')
    event = models.OneToOneField(ManufacturingExecutionEvent, on_delete=models.PROTECT, related_name='document_acknowledgement')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='production_document_acknowledgements')
    acknowledged_at = models.DateTimeField()
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [('operation_execution', 'requirement', 'actor')]


class ManufacturingGenealogyLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='genealogy_links')
    parent_lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='component_genealogy_links')
    parent_serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='component_genealogy_links')
    component_lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='parent_genealogy_links')
    component_serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='parent_genealogy_links')
    consumption = models.ForeignKey(ProductionMaterialConsumption, on_delete=models.PROTECT, related_name='genealogy_links')
    event = models.OneToOneField(ManufacturingExecutionEvent, on_delete=models.PROTECT, related_name='genealogy_link')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='manufacturing_genealogy_links')
    linked_at = models.DateTimeField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['linked_at']
        indexes = [models.Index(fields=['production_order'])]

    def clean(self):
        super().clean()
        if not self.parent_lot_id and not self.parent_serial_id:
            raise ValidationError({'parent': 'Parent lot or serial is required.'})
        if not self.component_lot_id and not self.component_serial_id:
            raise ValidationError({'component': 'Component lot or serial is required.'})


class InspectionPlan(models.Model):
    TYPE_RECEIVING = 'RECEIVING'
    TYPE_IN_PROCESS = 'IN_PROCESS'
    TYPE_FIRST_ARTICLE = 'FIRST_ARTICLE'
    TYPE_FINAL = 'FINAL'
    TYPE_TOOLING = 'TOOLING'
    TYPE_SETUP = 'SETUP'
    TYPE_OTHER = 'OTHER'
    TYPE_CHOICES = [
        (TYPE_RECEIVING, 'Receiving'),
        (TYPE_IN_PROCESS, 'In process'),
        (TYPE_FIRST_ARTICLE, 'First article'),
        (TYPE_FINAL, 'Final'),
        (TYPE_TOOLING, 'Tooling'),
        (TYPE_SETUP, 'Setup'),
        (TYPE_OTHER, 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan_code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    inspection_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_IN_PROCESS)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['plan_code']

    def clean(self):
        super().clean()
        self.plan_code = (self.plan_code or '').strip().upper()
        if not self.plan_code:
            raise ValidationError({'plan_code': 'Inspection plan code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InspectionPlanRevision(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_RELEASED = 'RELEASED'
    STATUS_SUPERSEDED = 'SUPERSEDED'
    STATUS_OBSOLETE = 'OBSOLETE'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_RELEASED, 'Released'),
        (STATUS_SUPERSEDED, 'Superseded'),
        (STATUS_OBSOLETE, 'Obsolete'),
    ]
    LOCKED_STATUSES = {STATUS_RELEASED, STATUS_SUPERSEDED, STATUS_OBSOLETE}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection_plan = models.ForeignKey(InspectionPlan, on_delete=models.CASCADE, related_name='revisions')
    revision = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    instruction_document_revision = models.ForeignKey(ControlledDocumentRevision, null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_plan_revisions')
    change_summary = models.TextField(blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='released_inspection_plan_revisions')
    released_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='superseded_revisions')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_inspection_plan_revisions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['inspection_plan__plan_code', 'revision']
        unique_together = [('inspection_plan', 'revision')]

    def clean(self):
        super().clean()
        self.revision = (self.revision or '').strip().upper()
        if not self.revision:
            raise ValidationError({'revision': 'Revision is required.'})
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective end cannot precede effective start.'})
        if self.instruction_document_revision_id and self.instruction_document_revision.status != ControlledDocumentRevision.STATUS_RELEASED:
            raise ValidationError({'instruction_document_revision': 'Inspection instructions must reference a released document revision.'})
        if self.pk:
            original = InspectionPlanRevision.objects.filter(pk=self.pk).values('status', 'revision', 'instruction_document_revision_id').first()
            if original and original['status'] in self.LOCKED_STATUSES and (
                original['revision'] != self.revision or original['instruction_document_revision_id'] != self.instruction_document_revision_id
            ):
                raise ValidationError({'status': 'Released inspection plan revisions are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InspectionApplicability(models.Model):
    TYPE_OPERATION = 'OPERATION'
    TYPE_FINAL = 'FINAL'
    TYPE_ITEM = 'ITEM'
    TYPE_PROCESS = 'PROCESS'
    TYPE_TOOLING = 'TOOLING'
    TYPE_CHOICES = [
        (TYPE_OPERATION, 'Operation'),
        (TYPE_FINAL, 'Final order inspection'),
        (TYPE_ITEM, 'Item'),
        (TYPE_PROCESS, 'Process'),
        (TYPE_TOOLING, 'Tooling'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection_plan_revision = models.ForeignKey(InspectionPlanRevision, on_delete=models.PROTECT, related_name='applicabilities')
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_applicabilities')
    process_definition = models.ForeignKey(ProcessDefinition, null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_applicabilities')
    opc_node = models.ForeignKey(OPCNode, null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_applicabilities')
    tooling_definition = models.ForeignKey(ToolingDefinition, null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_applicabilities')
    applicability_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_OPERATION)
    mandatory = models.BooleanField(default=True)
    sequence = models.PositiveIntegerField(default=1)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sequence', 'created_at']
        indexes = [models.Index(fields=['applicability_type', 'mandatory'])]

    def clean(self):
        super().clean()
        if self.inspection_plan_revision_id and self.inspection_plan_revision.status != InspectionPlanRevision.STATUS_RELEASED:
            raise ValidationError({'inspection_plan_revision': 'Applicability requires a released inspection plan revision.'})
        targets = [self.item_revision_id, self.process_definition_id, self.opc_node_id, self.tooling_definition_id]
        if not any(targets):
            raise ValidationError({'applicability': 'At least one applicability target is required.'})
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective end cannot precede effective start.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InspectionCharacteristic(models.Model):
    TYPE_NUMERIC = 'NUMERIC'
    TYPE_BOOLEAN = 'BOOLEAN'
    TYPE_ATTRIBUTE = 'ATTRIBUTE'
    TYPE_TEXT = 'TEXT'
    TYPE_VISUAL = 'VISUAL'
    TYPE_CHOICES = [
        (TYPE_NUMERIC, 'Numeric'),
        (TYPE_BOOLEAN, 'Boolean'),
        (TYPE_ATTRIBUTE, 'Attribute'),
        (TYPE_TEXT, 'Text'),
        (TYPE_VISUAL, 'Visual'),
    ]
    SAMPLING_ALL = 'ALL'
    SAMPLING_FIXED_COUNT = 'FIXED_COUNT'
    SAMPLING_PERCENTAGE = 'PERCENTAGE'
    SAMPLING_MANUAL = 'MANUAL'
    SAMPLING_CHOICES = [
        (SAMPLING_ALL, 'Inspect all'),
        (SAMPLING_FIXED_COUNT, 'Fixed count'),
        (SAMPLING_PERCENTAGE, 'Percentage'),
        (SAMPLING_MANUAL, 'Manual'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection_plan_revision = models.ForeignKey(InspectionPlanRevision, on_delete=models.CASCADE, related_name='characteristics')
    characteristic_number = models.CharField(max_length=80)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    characteristic_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    unit = models.CharField(max_length=30, blank=True)
    nominal_value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    lower_spec_limit = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    upper_spec_limit = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    lower_tolerance = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    upper_tolerance = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    expected_boolean = models.BooleanField(null=True, blank=True)
    allowed_attribute_values = models.JSONField(default=list, blank=True)
    mandatory = models.BooleanField(default=True)
    destructive = models.BooleanField(default=False)
    sampling_method = models.CharField(max_length=20, choices=SAMPLING_CHOICES, default=SAMPLING_FIXED_COUNT)
    sample_size = models.PositiveIntegerField(default=1)
    inspect_all = models.BooleanField(default=False)
    acceptance_number = models.PositiveIntegerField(default=0)
    rejection_number = models.PositiveIntegerField(default=1)
    percentage = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    sequence = models.PositiveIntegerField(default=1)
    instruction_document_revision = models.ForeignKey(ControlledDocumentRevision, null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_characteristics')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['inspection_plan_revision', 'sequence', 'characteristic_number']
        unique_together = [('inspection_plan_revision', 'characteristic_number')]

    def effective_limits(self):
        lower = self.lower_spec_limit
        upper = self.upper_spec_limit
        if self.nominal_value is not None:
            if self.lower_tolerance is not None:
                lower = self.nominal_value - self.lower_tolerance
            if self.upper_tolerance is not None:
                upper = self.nominal_value + self.upper_tolerance
        return lower, upper

    def clean(self):
        super().clean()
        self.characteristic_number = (self.characteristic_number or '').strip().upper()
        revision_status = InspectionPlanRevision.objects.filter(pk=self.inspection_plan_revision_id).values_list('status', flat=True).first() if self.inspection_plan_revision_id else None
        if revision_status in InspectionPlanRevision.LOCKED_STATUSES and self.pk:
            original = InspectionCharacteristic.objects.filter(pk=self.pk).values('name', 'characteristic_type').first()
            if original and (original['name'] != self.name or original['characteristic_type'] != self.characteristic_type):
                raise ValidationError({'inspection_plan_revision': 'Characteristics on released revisions are immutable.'})
        if self.characteristic_type == self.TYPE_NUMERIC:
            if self.expected_boolean is not None or self.allowed_attribute_values:
                raise ValidationError({'characteristic_type': 'Numeric characteristics cannot define Boolean or attribute criteria.'})
            if self.nominal_value is not None and (self.lower_spec_limit is not None or self.upper_spec_limit is not None):
                raise ValidationError({'nominal_value': 'Use direct limits or nominal/tolerance, not both.'})
            lower, upper = self.effective_limits()
            if lower is None and upper is None:
                raise ValidationError({'lower_spec_limit': 'Numeric characteristics require at least one effective limit.'})
            if lower is not None and upper is not None and lower > upper:
                raise ValidationError({'upper_spec_limit': 'Lower limit cannot exceed upper limit.'})
        if self.characteristic_type == self.TYPE_BOOLEAN and self.expected_boolean is None:
            raise ValidationError({'expected_boolean': 'Boolean characteristics require an expected value.'})
        if self.characteristic_type == self.TYPE_ATTRIBUTE and not self.allowed_attribute_values:
            raise ValidationError({'allowed_attribute_values': 'Attribute characteristics require allowed values.'})
        if self.sample_size <= 0:
            raise ValidationError({'sample_size': 'Sample size must be positive.'})
        if self.percentage is not None and (self.percentage <= 0 or self.percentage > 100):
            raise ValidationError({'percentage': 'Percentage must be greater than 0 and at most 100.'})
        if self.rejection_number <= self.acceptance_number:
            raise ValidationError({'rejection_number': 'Rejection number must be greater than acceptance number.'})
        if self.instruction_document_revision_id and self.instruction_document_revision.status != ControlledDocumentRevision.STATUS_RELEASED:
            raise ValidationError({'instruction_document_revision': 'Characteristic instructions must reference a released document revision.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ProductionInspectionRequirement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='inspection_requirements')
    operation = models.ForeignKey(ManufacturingOperation, null=True, blank=True, on_delete=models.CASCADE, related_name='inspection_requirements')
    source_applicability = models.ForeignKey(InspectionApplicability, null=True, blank=True, on_delete=models.PROTECT, related_name='production_requirements')
    inspection_plan_revision = models.ForeignKey(InspectionPlanRevision, on_delete=models.PROTECT, related_name='production_requirements')
    inspection_type = models.CharField(max_length=30, choices=InspectionPlan.TYPE_CHOICES)
    mandatory = models.BooleanField(default=True)
    sampling_policy_snapshot = models.JSONField(default=dict, blank=True)
    instruction_document_revision = models.ForeignKey(ControlledDocumentRevision, null=True, blank=True, on_delete=models.PROTECT, related_name='production_inspection_requirements')
    sequence = models.PositiveIntegerField(default=1)
    source_metadata_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['operation__sequence', 'sequence', 'created_at']
        unique_together = [('production_order', 'operation', 'inspection_plan_revision')]
        indexes = [models.Index(fields=['production_order', 'mandatory'])]

    def clean(self):
        super().clean()
        if self.operation_id and self.operation.production_order_id != self.production_order_id:
            raise ValidationError({'operation': 'Inspection requirement operation belongs to a different order.'})
        if self.inspection_plan_revision_id and self.inspection_plan_revision.status != InspectionPlanRevision.STATUS_RELEASED:
            raise ValidationError({'inspection_plan_revision': 'Production inspection requirements require released inspection revisions.'})
        if self.production_order_id and self.production_order.status in ProductionOrder.LOCKED_STATUSES and self.pk:
            original = ProductionInspectionRequirement.objects.filter(pk=self.pk).values('inspection_plan_revision_id', 'mandatory').first()
            if original and (original['inspection_plan_revision_id'] != self.inspection_plan_revision_id or original['mandatory'] != self.mandatory):
                raise ValidationError({'production_order': 'Released production inspection requirements are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InspectionExecution(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_READY = 'READY'
    STATUS_IN_PROGRESS = 'IN_PROGRESS'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_FAILED = 'FAILED'
    STATUS_ON_HOLD = 'ON_HOLD'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_READY, 'Ready'),
        (STATUS_IN_PROGRESS, 'In progress'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_ON_HOLD, 'On hold'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]
    RESULT_NOT_EVALUATED = 'NOT_EVALUATED'
    RESULT_PASS = 'PASS'
    RESULT_FAIL = 'FAIL'
    RESULT_CONDITIONAL = 'CONDITIONAL'
    RESULT_INCOMPLETE = 'INCOMPLETE'
    RESULT_CHOICES = [
        (RESULT_NOT_EVALUATED, 'Not evaluated'),
        (RESULT_PASS, 'Pass'),
        (RESULT_FAIL, 'Fail'),
        (RESULT_CONDITIONAL, 'Conditional'),
        (RESULT_INCOMPLETE, 'Incomplete'),
    ]
    DISPOSITION_NONE = 'NONE'
    DISPOSITION_REQUIRED = 'REQUIRED'
    DISPOSITION_ACCEPTED = 'ACCEPTED'
    DISPOSITION_REWORK = 'REWORK'
    DISPOSITION_SCRAP = 'SCRAP'
    DISPOSITION_CHOICES = [
        (DISPOSITION_NONE, 'None'),
        (DISPOSITION_REQUIRED, 'Required'),
        (DISPOSITION_ACCEPTED, 'Accepted'),
        (DISPOSITION_REWORK, 'Rework'),
        (DISPOSITION_SCRAP, 'Scrap'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requirement = models.ForeignKey(ProductionInspectionRequirement, on_delete=models.PROTECT, related_name='inspection_executions')
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='inspection_executions')
    operation_execution = models.ForeignKey(OperationExecution, null=True, blank=True, on_delete=models.CASCADE, related_name='inspection_executions')
    execution_cycle = models.ForeignKey(OperationExecutionCycle, null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_executions')
    serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_executions')
    lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_executions')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    inspection_version = models.PositiveBigIntegerField(default=0)
    assigned_inspector = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='assigned_inspection_executions')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    evaluated_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, default=RESULT_NOT_EVALUATED)
    disposition_status = models.CharField(max_length=20, choices=DISPOSITION_CHOICES, default=DISPOSITION_NONE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['production_order__order_number', 'requirement__sequence', 'created_at']
        indexes = [models.Index(fields=['production_order', 'status', 'result']), models.Index(fields=['operation_execution', 'status'])]

    def clean(self):
        super().clean()
        if self.requirement_id and self.production_order_id and self.requirement.production_order_id != self.production_order_id:
            raise ValidationError({'requirement': 'Inspection requirement belongs to a different order.'})
        if self.operation_execution_id and self.operation_execution.production_order_id != self.production_order_id:
            raise ValidationError({'operation_execution': 'Operation execution belongs to a different order.'})
        if self.serial_id and self.serial.production_order_id != self.production_order_id:
            raise ValidationError({'serial': 'Serial belongs to a different order.'})
        if self.lot_id and self.lot.production_order_id != self.production_order_id:
            raise ValidationError({'lot': 'Lot belongs to a different order.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InspectionSample(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_PASS = 'PASS'
    STATUS_FAIL = 'FAIL'
    STATUS_INCOMPLETE = 'INCOMPLETE'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_PASS, 'Pass'), (STATUS_FAIL, 'Fail'), (STATUS_INCOMPLETE, 'Incomplete')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection_execution = models.ForeignKey(InspectionExecution, on_delete=models.CASCADE, related_name='samples')
    sample_number = models.PositiveIntegerField(default=1)
    serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_samples')
    lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='inspection_samples')
    quantity_represented = models.DecimalField(max_digits=18, decimal_places=6, default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['inspection_execution', 'sample_number']
        unique_together = [('inspection_execution', 'sample_number')]

    def clean(self):
        super().clean()
        if self.serial_id and self.serial.production_order_id != self.inspection_execution.production_order_id:
            raise ValidationError({'serial': 'Sample serial belongs to a different order.'})
        if self.lot_id and self.lot.production_order_id != self.inspection_execution.production_order_id:
            raise ValidationError({'lot': 'Sample lot belongs to a different order.'})
        if self.quantity_represented <= 0:
            raise ValidationError({'quantity_represented': 'Sample quantity must be positive.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class QualityEvent(models.Model):
    EVENT_MEASUREMENT = 'MEASUREMENT'
    EVENT_CORRECTION = 'CORRECTION'
    EVENT_EVALUATION = 'EVALUATION'
    EVENT_COMPLETE = 'COMPLETE'
    EVENT_HOLD = 'HOLD'
    EVENT_RELEASE_HOLD = 'RELEASE_HOLD'
    EVENT_NCR = 'NCR'
    EVENT_DISPOSITION = 'DISPOSITION'
    EVENT_REWORK = 'REWORK'
    EVENT_REINSPECTION = 'REINSPECTION'
    EVENT_CHOICES = [
        (EVENT_MEASUREMENT, 'Measurement'),
        (EVENT_CORRECTION, 'Correction'),
        (EVENT_EVALUATION, 'Evaluation'),
        (EVENT_COMPLETE, 'Complete'),
        (EVENT_HOLD, 'Hold'),
        (EVENT_RELEASE_HOLD, 'Release hold'),
        (EVENT_NCR, 'Nonconformance'),
        (EVENT_DISPOSITION, 'Disposition'),
        (EVENT_REWORK, 'Rework'),
        (EVENT_REINSPECTION, 'Reinspection'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='quality_events')
    inspection_execution = models.ForeignKey(InspectionExecution, null=True, blank=True, on_delete=models.CASCADE, related_name='quality_events')
    event_sequence = models.PositiveBigIntegerField()
    event_type = models.CharField(max_length=30, choices=EVENT_CHOICES)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='quality_events')
    event_timestamp = models.DateTimeField()
    server_recorded_at = models.DateTimeField(auto_now_add=True)
    idempotency_key = models.CharField(max_length=120, blank=True)
    previous_version = models.PositiveBigIntegerField(default=0)
    new_version = models.PositiveBigIntegerField(default=0)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['production_order', 'event_sequence']
        unique_together = [('production_order', 'event_sequence'), ('production_order', 'idempotency_key')]
        indexes = [models.Index(fields=['inspection_execution', 'event_type']), models.Index(fields=['production_order', 'event_type'])]

    def save(self, *args, **kwargs):
        if self.pk and QualityEvent.objects.filter(pk=self.pk).exists():
            raise ValidationError({'event': 'Quality events are append-only and immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'event': 'Quality events cannot be deleted.'})


class InspectionMeasurement(models.Model):
    RESULT_PASS = 'PASS'
    RESULT_FAIL = 'FAIL'
    RESULT_INCOMPLETE = 'INCOMPLETE'
    RESULT_NOT_EVALUATED = 'NOT_EVALUATED'
    RESULT_CHOICES = [
        (RESULT_PASS, 'Pass'),
        (RESULT_FAIL, 'Fail'),
        (RESULT_INCOMPLETE, 'Incomplete'),
        (RESULT_NOT_EVALUATED, 'Not evaluated'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection_execution = models.ForeignKey(InspectionExecution, on_delete=models.CASCADE, related_name='measurements')
    sample = models.ForeignKey(InspectionSample, on_delete=models.CASCADE, related_name='measurements')
    characteristic = models.ForeignKey(InspectionCharacteristic, on_delete=models.PROTECT, related_name='measurements')
    measurement_sequence = models.PositiveBigIntegerField(default=1)
    numeric_value = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    boolean_value = models.BooleanField(null=True, blank=True)
    attribute_value = models.CharField(max_length=255, blank=True)
    text_value = models.TextField(blank=True)
    observed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='inspection_measurements')
    observed_at = models.DateTimeField()
    server_recorded_at = models.DateTimeField(auto_now_add=True)
    evaluation_result = models.CharField(max_length=20, choices=RESULT_CHOICES, default=RESULT_NOT_EVALUATED)
    nonconforming = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    supersedes = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='superseded_by')
    quality_event = models.OneToOneField(QualityEvent, on_delete=models.PROTECT, related_name='measurement')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['inspection_execution', 'sample__sample_number', 'characteristic__sequence', 'measurement_sequence']
        indexes = [models.Index(fields=['inspection_execution', 'evaluation_result']), models.Index(fields=['characteristic', 'nonconforming'])]

    def clean(self):
        super().clean()
        if self.sample_id and self.sample.inspection_execution_id != self.inspection_execution_id:
            raise ValidationError({'sample': 'Sample belongs to a different inspection.'})
        if self.characteristic_id and self.inspection_execution_id:
            if self.characteristic.inspection_plan_revision_id != self.inspection_execution.requirement.inspection_plan_revision_id:
                raise ValidationError({'characteristic': 'Characteristic belongs to a different inspection plan revision.'})
        values = [self.numeric_value is not None, self.boolean_value is not None, bool(self.attribute_value), bool(self.text_value)]
        if sum(1 for value in values if value) != 1:
            raise ValidationError({'value': 'Exactly one measurement value is required.'})
        if self.characteristic_id:
            kind = self.characteristic.characteristic_type
            if kind == InspectionCharacteristic.TYPE_NUMERIC and self.numeric_value is None:
                raise ValidationError({'numeric_value': 'Numeric value is required.'})
            if kind == InspectionCharacteristic.TYPE_BOOLEAN and self.boolean_value is None:
                raise ValidationError({'boolean_value': 'Boolean value is required.'})
            if kind == InspectionCharacteristic.TYPE_ATTRIBUTE and not self.attribute_value:
                raise ValidationError({'attribute_value': 'Attribute value is required.'})
            if kind in {InspectionCharacteristic.TYPE_TEXT, InspectionCharacteristic.TYPE_VISUAL} and not self.text_value:
                raise ValidationError({'text_value': 'Observation text is required.'})

    def save(self, *args, **kwargs):
        if self.pk and InspectionMeasurement.objects.filter(pk=self.pk).exists():
            raise ValidationError({'measurement': 'Inspection measurements are append-only. Use a correction.'})
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'measurement': 'Inspection measurements cannot be deleted.'})


class QualityHold(models.Model):
    SCOPE_ORDER = 'ORDER'
    SCOPE_OPERATION = 'OPERATION'
    SCOPE_INSPECTION = 'INSPECTION'
    SCOPE_SERIAL = 'SERIAL'
    SCOPE_LOT = 'LOT'
    SCOPE_CHOICES = [(SCOPE_ORDER, 'Order'), (SCOPE_OPERATION, 'Operation'), (SCOPE_INSPECTION, 'Inspection'), (SCOPE_SERIAL, 'Serial'), (SCOPE_LOT, 'Lot')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='quality_holds')
    operation_execution = models.ForeignKey(OperationExecution, null=True, blank=True, on_delete=models.CASCADE, related_name='quality_holds')
    inspection_execution = models.ForeignKey(InspectionExecution, null=True, blank=True, on_delete=models.CASCADE, related_name='quality_holds')
    serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='quality_holds')
    lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='quality_holds')
    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default=SCOPE_ORDER)
    reason = models.TextField()
    active = models.BooleanField(default=True)
    placed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='placed_quality_holds')
    placed_at = models.DateTimeField()
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='released_quality_holds')
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.TextField(blank=True)
    placed_event = models.OneToOneField(QualityEvent, on_delete=models.PROTECT, related_name='placed_hold')
    released_event = models.OneToOneField(QualityEvent, null=True, blank=True, on_delete=models.PROTECT, related_name='released_hold')

    class Meta:
        ordering = ['-placed_at']
        indexes = [models.Index(fields=['production_order', 'active', 'scope'])]

    def clean(self):
        super().clean()
        if self.operation_execution_id and self.operation_execution.production_order_id != self.production_order_id:
            raise ValidationError({'operation_execution': 'Hold operation belongs to a different order.'})
        if self.inspection_execution_id and self.inspection_execution.production_order_id != self.production_order_id:
            raise ValidationError({'inspection_execution': 'Hold inspection belongs to a different order.'})


class NonconformanceRecord(models.Model):
    STATUS_OPEN = 'OPEN'
    STATUS_UNDER_REVIEW = 'UNDER_REVIEW'
    STATUS_DISPOSITIONED = 'DISPOSITIONED'
    STATUS_CLOSED = 'CLOSED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_OPEN, 'Open'), (STATUS_UNDER_REVIEW, 'Under review'), (STATUS_DISPOSITIONED, 'Dispositioned'), (STATUS_CLOSED, 'Closed'), (STATUS_CANCELLED, 'Cancelled')]
    SEVERITY_MINOR = 'MINOR'
    SEVERITY_MAJOR = 'MAJOR'
    SEVERITY_CRITICAL = 'CRITICAL'
    SEVERITY_CHOICES = [(SEVERITY_MINOR, 'Minor'), (SEVERITY_MAJOR, 'Major'), (SEVERITY_CRITICAL, 'Critical')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ncr_number = models.CharField(max_length=80, unique=True)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='nonconformances')
    operation_execution = models.ForeignKey(OperationExecution, null=True, blank=True, on_delete=models.CASCADE, related_name='nonconformances')
    inspection_execution = models.ForeignKey(InspectionExecution, null=True, blank=True, on_delete=models.PROTECT, related_name='nonconformances')
    measurement = models.ForeignKey(InspectionMeasurement, null=True, blank=True, on_delete=models.PROTECT, related_name='nonconformances')
    serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='nonconformances')
    lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='nonconformances')
    defect_code = models.CharField(max_length=80)
    defect_description = models.TextField()
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default=SEVERITY_MINOR)
    affected_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=1)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_OPEN)
    ncr_version = models.PositiveBigIntegerField(default=0)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='owned_nonconformances')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_nonconformances')
    quality_event = models.OneToOneField(QualityEvent, null=True, blank=True, on_delete=models.PROTECT, related_name='ncr_record')
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['production_order', 'status']), models.Index(fields=['operation_execution', 'status'])]

    def clean(self):
        super().clean()
        self.ncr_number = (self.ncr_number or '').strip().upper()
        self.defect_code = (self.defect_code or '').strip().upper()
        if self.affected_quantity <= 0:
            raise ValidationError({'affected_quantity': 'Affected quantity must be positive.'})
        if self.operation_execution_id and self.operation_execution.production_order_id != self.production_order_id:
            raise ValidationError({'operation_execution': 'NCR operation belongs to a different order.'})
        if self.inspection_execution_id and self.inspection_execution.production_order_id != self.production_order_id:
            raise ValidationError({'inspection_execution': 'NCR inspection belongs to a different order.'})
        if self.measurement_id and self.measurement.inspection_execution.production_order_id != self.production_order_id:
            raise ValidationError({'measurement': 'NCR measurement belongs to a different order.'})
        if self.serial_id and self.serial.production_order_id != self.production_order_id:
            raise ValidationError({'serial': 'NCR serial belongs to a different order.'})
        if self.lot_id and self.lot.production_order_id != self.production_order_id:
            raise ValidationError({'lot': 'NCR lot belongs to a different order.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class QualityDisposition(models.Model):
    TYPE_REWORK = 'REWORK'
    TYPE_SCRAP = 'SCRAP'
    TYPE_USE_AS_IS = 'USE_AS_IS'
    TYPE_RETURN_TO_PROCESS = 'RETURN_TO_PROCESS'
    TYPE_REINSPECT = 'REINSPECT'
    TYPE_CHOICES = [
        (TYPE_REWORK, 'Rework'),
        (TYPE_SCRAP, 'Scrap'),
        (TYPE_USE_AS_IS, 'Use as is'),
        (TYPE_RETURN_TO_PROCESS, 'Return to process'),
        (TYPE_REINSPECT, 'Reinspect'),
    ]
    STATUS_PROPOSED = 'PROPOSED'
    STATUS_APPROVED = 'APPROVED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_IMPLEMENTED = 'IMPLEMENTED'
    STATUS_CHOICES = [(STATUS_PROPOSED, 'Proposed'), (STATUS_APPROVED, 'Approved'), (STATUS_REJECTED, 'Rejected'), (STATUS_IMPLEMENTED, 'Implemented')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ncr = models.ForeignKey(NonconformanceRecord, on_delete=models.CASCADE, related_name='dispositions')
    disposition_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    reason = models.TextField()
    instructions = models.TextField(blank=True)
    target_rework_edge = models.ForeignKey(ManufacturingOperationPrecedence, null=True, blank=True, on_delete=models.PROTECT, related_name='quality_dispositions')
    instruction_document_revision = models.ForeignKey(ControlledDocumentRevision, null=True, blank=True, on_delete=models.PROTECT, related_name='quality_dispositions')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PROPOSED)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='proposed_quality_dispositions')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='approved_quality_dispositions')
    implemented_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='implemented_quality_dispositions')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    implemented_at = models.DateTimeField(null=True, blank=True)
    reinspection = models.ForeignKey(InspectionExecution, null=True, blank=True, on_delete=models.PROTECT, related_name='source_dispositions')
    rework_cycle = models.ForeignKey(OperationExecutionCycle, null=True, blank=True, on_delete=models.PROTECT, related_name='quality_dispositions')

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'Disposition quantity must be positive.'})
        if self.ncr_id and self.quantity > self.ncr.affected_quantity:
            raise ValidationError({'quantity': 'Disposition quantity cannot exceed affected quantity.'})
        if self.target_rework_edge_id and self.target_rework_edge.production_order_id != self.ncr.production_order_id:
            raise ValidationError({'target_rework_edge': 'Rework target belongs to a different order.'})
        if self.disposition_type == self.TYPE_REWORK and not self.target_rework_edge_id:
            raise ValidationError({'target_rework_edge': 'Rework disposition requires a REWORK target.'})
        if self.target_rework_edge_id and not self.target_rework_edge.rework:
            raise ValidationError({'target_rework_edge': 'Target edge must be a REWORK path.'})
        if self.instruction_document_revision_id and self.instruction_document_revision.status != ControlledDocumentRevision.STATUS_RELEASED:
            raise ValidationError({'instruction_document_revision': 'Disposition instructions must reference a released document revision.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Warehouse(models.Model):
    TYPE_GENERAL = 'GENERAL'
    TYPE_PRODUCTION = 'PRODUCTION'
    TYPE_RECEIVING = 'RECEIVING'
    TYPE_QUARANTINE = 'QUARANTINE'
    TYPE_SCRAP = 'SCRAP'
    TYPE_TOOLING = 'TOOLING'
    TYPE_OTHER = 'OTHER'
    TYPE_CHOICES = [
        (TYPE_GENERAL, 'General'),
        (TYPE_PRODUCTION, 'Production'),
        (TYPE_RECEIVING, 'Receiving'),
        (TYPE_QUARANTINE, 'Quarantine'),
        (TYPE_SCRAP, 'Scrap'),
        (TYPE_TOOLING, 'Tooling'),
        (TYPE_OTHER, 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    plant = models.ForeignKey(Plant, null=True, blank=True, on_delete=models.PROTECT, related_name='warehouses')
    warehouse_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_GENERAL)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']
        indexes = [models.Index(fields=['active', 'warehouse_type'])]

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        if not self.code:
            raise ValidationError({'code': 'Warehouse code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class StorageLocation(models.Model):
    TYPE_ZONE = 'ZONE'
    TYPE_AISLE = 'AISLE'
    TYPE_RACK = 'RACK'
    TYPE_BIN = 'BIN'
    TYPE_FLOOR = 'FLOOR'
    TYPE_STAGING = 'STAGING'
    TYPE_QUARANTINE = 'QUARANTINE'
    TYPE_SCRAP = 'SCRAP'
    TYPE_CHOICES = [
        (TYPE_ZONE, 'Zone'),
        (TYPE_AISLE, 'Aisle'),
        (TYPE_RACK, 'Rack'),
        (TYPE_BIN, 'Bin'),
        (TYPE_FLOOR, 'Floor'),
        (TYPE_STAGING, 'Staging'),
        (TYPE_QUARANTINE, 'Quarantine'),
        (TYPE_SCRAP, 'Scrap'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='locations')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    location_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_BIN)
    active = models.BooleanField(default=True)
    inventory_enabled = models.BooleanField(default=True)
    quarantine = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['warehouse__code', 'code']
        unique_together = [('warehouse', 'code')]
        indexes = [models.Index(fields=['warehouse', 'active', 'inventory_enabled'])]

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        if not self.code:
            raise ValidationError({'code': 'Storage location code is required.'})
        if self.parent_id:
            if self.pk and self.parent_id == self.pk:
                raise ValidationError({'parent': 'Location cannot be its own parent.'})
            if self.parent.warehouse_id != self.warehouse_id:
                raise ValidationError({'parent': 'Parent location must belong to the same warehouse.'})
            ancestor = self.parent
            seen = set()
            while ancestor:
                if ancestor.pk in seen or (self.pk and ancestor.pk == self.pk):
                    raise ValidationError({'parent': 'Circular storage-location hierarchy is not allowed.'})
                seen.add(ancestor.pk)
                ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.warehouse.code}/{self.code}'


class InventoryBalance(models.Model):
    STATUS_AVAILABLE = 'AVAILABLE'
    STATUS_QUARANTINED = 'QUARANTINED'
    STATUS_BLOCKED = 'BLOCKED'
    STATUS_SCRAP = 'SCRAP'
    STATUS_IN_TRANSIT = 'IN_TRANSIT'
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, 'Available'),
        (STATUS_QUARANTINED, 'Quarantined'),
        (STATUS_BLOCKED, 'Blocked'),
        (STATUS_SCRAP, 'Scrap'),
        (STATUS_IN_TRANSIT, 'In transit'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='inventory_balances')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='inventory_balances')
    location = models.ForeignKey(StorageLocation, on_delete=models.PROTECT, related_name='inventory_balances')
    lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_balances')
    serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_balances')
    stock_status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    ownership = models.CharField(max_length=80, blank=True)
    unit = models.CharField(max_length=30, default='EA')
    on_hand_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    reserved_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    balance_version = models.PositiveBigIntegerField(default=0)
    last_transaction = models.ForeignKey('InventoryTransaction', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['item_revision__item__item_code', 'warehouse__code', 'location__code', 'stock_status']
        unique_together = [('item_revision', 'warehouse', 'location', 'lot', 'serial', 'stock_status', 'ownership')]
        indexes = [
            models.Index(fields=['item_revision', 'stock_status']),
            models.Index(fields=['warehouse', 'location', 'stock_status']),
            models.Index(fields=['serial', 'stock_status']),
        ]

    @property
    def available_quantity(self):
        if self.stock_status != self.STATUS_AVAILABLE:
            return Decimal('0')
        return self.on_hand_quantity - self.reserved_quantity

    def clean(self):
        super().clean()
        if self.location_id and self.warehouse_id and self.location.warehouse_id != self.warehouse_id:
            raise ValidationError({'location': 'Location belongs to a different warehouse.'})
        if self.on_hand_quantity < 0 or self.reserved_quantity < 0:
            raise ValidationError({'quantity': 'Inventory balance quantities cannot be negative.'})
        if self.reserved_quantity > self.on_hand_quantity:
            raise ValidationError({'reserved_quantity': 'Reserved quantity cannot exceed on-hand quantity.'})
        if self.lot_id and self.lot.item_revision_id != self.item_revision_id:
            raise ValidationError({'lot': 'Lot item revision must match stock item revision.'})
        if self.serial_id:
            if self.serial.item_revision_id != self.item_revision_id:
                raise ValidationError({'serial': 'Serial item revision must match stock item revision.'})
            if self.on_hand_quantity not in {Decimal('0'), Decimal('1')}:
                raise ValidationError({'on_hand_quantity': 'Serial-controlled stock quantity must be 0 or 1.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InventoryTransaction(models.Model):
    TYPE_RECEIPT = 'RECEIPT'
    TYPE_TRANSFER = 'TRANSFER'
    TYPE_RESERVATION = 'RESERVATION'
    TYPE_RESERVATION_RELEASE = 'RESERVATION_RELEASE'
    TYPE_PRODUCTION_ISSUE = 'PRODUCTION_ISSUE'
    TYPE_PRODUCTION_RETURN = 'PRODUCTION_RETURN'
    TYPE_CONSUMPTION_RECONCILIATION = 'CONSUMPTION_RECONCILIATION'
    TYPE_ADJUSTMENT_IN = 'ADJUSTMENT_IN'
    TYPE_ADJUSTMENT_OUT = 'ADJUSTMENT_OUT'
    TYPE_QUARANTINE = 'QUARANTINE'
    TYPE_QUALITY_RELEASE = 'QUALITY_RELEASE'
    TYPE_SCRAP = 'SCRAP'
    TYPE_REVERSAL = 'REVERSAL'
    TYPE_CHOICES = [
        (TYPE_RECEIPT, 'Receipt'),
        (TYPE_TRANSFER, 'Transfer'),
        (TYPE_RESERVATION, 'Reservation'),
        (TYPE_RESERVATION_RELEASE, 'Reservation release'),
        (TYPE_PRODUCTION_ISSUE, 'Production issue'),
        (TYPE_PRODUCTION_RETURN, 'Production return'),
        (TYPE_CONSUMPTION_RECONCILIATION, 'Consumption reconciliation'),
        (TYPE_ADJUSTMENT_IN, 'Adjustment in'),
        (TYPE_ADJUSTMENT_OUT, 'Adjustment out'),
        (TYPE_QUARANTINE, 'Quarantine'),
        (TYPE_QUALITY_RELEASE, 'Quality release'),
        (TYPE_SCRAP, 'Scrap'),
        (TYPE_REVERSAL, 'Reversal'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction_number = models.CharField(max_length=80, unique=True)
    transaction_type = models.CharField(max_length=40, choices=TYPE_CHOICES)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='inventory_transactions')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30)
    source_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='source_inventory_transactions')
    source_location = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.PROTECT, related_name='source_inventory_transactions')
    destination_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='destination_inventory_transactions')
    destination_location = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.PROTECT, related_name='destination_inventory_transactions')
    source_lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='source_inventory_transactions')
    source_serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='source_inventory_transactions')
    destination_lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='destination_inventory_transactions')
    destination_serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='destination_inventory_transactions')
    source_stock_status = models.CharField(max_length=30, choices=InventoryBalance.STATUS_CHOICES, blank=True)
    destination_stock_status = models.CharField(max_length=30, choices=InventoryBalance.STATUS_CHOICES, blank=True)
    ownership = models.CharField(max_length=80, blank=True)
    production_order = models.ForeignKey(ProductionOrder, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_transactions')
    operation_execution = models.ForeignKey(OperationExecution, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_transactions')
    material_requirement = models.ForeignKey(ProductionMaterialRequirement, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_transactions')
    material_consumption = models.ForeignKey(ProductionMaterialConsumption, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_transactions')
    quality_hold = models.ForeignKey(QualityHold, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_transactions')
    nonconformance = models.ForeignKey(NonconformanceRecord, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_transactions')
    disposition = models.ForeignKey(QualityDisposition, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_transactions')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='inventory_transactions')
    event_timestamp = models.DateTimeField()
    server_recorded_at = models.DateTimeField(auto_now_add=True)
    command_scope = models.CharField(max_length=80)
    idempotency_key = models.CharField(max_length=120)
    reversal_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='reversals')
    reason_code = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-server_recorded_at', '-transaction_number']
        unique_together = [('command_scope', 'idempotency_key')]
        indexes = [
            models.Index(fields=['transaction_type', 'server_recorded_at']),
            models.Index(fields=['production_order', 'transaction_type']),
            models.Index(fields=['material_requirement', 'transaction_type']),
            models.Index(fields=['source_warehouse', 'source_location']),
            models.Index(fields=['destination_warehouse', 'destination_location']),
        ]

    def clean(self):
        super().clean()
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'Inventory transaction quantity must be positive.'})
        if self.source_location_id and self.source_warehouse_id and self.source_location.warehouse_id != self.source_warehouse_id:
            raise ValidationError({'source_location': 'Source location belongs to a different warehouse.'})
        if self.destination_location_id and self.destination_warehouse_id and self.destination_location.warehouse_id != self.destination_warehouse_id:
            raise ValidationError({'destination_location': 'Destination location belongs to a different warehouse.'})

    def save(self, *args, **kwargs):
        if self.pk and InventoryTransaction.objects.filter(pk=self.pk).exists():
            raise ValidationError({'transaction': 'Inventory transactions are append-only and immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'transaction': 'Inventory transactions cannot be deleted.'})


class InventoryReservation(models.Model):
    STATUS_ACTIVE = 'ACTIVE'
    STATUS_PARTIALLY_ISSUED = 'PARTIALLY_ISSUED'
    STATUS_FULLY_ISSUED = 'FULLY_ISSUED'
    STATUS_RELEASED = 'RELEASED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_PARTIALLY_ISSUED, 'Partially issued'),
        (STATUS_FULLY_ISSUED, 'Fully issued'),
        (STATUS_RELEASED, 'Released'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name='inventory_reservations')
    material_requirement = models.ForeignKey(ProductionMaterialRequirement, on_delete=models.PROTECT, related_name='inventory_reservations')
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='inventory_reservations')
    balance = models.ForeignKey(InventoryBalance, on_delete=models.PROTECT, related_name='reservations')
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_reservations')
    location = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_reservations')
    lot = models.ForeignKey(ProductionLot, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_reservations')
    serial = models.ForeignKey(ProductionSerial, null=True, blank=True, on_delete=models.PROTECT, related_name='inventory_reservations')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    released_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    issued_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    unit = models.CharField(max_length=30)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    reservation_version = models.PositiveBigIntegerField(default=0)
    reserved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='inventory_reservations')
    reserved_at = models.DateTimeField()
    last_transaction = models.ForeignKey(InventoryTransaction, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['production_order__order_number', 'material_requirement__sequence', 'created_at']
        indexes = [models.Index(fields=['production_order', 'status']), models.Index(fields=['material_requirement', 'status']), models.Index(fields=['balance', 'status'])]

    @property
    def open_quantity(self):
        return self.quantity - self.released_quantity - self.issued_quantity

    def clean(self):
        super().clean()
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'Reservation quantity must be positive.'})
        if self.released_quantity < 0 or self.issued_quantity < 0:
            raise ValidationError({'quantity': 'Reservation quantities cannot be negative.'})
        if self.released_quantity + self.issued_quantity > self.quantity:
            raise ValidationError({'quantity': 'Released plus issued quantity cannot exceed reserved quantity.'})
        if self.material_requirement_id and self.production_order_id and self.material_requirement.production_order_id != self.production_order_id:
            raise ValidationError({'material_requirement': 'Material requirement belongs to a different order.'})
        if self.material_requirement_id and self.item_revision_id != self.material_requirement.component_item_revision_id:
            raise ValidationError({'item_revision': 'Reservation item revision must match material requirement.'})
        if self.balance_id and self.balance.item_revision_id != self.item_revision_id:
            raise ValidationError({'balance': 'Balance item revision must match reservation.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PlanningCalendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    timezone = models.CharField(max_length=80, default='UTC')
    working_weekdays = models.JSONField(default=list, blank=True)
    holidays = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        if not self.code:
            raise ValidationError({'code': 'Planning calendar code is required.'})
        weekdays = self.working_weekdays or [0, 1, 2, 3, 4]
        if not all(isinstance(day, int) and 0 <= day <= 6 for day in weekdays):
            raise ValidationError({'working_weekdays': 'Working weekdays must be integers 0 through 6.'})

    def save(self, *args, **kwargs):
        if not self.working_weekdays:
            self.working_weekdays = [0, 1, 2, 3, 4]
        self.full_clean()
        super().save(*args, **kwargs)


class ItemPlanningPolicy(models.Model):
    METHOD_MRP = 'MRP'
    METHOD_REORDER_POINT = 'REORDER_POINT'
    METHOD_MANUAL = 'MANUAL'
    METHOD_NOT_PLANNED = 'NOT_PLANNED'
    METHOD_CHOICES = [(METHOD_MRP, 'MRP'), (METHOD_REORDER_POINT, 'Reorder point'), (METHOD_MANUAL, 'Manual'), (METHOD_NOT_PLANNED, 'Not planned')]
    PROCUREMENT_MAKE = 'MAKE'
    PROCUREMENT_BUY = 'BUY'
    PROCUREMENT_TRANSFER = 'TRANSFER'
    PROCUREMENT_PHANTOM = 'PHANTOM'
    PROCUREMENT_MANUAL = 'MANUAL'
    PROCUREMENT_CHOICES = [(PROCUREMENT_MAKE, 'Make'), (PROCUREMENT_BUY, 'Buy'), (PROCUREMENT_TRANSFER, 'Transfer'), (PROCUREMENT_PHANTOM, 'Phantom'), (PROCUREMENT_MANUAL, 'Manual')]
    LOT_FOR_LOT = 'LOT_FOR_LOT'
    LOT_FIXED = 'FIXED_QUANTITY'
    LOT_MINIMUM = 'MINIMUM_QUANTITY'
    LOT_MULTIPLE = 'ORDER_MULTIPLE'
    LOT_CHOICES = [(LOT_FOR_LOT, 'Lot for lot'), (LOT_FIXED, 'Fixed quantity'), (LOT_MINIMUM, 'Minimum quantity'), (LOT_MULTIPLE, 'Order multiple')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item_revision = models.OneToOneField('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='planning_policy')
    planning_method = models.CharField(max_length=30, choices=METHOD_CHOICES, default=METHOD_MRP)
    procurement_type = models.CharField(max_length=30, choices=PROCUREMENT_CHOICES, default=PROCUREMENT_MAKE)
    default_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='planning_policies')
    safety_stock = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    minimum_stock = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    reorder_point = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    pre_processing_lead_days = models.PositiveIntegerField(default=0)
    supply_lead_days = models.PositiveIntegerField(default=0)
    post_processing_lead_days = models.PositiveIntegerField(default=0)
    safety_lead_days = models.PositiveIntegerField(default=0)
    variable_lead_days_per_unit = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    lot_sizing_method = models.CharField(max_length=30, choices=LOT_CHOICES, default=LOT_FOR_LOT)
    minimum_order_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    maximum_order_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    order_multiple = models.DecimalField(max_digits=18, decimal_places=6, default=1)
    fixed_lot_quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    planning_time_fence_days = models.PositiveIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['item_revision__item__item_code']

    def clean(self):
        super().clean()
        for field in ('safety_stock', 'minimum_stock', 'minimum_order_quantity', 'order_multiple'):
            if getattr(self, field) < 0:
                raise ValidationError({field: 'Planning quantities cannot be negative.'})
        if self.order_multiple <= 0:
            raise ValidationError({'order_multiple': 'Order multiple must be positive.'})
        if self.lot_sizing_method == self.LOT_FIXED and not self.fixed_lot_quantity:
            raise ValidationError({'fixed_lot_quantity': 'Fixed lot sizing requires a fixed quantity.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PlanningDemand(models.Model):
    TYPE_PRODUCTION_REQUIREMENT = 'PRODUCTION_REQUIREMENT'
    TYPE_FORECAST = 'FORECAST'
    TYPE_CUSTOMER_REQUIREMENT = 'CUSTOMER_REQUIREMENT'
    TYPE_MANUAL = 'MANUAL'
    TYPE_SAFETY_STOCK = 'SAFETY_STOCK'
    TYPE_REPLACEMENT = 'REPLACEMENT'
    TYPE_OTHER = 'OTHER'
    TYPE_CHOICES = [(TYPE_PRODUCTION_REQUIREMENT, 'Production requirement'), (TYPE_FORECAST, 'Forecast'), (TYPE_CUSTOMER_REQUIREMENT, 'Customer requirement'), (TYPE_MANUAL, 'Manual'), (TYPE_SAFETY_STOCK, 'Safety stock'), (TYPE_REPLACEMENT, 'Replacement'), (TYPE_OTHER, 'Other')]
    STATUS_DRAFT = 'DRAFT'
    STATUS_APPROVED = 'APPROVED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_FULFILLED = 'FULFILLED'
    STATUS_EXPIRED = 'EXPIRED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_APPROVED, 'Approved'), (STATUS_CANCELLED, 'Cancelled'), (STATUS_FULFILLED, 'Fulfilled'), (STATUS_EXPIRED, 'Expired')]
    PRIORITY_LOW = 'LOW'
    PRIORITY_NORMAL = 'NORMAL'
    PRIORITY_HIGH = 'HIGH'
    PRIORITY_URGENT = 'URGENT'
    PRIORITY_CHOICES = ProductionOrder.PRIORITY_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    demand_number = models.CharField(max_length=80, unique=True, blank=True)
    demand_type = models.CharField(max_length=40, choices=TYPE_CHOICES, default=TYPE_MANUAL)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='planning_demands')
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='planning_demands')
    plant = models.ForeignKey(Plant, null=True, blank=True, on_delete=models.PROTECT, related_name='planning_demands')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30, default='EA')
    required_date = models.DateField()
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    source_reference = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    demand_version = models.PositiveBigIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_planning_demands')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='approved_planning_demands')
    approved_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='cancelled_planning_demands')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['required_date', 'priority', 'demand_number']
        indexes = [models.Index(fields=['status', 'required_date']), models.Index(fields=['item_revision', 'required_date'])]

    def clean(self):
        super().clean()
        self.demand_number = (self.demand_number or '').strip().upper()
        self.unit = (self.unit or 'EA').strip().upper()
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'Demand quantity must be positive.'})
        if not self.warehouse_id and not self.plant_id:
            raise ValidationError({'scope': 'Demand requires a warehouse or plant scope.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ScheduledSupply(models.Model):
    TYPE_PURCHASE = 'PURCHASE'
    TYPE_PRODUCTION = 'PRODUCTION'
    TYPE_TRANSFER = 'TRANSFER'
    TYPE_MANUAL = 'MANUAL'
    TYPE_CHOICES = [(TYPE_PURCHASE, 'Purchase'), (TYPE_PRODUCTION, 'Production'), (TYPE_TRANSFER, 'Transfer'), (TYPE_MANUAL, 'Manual')]
    STATUS_DRAFT = 'DRAFT'
    STATUS_APPROVED = 'APPROVED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_RECEIVED = 'RECEIVED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_APPROVED, 'Approved'), (STATUS_CANCELLED, 'Cancelled'), (STATUS_RECEIVED, 'Received')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supply_number = models.CharField(max_length=80, unique=True, blank=True)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='scheduled_supplies')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30, default='EA')
    expected_date = models.DateField()
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='scheduled_supplies')
    supply_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_MANUAL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    firm = models.BooleanField(default=True)
    source_reference = models.CharField(max_length=160, blank=True)
    supply_version = models.PositiveBigIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_scheduled_supplies')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['expected_date', 'supply_number']
        indexes = [models.Index(fields=['status', 'expected_date']), models.Index(fields=['item_revision', 'expected_date'])]

    def clean(self):
        super().clean()
        self.supply_number = (self.supply_number or '').strip().upper()
        self.unit = (self.unit or 'EA').strip().upper()
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'Supply quantity must be positive.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MRPRun(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_RUNNING = 'RUNNING'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_FAILED = 'FAILED'
    STATUS_SUPERSEDED = 'SUPERSEDED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_RUNNING, 'Running'), (STATUS_COMPLETED, 'Completed'), (STATUS_FAILED, 'Failed'), (STATUS_SUPERSEDED, 'Superseded'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run_number = models.CharField(max_length=80, unique=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    horizon_start = models.DateField()
    horizon_end = models.DateField()
    cutoff_timestamp = models.DateTimeField()
    planning_calendar = models.ForeignKey(PlanningCalendar, null=True, blank=True, on_delete=models.PROTECT, related_name='mrp_runs')
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='mrp_runs')
    plant = models.ForeignKey(Plant, null=True, blank=True, on_delete=models.PROTECT, related_name='mrp_runs')
    policy_version = models.CharField(max_length=80, default='mrp-policy-v1')
    input_checksum = models.CharField(max_length=64, blank=True)
    result_checksum = models.CharField(max_length=64, blank=True)
    parameter_snapshot = models.JSONField(default=dict, blank=True)
    freshness_snapshot = models.JSONField(default=dict, blank=True)
    error_summary = models.TextField(blank=True)
    run_version = models.PositiveBigIntegerField(default=0)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='mrp_runs')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'horizon_start', 'horizon_end'])]

    def clean(self):
        super().clean()
        if self.horizon_end < self.horizon_start:
            raise ValidationError({'horizon_end': 'Planning horizon end cannot precede start.'})

    def save(self, *args, **kwargs):
        if self.pk and MRPRun.objects.filter(pk=self.pk, status=self.STATUS_COMPLETED).exists():
            original = MRPRun.objects.get(pk=self.pk)
            mutable = {'status'}
            for field in self._meta.fields:
                if field.name not in mutable and getattr(original, field.name) != getattr(self, field.name):
                    raise ValidationError({'mrp_run': 'Completed MRP runs are immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)


class MRPDemandSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mrp_run = models.ForeignKey(MRPRun, on_delete=models.CASCADE, related_name='demand_snapshots')
    source_demand = models.ForeignKey(PlanningDemand, null=True, blank=True, on_delete=models.PROTECT, related_name='mrp_snapshots')
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='mrp_demand_snapshots')
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30)
    required_date = models.DateField()
    priority = models.CharField(max_length=20, default=PlanningDemand.PRIORITY_NORMAL)
    source_status = models.CharField(max_length=20, blank=True)
    source_version = models.PositiveBigIntegerField(default=0)
    source_metadata = models.JSONField(default=dict, blank=True)
    sequence = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['sequence', 'required_date']

    def save(self, *args, **kwargs):
        if self.pk and MRPDemandSnapshot.objects.filter(pk=self.pk).exists():
            raise ValidationError({'mrp_snapshot': 'MRP demand snapshots are immutable.'})
        super().save(*args, **kwargs)


class MRPSupplySnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mrp_run = models.ForeignKey(MRPRun, on_delete=models.CASCADE, related_name='supply_snapshots')
    source_type = models.CharField(max_length=40)
    source_id = models.CharField(max_length=80, blank=True)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='mrp_supply_snapshots')
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30)
    available_date = models.DateField()
    firm = models.BooleanField(default=True)
    source_status = models.CharField(max_length=40, blank=True)
    source_version = models.PositiveBigIntegerField(default=0)
    source_metadata = models.JSONField(default=dict, blank=True)
    sequence = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['sequence', 'available_date']

    def save(self, *args, **kwargs):
        if self.pk and MRPSupplySnapshot.objects.filter(pk=self.pk).exists():
            raise ValidationError({'mrp_snapshot': 'MRP supply snapshots are immutable.'})
        super().save(*args, **kwargs)


class MRPRequirement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mrp_run = models.ForeignKey(MRPRun, on_delete=models.CASCADE, related_name='requirements')
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='mrp_requirements')
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    bucket_date = models.DateField()
    gross_requirement = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    scheduled_receipt = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    projected_available = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    safety_stock = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    net_requirement = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    planned_receipt = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    planned_release = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    planned_release_date = models.DateField(null=True, blank=True)
    procurement_type = models.CharField(max_length=30, blank=True)
    bom_revision = models.ForeignKey('enterprise_items.BOMRevision', null=True, blank=True, on_delete=models.PROTECT, related_name='mrp_requirements')
    parent_requirement = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='dependent_requirements')
    demand_snapshot = models.ForeignKey(MRPDemandSnapshot, null=True, blank=True, on_delete=models.PROTECT, related_name='requirements')
    bom_path = models.JSONField(default=list, blank=True)
    exception_codes = models.JSONField(default=list, blank=True)
    sequence = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['sequence', 'item_revision__item__item_code', 'bucket_date']
        indexes = [models.Index(fields=['mrp_run', 'item_revision', 'bucket_date'])]

    def save(self, *args, **kwargs):
        if self.pk and MRPRequirement.objects.filter(pk=self.pk).exists():
            raise ValidationError({'mrp_requirement': 'MRP requirements are immutable.'})
        super().save(*args, **kwargs)


class MRPRecommendation(models.Model):
    TYPE_PRODUCTION = 'PRODUCTION'
    TYPE_PURCHASE_REQUISITION = 'PURCHASE_REQUISITION'
    TYPE_TRANSFER = 'TRANSFER'
    TYPE_RESCHEDULE_IN = 'RESCHEDULE_IN'
    TYPE_RESCHEDULE_OUT = 'RESCHEDULE_OUT'
    TYPE_CANCEL = 'CANCEL'
    TYPE_EXPEDITE = 'EXPEDITE'
    TYPE_CHOICES = [(TYPE_PRODUCTION, 'Production'), (TYPE_PURCHASE_REQUISITION, 'Purchase requisition'), (TYPE_TRANSFER, 'Transfer'), (TYPE_RESCHEDULE_IN, 'Reschedule in'), (TYPE_RESCHEDULE_OUT, 'Reschedule out'), (TYPE_CANCEL, 'Cancel'), (TYPE_EXPEDITE, 'Expedite')]
    STATUS_PLANNED = 'PLANNED'
    STATUS_REVIEWED = 'REVIEWED'
    STATUS_APPROVED = 'APPROVED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_CONVERTED = 'CONVERTED'
    STATUS_STALE = 'STALE'
    STATUS_CHOICES = [(STATUS_PLANNED, 'Planned'), (STATUS_REVIEWED, 'Reviewed'), (STATUS_APPROVED, 'Approved'), (STATUS_REJECTED, 'Rejected'), (STATUS_CONVERTED, 'Converted'), (STATUS_STALE, 'Stale')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mrp_run = models.ForeignKey(MRPRun, on_delete=models.CASCADE, related_name='recommendations')
    recommendation_number = models.CharField(max_length=80, unique=True)
    recommendation_type = models.CharField(max_length=40, choices=TYPE_CHOICES)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='mrp_recommendations')
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30)
    required_date = models.DateField()
    planned_receipt_date = models.DateField()
    planned_release_date = models.DateField()
    procurement_type = models.CharField(max_length=30)
    source_requirement = models.ForeignKey(MRPRequirement, null=True, blank=True, on_delete=models.PROTECT, related_name='recommendations')
    shortage_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    priority = models.CharField(max_length=20, default=PlanningDemand.PRIORITY_NORMAL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PLANNED)
    recommendation_version = models.PositiveBigIntegerField(default=0)
    explanation = models.TextField(blank=True)
    exception_codes = models.JSONField(default=list, blank=True)
    input_checksum = models.CharField(max_length=64, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='reviewed_mrp_recommendations')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='approved_mrp_recommendations')
    converted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='converted_mrp_recommendations')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    converted_at = models.DateTimeField(null=True, blank=True)
    converted_production_order = models.ForeignKey(ProductionOrder, null=True, blank=True, on_delete=models.PROTECT, related_name='source_mrp_recommendations')
    converted_purchase_requisition = models.ForeignKey('PurchaseRequisition', null=True, blank=True, on_delete=models.PROTECT, related_name='source_mrp_recommendations')

    class Meta:
        ordering = ['planned_release_date', 'recommendation_number']
        indexes = [models.Index(fields=['mrp_run', 'status']), models.Index(fields=['item_revision', 'status'])]


class MRPPegging(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mrp_run = models.ForeignKey(MRPRun, on_delete=models.CASCADE, related_name='pegging')
    recommendation = models.ForeignKey(MRPRecommendation, null=True, blank=True, on_delete=models.CASCADE, related_name='pegging')
    requirement = models.ForeignKey(MRPRequirement, on_delete=models.CASCADE, related_name='pegging')
    demand_snapshot = models.ForeignKey(MRPDemandSnapshot, null=True, blank=True, on_delete=models.PROTECT, related_name='pegging')
    parent_requirement = models.ForeignKey(MRPRequirement, null=True, blank=True, on_delete=models.PROTECT, related_name='child_pegging')
    bom_path = models.JSONField(default=list, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    sequence = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['sequence']

    def save(self, *args, **kwargs):
        if self.pk and MRPPegging.objects.filter(pk=self.pk).exists():
            raise ValidationError({'mrp_pegging': 'MRP pegging rows are immutable.'})
        super().save(*args, **kwargs)


class PurchaseRequisition(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requisition_number = models.CharField(max_length=80, unique=True)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='purchase_requisitions')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30)
    required_date = models.DateField()
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_requisitions')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    source_recommendation = models.OneToOneField(MRPRecommendation, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_requisition')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_purchase_requisitions')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'Requisition quantity must be positive.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Supplier(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_QUALIFIED = 'QUALIFIED'
    STATUS_SUSPENDED = 'SUSPENDED'
    STATUS_DISQUALIFIED = 'DISQUALIFIED'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_QUALIFIED, 'Qualified'),
        (STATUS_SUSPENDED, 'Suspended'),
        (STATUS_DISQUALIFIED, 'Disqualified'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier_code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)
    tax_identifier = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    qualification_score = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    risk_rating = models.CharField(max_length=40, default='MEDIUM')
    supplier_version = models.PositiveBigIntegerField(default=0)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_suppliers')
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_suppliers')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['supplier_code']
        indexes = [models.Index(fields=['status', 'supplier_code'])]

    def clean(self):
        super().clean()
        self.supplier_code = (self.supplier_code or '').strip().upper()
        if not self.supplier_code:
            raise ValidationError({'supplier_code': 'Supplier code is required.'})
        if self.qualification_score < 0:
            raise ValidationError({'qualification_score': 'Qualification score cannot be negative.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.supplier_code} - {self.name}'


class SupplierSite(models.Model):
    TYPE_HEADQUARTERS = 'HEADQUARTERS'
    TYPE_MANUFACTURING = 'MANUFACTURING'
    TYPE_WAREHOUSE = 'WAREHOUSE'
    TYPE_BILLING = 'BILLING'
    TYPE_CHOICES = [
        (TYPE_HEADQUARTERS, 'Headquarters'),
        (TYPE_MANUFACTURING, 'Manufacturing'),
        (TYPE_WAREHOUSE, 'Warehouse'),
        (TYPE_BILLING, 'Billing'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='sites')
    site_code = models.CharField(max_length=80)
    name = models.CharField(max_length=255)
    site_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_MANUFACTURING)
    address = models.TextField(blank=True)
    country = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['supplier__supplier_code', 'site_code']
        unique_together = [('supplier', 'site_code')]

    def clean(self):
        super().clean()
        self.site_code = (self.site_code or '').strip().upper()
        if not self.site_code:
            raise ValidationError({'site_code': 'Supplier site code is required.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SupplierContact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='contacts')
    site = models.ForeignKey(SupplierSite, null=True, blank=True, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=80, blank=True)
    primary = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.site_id and self.site.supplier_id != self.supplier_id:
            raise ValidationError({'site': 'Supplier contact site belongs to a different supplier.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SupplierCapability(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_APPROVED = 'APPROVED'
    STATUS_SUSPENDED = 'SUSPENDED'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_APPROVED, 'Approved'), (STATUS_SUSPENDED, 'Suspended')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='capabilities')
    site = models.ForeignKey(SupplierSite, null=True, blank=True, on_delete=models.CASCADE, related_name='capabilities')
    capability_code = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    process_definition = models.ForeignKey(ProcessDefinition, null=True, blank=True, on_delete=models.PROTECT, related_name='supplier_capabilities')
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', null=True, blank=True, on_delete=models.PROTECT, related_name='supplier_capabilities')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    lead_time_days = models.PositiveIntegerField(default=0)
    capacity_per_week = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_supplier_capabilities')
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['supplier__supplier_code', 'capability_code']
        unique_together = [('supplier', 'capability_code', 'item_revision')]

    def clean(self):
        super().clean()
        self.capability_code = (self.capability_code or '').strip().upper()
        if self.site_id and self.site.supplier_id != self.supplier_id:
            raise ValidationError({'site': 'Supplier capability site belongs to a different supplier.'})
        if self.capacity_per_week < 0:
            raise ValidationError({'capacity_per_week': 'Capability capacity cannot be negative.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ApprovedSupplierItem(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_APPROVED = 'APPROVED'
    STATUS_SUSPENDED = 'SUSPENDED'
    STATUS_EXPIRED = 'EXPIRED'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_APPROVED, 'Approved'), (STATUS_SUSPENDED, 'Suspended'), (STATUS_EXPIRED, 'Expired')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='approved_items')
    supplier_site = models.ForeignKey(SupplierSite, null=True, blank=True, on_delete=models.PROTECT, related_name='approved_items')
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='approved_suppliers')
    supplier_item_code = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    lead_time_days = models.PositiveIntegerField(default=0)
    min_order_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    currency = models.CharField(max_length=3, default='USD')
    last_unit_price = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    approval_evidence = models.JSONField(default=dict, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_supplier_items')
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['item_revision__item__item_code', 'supplier__supplier_code']
        unique_together = [('supplier', 'supplier_site', 'item_revision')]
        indexes = [models.Index(fields=['item_revision', 'status']), models.Index(fields=['supplier', 'status'])]

    def clean(self):
        super().clean()
        self.currency = (self.currency or 'USD').strip().upper()
        if self.supplier_site_id and self.supplier_site.supplier_id != self.supplier_id:
            raise ValidationError({'supplier_site': 'Approved item site belongs to a different supplier.'})
        if self.min_order_quantity < 0 or self.last_unit_price < 0:
            raise ValidationError({'price': 'Approved supplier item quantities and prices cannot be negative.'})
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValidationError({'valid_to': 'Valid-to date cannot precede valid-from date.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class RequestForQuotation(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_RELEASED = 'RELEASED'
    STATUS_CLOSED = 'CLOSED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_RELEASED, 'Released'), (STATUS_CLOSED, 'Closed'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rfq_number = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=255)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    currency = models.CharField(max_length=3, default='USD')
    due_date = models.DateField(null=True, blank=True)
    requisition = models.ForeignKey(PurchaseRequisition, null=True, blank=True, on_delete=models.PROTECT, related_name='rfqs')
    project_requirement = models.ForeignKey('ktcPlanning.ProjectProcurementRequirement', null=True, blank=True, on_delete=models.PROTECT, related_name='rfqs')
    wbs_activity = models.ForeignKey('ktcPlanning.Task', null=True, blank=True, on_delete=models.PROTECT, related_name='rfqs')
    project = models.ForeignKey('ktcPlanning.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='rfqs')
    technical_requirements = models.JSONField(default=dict, blank=True)
    commercial_terms = models.JSONField(default=dict, blank=True)
    rfq_version = models.PositiveBigIntegerField(default=0)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='released_rfqs')
    released_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_rfqs')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'due_date']), models.Index(fields=['project', 'status'])]

    def clean(self):
        super().clean()
        self.rfq_number = (self.rfq_number or '').strip().upper()
        self.currency = (self.currency or 'USD').strip().upper()
        if self.status != self.STATUS_DRAFT and self.pk:
            original = RequestForQuotation.objects.filter(pk=self.pk).values('status', 'currency', 'requisition_id', 'project_requirement_id').first()
            if original and original['status'] != self.STATUS_DRAFT:
                if original['currency'] != self.currency or original['requisition_id'] != self.requisition_id or original['project_requirement_id'] != self.project_requirement_id:
                    raise ValidationError({'rfq': 'Released RFQ sourcing context is immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class RFQLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rfq = models.ForeignKey(RequestForQuotation, on_delete=models.CASCADE, related_name='lines')
    line_number = models.PositiveIntegerField(default=1)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='rfq_lines')
    description = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30, default='EA')
    required_date = models.DateField(null=True, blank=True)
    delivery_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='rfq_lines')
    delivery_location = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.PROTECT, related_name='rfq_lines')
    source_requisition = models.ForeignKey(PurchaseRequisition, null=True, blank=True, on_delete=models.PROTECT, related_name='rfq_lines')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['rfq', 'line_number']
        unique_together = [('rfq', 'line_number')]

    def clean(self):
        super().clean()
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'RFQ line quantity must be positive.'})
        if self.delivery_location_id and self.delivery_warehouse_id and self.delivery_location.warehouse_id != self.delivery_warehouse_id:
            raise ValidationError({'delivery_location': 'RFQ delivery location belongs to a different warehouse.'})
        if self.rfq_id and self.rfq.status != RequestForQuotation.STATUS_DRAFT and self.pk:
            original = RFQLine.objects.filter(pk=self.pk).values('quantity', 'item_revision_id').first()
            if original and (original['quantity'] != self.quantity or original['item_revision_id'] != self.item_revision_id):
                raise ValidationError({'rfq_line': 'Released RFQ lines are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class RFQSupplierInvitation(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_SENT = 'SENT'
    STATUS_DECLINED = 'DECLINED'
    STATUS_RESPONDED = 'RESPONDED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_SENT, 'Sent'), (STATUS_DECLINED, 'Declined'), (STATUS_RESPONDED, 'Responded')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rfq = models.ForeignKey(RequestForQuotation, on_delete=models.CASCADE, related_name='invitations')
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='rfq_invitations')
    supplier_site = models.ForeignKey(SupplierSite, null=True, blank=True, on_delete=models.PROTECT, related_name='rfq_invitations')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    invited_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    contact = models.ForeignKey(SupplierContact, null=True, blank=True, on_delete=models.PROTECT, related_name='rfq_invitations')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['rfq', 'supplier__supplier_code']
        unique_together = [('rfq', 'supplier')]

    def clean(self):
        super().clean()
        if self.supplier_site_id and self.supplier_site.supplier_id != self.supplier_id:
            raise ValidationError({'supplier_site': 'RFQ invitation site belongs to a different supplier.'})
        if self.contact_id and self.contact.supplier_id != self.supplier_id:
            raise ValidationError({'contact': 'RFQ invitation contact belongs to a different supplier.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SupplierQuotation(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_SUBMITTED = 'SUBMITTED'
    STATUS_WITHDRAWN = 'WITHDRAWN'
    STATUS_AWARDED = 'AWARDED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_SUBMITTED, 'Submitted'), (STATUS_WITHDRAWN, 'Withdrawn'), (STATUS_AWARDED, 'Awarded'), (STATUS_REJECTED, 'Rejected')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quotation_number = models.CharField(max_length=80, unique=True)
    rfq = models.ForeignKey(RequestForQuotation, on_delete=models.PROTECT, related_name='quotations')
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='quotations')
    supplier_site = models.ForeignKey(SupplierSite, null=True, blank=True, on_delete=models.PROTECT, related_name='quotations')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    currency = models.CharField(max_length=3, default='USD')
    submitted_at = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=255, blank=True)
    incoterms = models.CharField(max_length=80, blank=True)
    technical_score = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    commercial_score = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    quotation_version = models.PositiveBigIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_supplier_quotations')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('rfq', 'supplier')]
        indexes = [models.Index(fields=['rfq', 'status']), models.Index(fields=['supplier', 'status'])]

    def clean(self):
        super().clean()
        self.quotation_number = (self.quotation_number or '').strip().upper()
        self.currency = (self.currency or 'USD').strip().upper()
        if self.supplier_site_id and self.supplier_site.supplier_id != self.supplier_id:
            raise ValidationError({'supplier_site': 'Quotation site belongs to a different supplier.'})
        if self.technical_score < 0 or self.commercial_score < 0:
            raise ValidationError({'score': 'Quotation scores cannot be negative.'})
        if self.pk:
            original = SupplierQuotation.objects.filter(pk=self.pk).values('status', 'supplier_id', 'rfq_id', 'currency').first()
            if original and original['status'] != self.STATUS_DRAFT:
                if original['supplier_id'] != self.supplier_id or original['rfq_id'] != self.rfq_id or original['currency'] != self.currency:
                    raise ValidationError({'quotation': 'Submitted quotation sourcing context is immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SupplierQuotationLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quotation = models.ForeignKey(SupplierQuotation, on_delete=models.CASCADE, related_name='lines')
    rfq_line = models.ForeignKey(RFQLine, on_delete=models.PROTECT, related_name='quotation_lines')
    line_number = models.PositiveIntegerField(default=1)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit_price = models.DecimalField(max_digits=18, decimal_places=6)
    lead_time_days = models.PositiveIntegerField(default=0)
    promised_date = models.DateField(null=True, blank=True)
    technical_compliance = models.CharField(max_length=40, default='COMPLIANT')
    commercial_rank = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['quotation', 'line_number']
        unique_together = [('quotation', 'rfq_line')]

    @property
    def extended_price(self):
        return self.quantity * self.unit_price

    def clean(self):
        super().clean()
        if self.quantity <= 0 or self.unit_price < 0:
            raise ValidationError({'price': 'Quotation line quantity must be positive and price cannot be negative.'})
        if self.quotation_id and self.rfq_line_id and self.rfq_line.rfq_id != self.quotation.rfq_id:
            raise ValidationError({'rfq_line': 'Quotation line must belong to the quoted RFQ.'})
        if self.quotation_id and self.quotation.status != SupplierQuotation.STATUS_DRAFT and self.pk:
            original = SupplierQuotationLine.objects.filter(pk=self.pk).values('quantity', 'unit_price').first()
            if original and (original['quantity'] != self.quantity or original['unit_price'] != self.unit_price):
                raise ValidationError({'quotation_line': 'Submitted quotation lines are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class QuotationComparisonSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rfq = models.ForeignKey(RequestForQuotation, on_delete=models.PROTECT, related_name='comparison_snapshots')
    snapshot_number = models.CharField(max_length=80, unique=True)
    policy_version = models.CharField(max_length=80, default='quotation-comparison-v1')
    technical_matrix = models.JSONField(default=list, blank=True)
    commercial_matrix = models.JSONField(default=list, blank=True)
    recommended_supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name='recommended_comparisons')
    recommended_quotation = models.ForeignKey(SupplierQuotation, null=True, blank=True, on_delete=models.PROTECT, related_name='recommended_comparisons')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='quotation_comparison_snapshots')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['rfq', 'policy_version'])]

    def save(self, *args, **kwargs):
        if self.pk and QuotationComparisonSnapshot.objects.filter(pk=self.pk).exists():
            raise ValidationError({'snapshot': 'Quotation comparison snapshots are immutable.'})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'snapshot': 'Quotation comparison snapshots cannot be deleted.'})


class SourcingDecision(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_APPROVED = 'APPROVED'
    STATUS_CONVERTED = 'CONVERTED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_APPROVED, 'Approved'), (STATUS_CONVERTED, 'Converted'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    decision_number = models.CharField(max_length=80, unique=True)
    rfq = models.ForeignKey(RequestForQuotation, null=True, blank=True, on_delete=models.PROTECT, related_name='sourcing_decisions')
    comparison_snapshot = models.ForeignKey(QuotationComparisonSnapshot, null=True, blank=True, on_delete=models.PROTECT, related_name='sourcing_decisions')
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='sourcing_decisions')
    quotation = models.ForeignKey(SupplierQuotation, null=True, blank=True, on_delete=models.PROTECT, related_name='sourcing_decisions')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    decision_reason = models.TextField(blank=True)
    project = models.ForeignKey('ktcPlanning.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='sourcing_decisions')
    procurement_requirement = models.ForeignKey('ktcPlanning.ProjectProcurementRequirement', null=True, blank=True, on_delete=models.PROTECT, related_name='sourcing_decisions')
    decision_version = models.PositiveBigIntegerField(default=0)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_sourcing_decisions')
    approved_at = models.DateTimeField(null=True, blank=True)
    converted_purchase_order = models.ForeignKey('PurchaseOrder', null=True, blank=True, on_delete=models.PROTECT, related_name='source_decisions')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_sourcing_decisions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'supplier']), models.Index(fields=['project', 'status'])]

    def clean(self):
        super().clean()
        self.decision_number = (self.decision_number or '').strip().upper()
        if self.quotation_id and self.quotation.supplier_id != self.supplier_id:
            raise ValidationError({'quotation': 'Decision quotation belongs to a different supplier.'})
        if self.rfq_id and self.quotation_id and self.quotation.rfq_id != self.rfq_id:
            raise ValidationError({'quotation': 'Decision quotation belongs to a different RFQ.'})
        if self.pk:
            original = SourcingDecision.objects.filter(pk=self.pk).values('status', 'supplier_id', 'quotation_id', 'rfq_id').first()
            if original and original['status'] in {self.STATUS_APPROVED, self.STATUS_CONVERTED}:
                if original['supplier_id'] != self.supplier_id or original['quotation_id'] != self.quotation_id or original['rfq_id'] != self.rfq_id:
                    raise ValidationError({'decision': 'Approved sourcing decision award context is immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SourcingDecisionLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    decision = models.ForeignKey(SourcingDecision, on_delete=models.CASCADE, related_name='lines')
    quotation_line = models.ForeignKey(SupplierQuotationLine, null=True, blank=True, on_delete=models.PROTECT, related_name='sourcing_decision_lines')
    rfq_line = models.ForeignKey(RFQLine, null=True, blank=True, on_delete=models.PROTECT, related_name='sourcing_decision_lines')
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='sourcing_decision_lines')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30, default='EA')
    unit_price = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    currency = models.CharField(max_length=3, default='USD')
    promised_date = models.DateField(null=True, blank=True)
    delivery_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='sourcing_decision_lines')
    delivery_location = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.PROTECT, related_name='sourcing_decision_lines')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['decision', 'created_at']

    def clean(self):
        super().clean()
        self.currency = (self.currency or 'USD').strip().upper()
        if self.quantity <= 0 or self.unit_price < 0:
            raise ValidationError({'quantity': 'Sourcing decision line quantity must be positive and price cannot be negative.'})
        if self.delivery_location_id and self.delivery_warehouse_id and self.delivery_location.warehouse_id != self.delivery_warehouse_id:
            raise ValidationError({'delivery_location': 'Sourcing delivery location belongs to a different warehouse.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PurchaseOrder(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_APPROVED = 'APPROVED'
    STATUS_RELEASED = 'RELEASED'
    STATUS_ACKNOWLEDGED = 'ACKNOWLEDGED'
    STATUS_CONFIRMED = 'CONFIRMED'
    STATUS_PARTIALLY_RECEIVED = 'PARTIALLY_RECEIVED'
    STATUS_RECEIVED = 'RECEIVED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CLOSED = 'CLOSED'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_RELEASED, 'Released'),
        (STATUS_ACKNOWLEDGED, 'Acknowledged'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_PARTIALLY_RECEIVED, 'Partially received'),
        (STATUS_RECEIVED, 'Received'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_CLOSED, 'Closed'),
    ]
    LOCKED_STATUSES = {STATUS_RELEASED, STATUS_ACKNOWLEDGED, STATUS_CONFIRMED, STATUS_PARTIALLY_RECEIVED, STATUS_RECEIVED, STATUS_CLOSED}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    po_number = models.CharField(max_length=80, unique=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchase_orders')
    supplier_site = models.ForeignKey(SupplierSite, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_orders')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    currency = models.CharField(max_length=3, default='USD')
    sourcing_decision = models.ForeignKey(SourcingDecision, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_orders')
    quotation = models.ForeignKey(SupplierQuotation, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_orders')
    project = models.ForeignKey('ktcPlanning.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_orders')
    wbs_activity = models.ForeignKey('ktcPlanning.Task', null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_orders')
    expected_delivery_date = models.DateField(null=True, blank=True)
    supplier_reference = models.CharField(max_length=120, blank=True)
    payment_terms = models.CharField(max_length=255, blank=True)
    incoterms = models.CharField(max_length=80, blank=True)
    po_version = models.PositiveBigIntegerField(default=0)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_purchase_orders')
    approved_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='released_purchase_orders')
    released_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_purchase_orders')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['supplier', 'status']), models.Index(fields=['project', 'status'])]

    def clean(self):
        super().clean()
        self.po_number = (self.po_number or '').strip().upper()
        self.currency = (self.currency or 'USD').strip().upper()
        if self.supplier_site_id and self.supplier_site.supplier_id != self.supplier_id:
            raise ValidationError({'supplier_site': 'PO site belongs to a different supplier.'})
        if self.quotation_id and self.quotation.supplier_id != self.supplier_id:
            raise ValidationError({'quotation': 'PO quotation belongs to a different supplier.'})
        if self.pk:
            original = PurchaseOrder.objects.filter(pk=self.pk).values('status', 'supplier_id', 'currency', 'sourcing_decision_id', 'quotation_id').first()
            if original and original['status'] in self.LOCKED_STATUSES:
                changed = (
                    original['supplier_id'] != self.supplier_id
                    or original['currency'] != self.currency
                    or original['sourcing_decision_id'] != self.sourcing_decision_id
                    or original['quotation_id'] != self.quotation_id
                )
                if changed:
                    raise ValidationError({'purchase_order': 'Released purchase order sourcing fields are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PurchaseOrderLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='lines')
    line_number = models.PositiveIntegerField(default=1)
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='purchase_order_lines')
    description = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    received_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    accepted_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    unit = models.CharField(max_length=30, default='EA')
    unit_price = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    currency = models.CharField(max_length=3, default='USD')
    requisition = models.ForeignKey(PurchaseRequisition, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_order_lines')
    sourcing_decision_line = models.ForeignKey(SourcingDecisionLine, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_order_lines')
    delivery_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_order_lines')
    delivery_location = models.ForeignKey(StorageLocation, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_order_lines')
    required_date = models.DateField(null=True, blank=True)
    line_version = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['purchase_order', 'line_number']
        unique_together = [('purchase_order', 'line_number')]

    @property
    def open_quantity(self):
        remaining = self.quantity - self.received_quantity
        return remaining if remaining > 0 else Decimal('0')

    def clean(self):
        super().clean()
        self.currency = (self.currency or self.purchase_order.currency if self.purchase_order_id else self.currency or 'USD').strip().upper()
        if self.quantity <= 0 or self.received_quantity < 0 or self.accepted_quantity < 0 or self.unit_price < 0:
            raise ValidationError({'quantity': 'PO line quantities must be positive/non-negative and price cannot be negative.'})
        if self.received_quantity > self.quantity:
            raise ValidationError({'received_quantity': 'Received quantity cannot exceed ordered quantity.'})
        if self.accepted_quantity > self.received_quantity:
            raise ValidationError({'accepted_quantity': 'Accepted quantity cannot exceed received quantity.'})
        if self.delivery_location_id and self.delivery_warehouse_id and self.delivery_location.warehouse_id != self.delivery_warehouse_id:
            raise ValidationError({'delivery_location': 'PO delivery location belongs to a different warehouse.'})
        if self.purchase_order_id and self.pk:
            original = PurchaseOrderLine.objects.filter(pk=self.pk).values('quantity', 'item_revision_id', 'unit_price').first()
            if original and self.purchase_order.status in PurchaseOrder.LOCKED_STATUSES:
                if original['quantity'] != self.quantity or original['item_revision_id'] != self.item_revision_id or original['unit_price'] != self.unit_price:
                    raise ValidationError({'purchase_order_line': 'Released PO line commercial/source fields are immutable.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PurchaseOrderDeliverySchedule(models.Model):
    STATUS_OPEN = 'OPEN'
    STATUS_CONFIRMED = 'CONFIRMED'
    STATUS_RECEIVED = 'RECEIVED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_OPEN, 'Open'), (STATUS_CONFIRMED, 'Confirmed'), (STATUS_RECEIVED, 'Received'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.CASCADE, related_name='schedules')
    schedule_number = models.PositiveIntegerField(default=1)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    promised_date = models.DateField()
    confirmed_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_OPEN)
    supplier_commitment_reference = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['purchase_order_line', 'schedule_number']
        unique_together = [('purchase_order_line', 'schedule_number')]

    def clean(self):
        super().clean()
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'Delivery schedule quantity must be positive.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PurchaseReceipt(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_POSTED = 'POSTED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_POSTED, 'Posted'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receipt_number = models.CharField(max_length=80, unique=True)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name='receipts')
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchase_receipts')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_POSTED)
    received_at = models.DateTimeField()
    packing_slip = models.CharField(max_length=120, blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='purchase_receipts')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_at']
        constraints = [
            models.UniqueConstraint(fields=['purchase_order', 'idempotency_key'], condition=~Q(idempotency_key=''), name='unique_purchase_receipt_idempotency_key'),
        ]

    def save(self, *args, **kwargs):
        if self.pk and PurchaseReceipt.objects.filter(pk=self.pk).exists():
            raise ValidationError({'receipt': 'Posted purchase receipts are immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'receipt': 'Purchase receipts cannot be deleted.'})


class PurchaseReceiptLine(models.Model):
    QUALITY_PENDING = 'PENDING_INSPECTION'
    QUALITY_RELEASED = 'RELEASED'
    QUALITY_QUARANTINED = 'QUARANTINED'
    QUALITY_REJECTED = 'REJECTED'
    QUALITY_CHOICES = [(QUALITY_PENDING, 'Pending inspection'), (QUALITY_RELEASED, 'Released'), (QUALITY_QUARANTINED, 'Quarantined'), (QUALITY_REJECTED, 'Rejected')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receipt = models.ForeignKey(PurchaseReceipt, on_delete=models.CASCADE, related_name='lines')
    purchase_order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name='receipt_lines')
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', on_delete=models.PROTECT, related_name='purchase_receipt_lines')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    accepted_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    unit = models.CharField(max_length=30, default='EA')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='purchase_receipt_lines')
    location = models.ForeignKey(StorageLocation, on_delete=models.PROTECT, related_name='purchase_receipt_lines')
    inventory_transaction = models.ForeignKey(InventoryTransaction, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_receipt_lines')
    inventory_balance = models.ForeignKey(InventoryBalance, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_receipt_lines')
    quality_status = models.CharField(max_length=30, choices=QUALITY_CHOICES, default=QUALITY_PENDING)
    inspection_execution = models.ForeignKey(InspectionExecution, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_receipt_lines')
    quality_hold = models.ForeignKey(QualityHold, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_receipt_lines')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['receipt', 'created_at']

    def clean(self):
        super().clean()
        if self.quantity <= 0 or self.accepted_quantity < 0:
            raise ValidationError({'quantity': 'Receipt quantity must be positive and accepted quantity cannot be negative.'})
        if self.accepted_quantity > self.quantity:
            raise ValidationError({'accepted_quantity': 'Accepted quantity cannot exceed receipt quantity.'})
        if self.location_id and self.warehouse_id and self.location.warehouse_id != self.warehouse_id:
            raise ValidationError({'location': 'Receipt location belongs to a different warehouse.'})
        if self.purchase_order_line_id and self.item_revision_id and self.purchase_order_line.item_revision_id != self.item_revision_id:
            raise ValidationError({'item_revision': 'Receipt item must match the PO line item.'})

    def save(self, *args, **kwargs):
        if self.pk and PurchaseReceiptLine.objects.filter(pk=self.pk).exists():
            raise ValidationError({'receipt_line': 'Posted purchase receipt lines are immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)


class PurchaseReturn(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_RELEASED = 'RELEASED'
    STATUS_CLOSED = 'CLOSED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_RELEASED, 'Released'), (STATUS_CLOSED, 'Closed'), (STATUS_CANCELLED, 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    return_number = models.CharField(max_length=80, unique=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchase_returns')
    purchase_order = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_returns')
    receipt_line = models.ForeignKey(PurchaseReceiptLine, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_returns')
    nonconformance = models.ForeignKey(NonconformanceRecord, null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_returns')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_purchase_returns')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        self.return_number = (self.return_number or '').strip().upper()
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'Return quantity must be positive.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ProcurementEvent(models.Model):
    EVENT_CHOICES = [
        ('SUPPLIER_STATUS', 'Supplier status'),
        ('RFQ_RELEASED', 'RFQ released'),
        ('QUOTE_SUBMITTED', 'Quote submitted'),
        ('SOURCING_APPROVED', 'Sourcing approved'),
        ('PO_RELEASED', 'PO released'),
        ('PO_CONFIRMED', 'PO confirmed'),
        ('RECEIPT_POSTED', 'Receipt posted'),
        ('RETURN_CREATED', 'Return created'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_number = models.CharField(max_length=80, unique=True)
    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name='procurement_events')
    rfq = models.ForeignKey(RequestForQuotation, null=True, blank=True, on_delete=models.PROTECT, related_name='procurement_events')
    quotation = models.ForeignKey(SupplierQuotation, null=True, blank=True, on_delete=models.PROTECT, related_name='procurement_events')
    sourcing_decision = models.ForeignKey(SourcingDecision, null=True, blank=True, on_delete=models.PROTECT, related_name='procurement_events')
    purchase_order = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT, related_name='procurement_events')
    receipt = models.ForeignKey(PurchaseReceipt, null=True, blank=True, on_delete=models.PROTECT, related_name='procurement_events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='procurement_events')
    event_timestamp = models.DateTimeField()
    idempotency_key = models.CharField(max_length=120, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-event_timestamp', '-event_number']
        constraints = [
            models.UniqueConstraint(fields=['event_type', 'idempotency_key'], condition=~Q(idempotency_key=''), name='unique_procurement_event_idempotency_key'),
        ]
        indexes = [models.Index(fields=['event_type', 'event_timestamp']), models.Index(fields=['purchase_order', 'event_type'])]

    def save(self, *args, **kwargs):
        if self.pk and ProcurementEvent.objects.filter(pk=self.pk).exists():
            raise ValidationError({'event': 'Procurement events are append-only and immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'event': 'Procurement events cannot be deleted.'})


class CostRate(models.Model):
    TYPE_LABOR = 'LABOR'
    TYPE_MACHINE = 'MACHINE'
    TYPE_WORK_CENTER = 'WORK_CENTER'
    TYPE_MATERIAL_OVERHEAD = 'MATERIAL_OVERHEAD'
    TYPE_CHOICES = [(TYPE_LABOR, 'Labor'), (TYPE_MACHINE, 'Machine'), (TYPE_WORK_CENTER, 'Work center'), (TYPE_MATERIAL_OVERHEAD, 'Material overhead')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rate_code = models.CharField(max_length=80, unique=True)
    rate_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    currency = models.CharField(max_length=3, default='USD')
    hourly_rate = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    unit_rate = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    labor_skill = models.ForeignKey(LaborSkill, null=True, blank=True, on_delete=models.PROTECT, related_name='cost_rates')
    work_center = models.ForeignKey(WorkCenter, null=True, blank=True, on_delete=models.PROTECT, related_name='cost_rates')
    machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='cost_rates')
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['rate_code', '-effective_from']
        indexes = [models.Index(fields=['rate_type', 'active']), models.Index(fields=['work_center', 'active']), models.Index(fields=['machine', 'active'])]

    def clean(self):
        super().clean()
        self.rate_code = (self.rate_code or '').strip().upper()
        self.currency = (self.currency or 'USD').strip().upper()
        if self.hourly_rate < 0 or self.unit_rate < 0:
            raise ValidationError({'rate': 'Cost rates cannot be negative.'})
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective-to date cannot precede effective-from date.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class CurrencyRatePolicy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy_code = models.CharField(max_length=80, unique=True)
    base_currency = models.CharField(max_length=3, default='USD')
    target_currency = models.CharField(max_length=3, default='USD')
    rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    effective_from = models.DateField()
    source = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        self.policy_code = (self.policy_code or '').strip().upper()
        self.base_currency = (self.base_currency or 'USD').strip().upper()
        self.target_currency = (self.target_currency or 'USD').strip().upper()
        if self.rate <= 0:
            raise ValidationError({'rate': 'Currency rate must be positive.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class CostSnapshot(models.Model):
    TYPE_ITEM = 'ITEM'
    TYPE_PRODUCTION_ORDER = 'PRODUCTION_ORDER'
    TYPE_MAINTENANCE_WORK_ORDER = 'MAINTENANCE_WORK_ORDER'
    TYPE_PROJECT = 'PROJECT'
    TYPE_RECEIPT = 'RECEIPT'
    TYPE_CHOICES = [
        (TYPE_ITEM, 'Item'),
        (TYPE_PRODUCTION_ORDER, 'Production order'),
        (TYPE_MAINTENANCE_WORK_ORDER, 'Maintenance work order'),
        (TYPE_PROJECT, 'Project'),
        (TYPE_RECEIPT, 'Receipt'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot_number = models.CharField(max_length=80, unique=True)
    snapshot_type = models.CharField(max_length=40, choices=TYPE_CHOICES)
    policy_version = models.CharField(max_length=80, default='cost-rollup-v1')
    currency = models.CharField(max_length=3, default='USD')
    item_revision = models.ForeignKey('enterprise_items.ItemRevision', null=True, blank=True, on_delete=models.PROTECT, related_name='cost_snapshots')
    production_order = models.ForeignKey(ProductionOrder, null=True, blank=True, on_delete=models.PROTECT, related_name='cost_snapshots')
    maintenance_work_order = models.ForeignKey(MaintenanceWorkOrder, null=True, blank=True, on_delete=models.PROTECT, related_name='cost_snapshots')
    project = models.ForeignKey('ktcPlanning.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='cost_snapshots')
    purchase_receipt = models.ForeignKey(PurchaseReceipt, null=True, blank=True, on_delete=models.PROTECT, related_name='cost_snapshots')
    material_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    labor_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    machine_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    subcontract_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    overhead_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    total_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    evidence = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='cost_snapshots')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['snapshot_type', 'created_at']), models.Index(fields=['production_order', 'created_at']), models.Index(fields=['project', 'created_at'])]

    def clean(self):
        super().clean()
        self.currency = (self.currency or 'USD').strip().upper()
        for field in ('material_cost', 'labor_cost', 'machine_cost', 'subcontract_cost', 'overhead_cost', 'total_cost'):
            if getattr(self, field) < 0:
                raise ValidationError({field: 'Cost snapshot values cannot be negative.'})

    def save(self, *args, **kwargs):
        if self.pk and CostSnapshot.objects.filter(pk=self.pk).exists():
            raise ValidationError({'snapshot': 'Cost snapshots are immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError({'snapshot': 'Cost snapshots cannot be deleted.'})


class SupplierPerformanceSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='performance_snapshots')
    snapshot_number = models.CharField(max_length=80, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()
    on_time_delivery_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    quality_acceptance_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    average_lead_time_days = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    defect_count = models.PositiveIntegerField(default=0)
    receipt_count = models.PositiveIntegerField(default=0)
    score = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    evidence = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period_end', 'supplier__supplier_code']
        unique_together = [('supplier', 'period_start', 'period_end')]

    def clean(self):
        super().clean()
        if self.period_end < self.period_start:
            raise ValidationError({'period_end': 'Performance period end cannot precede start.'})

    def save(self, *args, **kwargs):
        if self.pk and SupplierPerformanceSnapshot.objects.filter(pk=self.pk).exists():
            raise ValidationError({'snapshot': 'Supplier performance snapshots are immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)


class OperationalKPISnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot_number = models.CharField(max_length=80, unique=True)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    plant = models.ForeignKey(Plant, null=True, blank=True, on_delete=models.PROTECT, related_name='kpi_snapshots')
    work_center = models.ForeignKey(WorkCenter, null=True, blank=True, on_delete=models.PROTECT, related_name='kpi_snapshots')
    machine = models.ForeignKey(MachineAsset, null=True, blank=True, on_delete=models.PROTECT, related_name='kpi_snapshots')
    production_order = models.ForeignKey(ProductionOrder, null=True, blank=True, on_delete=models.PROTECT, related_name='kpi_snapshots')
    oee_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    availability_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    performance_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    quality_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    throughput_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    cycle_time_seconds = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    setup_seconds = models.PositiveIntegerField(default=0)
    wait_seconds = models.PositiveIntegerField(default=0)
    yield_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    scrap_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    rework_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    schedule_adherence_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    utilization_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    evidence = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='operational_kpi_snapshots')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period_end']
        indexes = [models.Index(fields=['plant', 'period_end']), models.Index(fields=['work_center', 'period_end']), models.Index(fields=['machine', 'period_end'])]

    def clean(self):
        super().clean()
        if self.period_end <= self.period_start:
            raise ValidationError({'period_end': 'KPI period end must be after start.'})
        for field in ('oee_percent', 'availability_percent', 'performance_percent', 'quality_percent', 'yield_percent', 'scrap_percent', 'rework_percent', 'schedule_adherence_percent', 'utilization_percent'):
            if getattr(self, field) < 0:
                raise ValidationError({field: 'KPI percentages cannot be negative.'})

    def save(self, *args, **kwargs):
        if self.pk and OperationalKPISnapshot.objects.filter(pk=self.pk).exists():
            raise ValidationError({'snapshot': 'Operational KPI snapshots are immutable.'})
        self.full_clean()
        super().save(*args, **kwargs)


class OPCValidationEvidence(models.Model):
    MODE_DRAFT = 'draft'
    MODE_RELEASE = 'release'
    MODE_CHOICES = [
        (MODE_DRAFT, 'Draft'),
        (MODE_RELEASE, 'Release'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    diagram = models.ForeignKey(OPCDiagram, on_delete=models.CASCADE, related_name='validation_evidence')
    graph_version = models.PositiveBigIntegerField()
    policy_version = models.CharField(max_length=80)
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default=MODE_RELEASE)
    validated_at = models.DateTimeField()
    released_at = models.DateTimeField(null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='opc_validation_evidence')
    valid = models.BooleanField(default=False)
    release_ready = models.BooleanField(default=False)
    counts = models.JSONField(default=dict, blank=True)
    issues = models.JSONField(default=list, blank=True)
    acknowledged_issue_keys = models.JSONField(default=list, blank=True)
    acknowledged_warning_codes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['diagram', 'graph_version', 'policy_version']),
            models.Index(fields=['released_at']),
        ]

    def __str__(self):
        return f'{self.diagram_id} / {self.policy_version} / v{self.graph_version}'
