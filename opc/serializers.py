from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from enterprise_items.serializers import ItemRevisionSerializer

from .models import OPCDiagram, OPCEdge, OPCNode
from .services import ensure_diagram_editable, ensure_edge_editable, ensure_node_editable


class OPCNodeSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='node_type', required=False)

    class Meta:
        model = OPCNode
        fields = [
            'id', 'diagram', 'type', 'label', 'part_code',
            'process_code', 'station', 'execution_type', 'setup_time_hours',
            'run_time_per_unit_hours', 'queue_time_hours', 'move_time_hours',
            'inspection_time_hours', 'external_lead_time_days', 'buffer_time_hours',
            'description', 'sequence', 'x', 'y', 'meta', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
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

    class Meta:
        model = OPCDiagram
        fields = [
            'id', 'item_revision', 'item_revision_detail', 'title', 'part_code',
            'part_name', 'revision', 'description', 'effective_from', 'effective_to',
            'status', 'submitted_by', 'submitted_by_username', 'submitted_at',
            'approved_by', 'approved_by_username', 'approved_at', 'released_by',
            'released_by_username', 'released_at', 'superseded_at', 'superseded_by',
            'superseded_by_revision', 'nodes', 'edges', 'created_by', 'updated_by',
            'created_at', 'updated_at', 'allowed_actions',
        ]
        read_only_fields = ['id', 'item_revision_detail', 'status', 'submitted_by', 'submitted_by_username', 'submitted_at', 'approved_by', 'approved_by_username', 'approved_at', 'released_by', 'released_by_username', 'released_at', 'superseded_at', 'superseded_by', 'superseded_by_revision', 'created_by', 'updated_by', 'created_at', 'updated_at', 'allowed_actions']

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
            'status', 'effective_from', 'effective_to', 'released_at', 'node_count', 'edge_count', 'updated_at',
        ]
