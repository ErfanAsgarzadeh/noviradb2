from rest_framework import serializers

from .models import (
    CodeScheme,
    CodingOrganization,
    Item,
    ItemCategory,
    ItemRevision,
    ItemType,
    ManufacturingVariant,
    ProcessStageCode,
    VariantInput,
)


class CodingOrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodingOrganization
        fields = ['id', 'name', 'code', 'description', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']


class ItemTypeSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)

    class Meta:
        model = ItemType
        fields = ['id', 'organization', 'organization_name', 'name', 'code', 'group', 'description', 'is_active']
        read_only_fields = ['id']


class ItemCategorySerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    parent_name = serializers.CharField(source='parent.name', read_only=True, default=None)

    class Meta:
        model = ItemCategory
        fields = [
            'id', 'organization', 'organization_name', 'parent', 'parent_name',
            'name', 'code', 'description', 'is_active',
        ]
        read_only_fields = ['id']


class CodeSchemeSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    item_type_code = serializers.CharField(source='item_type.code', read_only=True, default=None)
    preview_code = serializers.SerializerMethodField()

    class Meta:
        model = CodeScheme
        fields = [
            'id', 'organization', 'organization_name', 'name', 'item_type',
            'item_type_code', 'prefix', 'separator', 'sequence_padding',
            'next_sequence', 'include_category', 'include_revision',
            'include_stage', 'is_default', 'is_active', 'description',
            'preview_code',
        ]
        read_only_fields = ['id', 'preview_code']

    def get_preview_code(self, obj):
        return obj.preview()


class ItemRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemRevision
        fields = [
            'id', 'item', 'revision', 'title', 'drawing_no', 'specification',
            'effective_from', 'is_active', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class VariantInputSerializer(serializers.ModelSerializer):
    linked_item_code = serializers.CharField(source='linked_item.item_code', read_only=True, default=None)
    linked_item_name = serializers.CharField(source='linked_item.name', read_only=True, default=None)

    class Meta:
        model = VariantInput
        fields = [
            'id', 'variant', 'source_type', 'linked_item', 'linked_item_code',
            'linked_item_name', 'material_grade', 'shape', 'dimensions',
            'quantity', 'unit', 'description',
        ]
        read_only_fields = ['id', 'linked_item_code', 'linked_item_name']

    def validate(self, attrs):
        source_type = attrs.get('source_type') or getattr(self.instance, 'source_type', None)
        linked_item = attrs.get('linked_item') or getattr(self.instance, 'linked_item', None)
        if source_type == 'EXISTING_ITEM' and not linked_item:
            raise serializers.ValidationError({'linked_item': 'Existing item input requires a linked item.'})
        if source_type != 'EXISTING_ITEM' and linked_item:
            raise serializers.ValidationError({'linked_item': 'Linked item can only be used for EXISTING_ITEM inputs.'})
        return attrs


class ProcessStageCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessStageCode
        fields = [
            'id', 'variant', 'stage_code', 'stage_name', 'output_item_code',
            'sequence', 'creates_inventory_identity', 'description',
        ]
        read_only_fields = ['id']


class ManufacturingVariantSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item.item_code', read_only=True)
    item_name = serializers.CharField(source='item.name', read_only=True)
    inputs = VariantInputSerializer(many=True, read_only=True)
    stage_codes = ProcessStageCodeSerializer(many=True, read_only=True)

    class Meta:
        model = ManufacturingVariant
        fields = [
            'id', 'item', 'item_code', 'item_name', 'variant_code', 'name',
            'description', 'status', 'is_default', 'opc_diagram',
            'inputs', 'stage_codes', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'item_code', 'item_name', 'created_at', 'updated_at']


class ItemSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    item_type_code = serializers.CharField(source='item_type.code', read_only=True)
    item_type_group = serializers.CharField(source='item_type.group', read_only=True)
    category_code = serializers.CharField(source='category.code', read_only=True, default=None)
    category_name = serializers.CharField(source='category.name', read_only=True, default=None)
    revisions = ItemRevisionSerializer(many=True, read_only=True)
    manufacturing_variants = ManufacturingVariantSerializer(many=True, read_only=True)
    code_scheme = serializers.PrimaryKeyRelatedField(queryset=CodeScheme.objects.all(), write_only=True, required=False, allow_null=True)

    class Meta:
        model = Item
        fields = [
            'id', 'organization', 'organization_name', 'item_code', 'code_scheme',
            'name', 'item_type', 'item_type_code', 'item_type_group',
            'category', 'category_code', 'category_name', 'base_unit',
            'status', 'tracking_mode', 'drawing_no', 'specification',
            'default_revision', 'weight', 'metadata', 'revisions',
            'manufacturing_variants', 'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        organization = attrs.get('organization') or getattr(self.instance, 'organization', None)
        item_type = attrs.get('item_type') or getattr(self.instance, 'item_type', None)
        category = attrs.get('category') or getattr(self.instance, 'category', None)
        code_scheme = attrs.get('code_scheme')

        if item_type and organization and item_type.organization_id != organization.id:
            raise serializers.ValidationError({'item_type': 'Item type must belong to the selected organization.'})
        if category and organization and category.organization_id != organization.id:
            raise serializers.ValidationError({'category': 'Category must belong to the selected organization.'})
        if code_scheme and organization and code_scheme.organization_id != organization.id:
            raise serializers.ValidationError({'code_scheme': 'Code scheme must belong to the selected organization.'})
        if code_scheme and item_type and code_scheme.item_type_id and code_scheme.item_type_id != item_type.id:
            raise serializers.ValidationError({'code_scheme': 'Code scheme is not valid for this item type.'})
        return attrs

    def create(self, validated_data):
        code_scheme = validated_data.pop('code_scheme', None)
        if not validated_data.get('item_code'):
            if not code_scheme:
                code_scheme = CodeScheme.objects.filter(
                    organization=validated_data['organization'],
                    item_type=validated_data['item_type'],
                    is_default=True,
                    is_active=True,
                ).first() or CodeScheme.objects.filter(
                    organization=validated_data['organization'],
                    is_default=True,
                    is_active=True,
                ).first()
            if not code_scheme:
                raise serializers.ValidationError({'item_code': 'Item code or a default code scheme is required.'})
            validated_data['item_code'] = code_scheme.generate_code(category=validated_data.get('category'), revision=validated_data.get('default_revision', ''))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop('code_scheme', None)
        return super().update(instance, validated_data)
