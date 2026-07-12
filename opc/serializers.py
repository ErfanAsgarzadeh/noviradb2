from rest_framework import serializers

from .models import OPCDiagram, OPCEdge, OPCNode


class OPCNodeSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='node_type', required=False)

    class Meta:
        model = OPCNode
        fields = [
            'id', 'diagram', 'type', 'label', 'part_code',
            'process_code', 'station', 'description', 'sequence', 'x', 'y',
            'meta', 'created_at', 'updated_at',
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
        return attrs


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
        return attrs


class OPCDiagramSerializer(serializers.ModelSerializer):
    nodes = OPCNodeSerializer(many=True, read_only=True)
    edges = OPCEdgeSerializer(many=True, read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    updated_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = OPCDiagram
        fields = [
            'id', 'title', 'part_code', 'part_name', 'revision',
            'description', 'status', 'nodes', 'edges',
            'created_by', 'updated_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'updated_by', 'created_at', 'updated_at']


class OPCDiagramSummarySerializer(serializers.ModelSerializer):
    node_count = serializers.IntegerField(read_only=True)
    edge_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = OPCDiagram
        fields = [
            'id', 'title', 'part_code', 'part_name', 'revision',
            'status', 'node_count', 'edge_count', 'updated_at',
        ]
