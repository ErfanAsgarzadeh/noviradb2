import uuid

from django.conf import settings
from django.db import models


class OPCDiagram(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('ACTIVE', 'Active'),
        ('ARCHIVED', 'Archived'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    part_code = models.CharField(max_length=120, db_index=True)
    part_name = models.CharField(max_length=255, blank=True)
    revision = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='DRAFT')
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
        indexes = [
            models.Index(fields=['part_code', 'status']),
            models.Index(fields=['updated_at']),
        ]

    def __str__(self):
        return f'{self.part_code} - {self.title}'


class OPCNode(models.Model):
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
