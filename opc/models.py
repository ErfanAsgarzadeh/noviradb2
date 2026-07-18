import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


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
            models.Index(fields=['node_type']),
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
