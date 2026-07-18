from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .bom_services import ensure_bom_line_editable, ensure_bom_revision_editable
from .readiness import evaluate_item_revision_readiness
from .services import ensure_revision_editable

from .models import (
    BOM,
    BOMLine,
    BOMRevision,
    AttributeDefinition,
    AttributeEncodingOption,
    AttributeEncodingRule,
    ClassificationAttribute,
    CodeScheme,
    CodingOrganization,
    ItemClassification,
    ItemCodeSequence,
    ItemCodingScheme,
    ItemCodingTemplate,
    ItemCodingTemplateSegment,
    ItemIdentifier,
    ItemRevisionAttributeValue,
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
    item_code = serializers.CharField(source='item.item_code', read_only=True)
    released_by_username = serializers.CharField(source='released_by.username', read_only=True, default=None)
    approved_by_username = serializers.CharField(source='approved_by.username', read_only=True, default=None)
    submitted_by_username = serializers.CharField(source='submitted_by.username', read_only=True, default=None)
    superseded_by_revision = serializers.CharField(source='superseded_by.revision', read_only=True, default=None)
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = ItemRevision
        fields = [
            'id', 'item', 'item_code', 'revision', 'title', 'description',
            'drawing_no', 'specification', 'material_specification', 'technical_notes',
            'effective_from', 'effective_to', 'status', 'is_active',
            'weight', 'weight_unit', 'dimensions',
            'submitted_by', 'submitted_by_username', 'submitted_at',
            'approved_by', 'approved_by_username', 'approved_at',
            'released_by', 'released_by_username', 'released_at',
            'superseded_at', 'superseded_by', 'superseded_by_revision',
            'created_at', 'updated_at', 'allowed_actions',
        ]
        read_only_fields = [
            'id', 'item_code', 'status', 'submitted_by', 'submitted_by_username', 'submitted_at',
            'approved_by', 'approved_by_username', 'approved_at', 'released_by',
            'released_by_username', 'released_at', 'superseded_at', 'superseded_by',
            'superseded_by_revision', 'created_at', 'updated_at', 'allowed_actions',
        ]

    def get_allowed_actions(self, obj):
        status = obj.status
        if status == ItemRevision.STATUS_DRAFT:
            return ['submit']
        if status == ItemRevision.STATUS_UNDER_REVIEW:
            return ['return_to_draft', 'approve']
        if status == ItemRevision.STATUS_APPROVED:
            return ['return_to_review', 'release', 'supersede']
        if status == ItemRevision.STATUS_RELEASED:
            return ['obsolete']
        if status == ItemRevision.STATUS_SUPERSEDED:
            return ['obsolete']
        return []

    def validate_revision(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Revision code is required.')
        return value

    def validate(self, attrs):
        ensure_revision_editable(self.instance, attrs)
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


class BOMLineSerializer(serializers.ModelSerializer):
    component_item_code = serializers.CharField(source='component_item_revision.item.item_code', read_only=True)
    component_revision = serializers.CharField(source='component_item_revision.revision', read_only=True)

    class Meta:
        model = BOMLine
        fields = [
            'id', 'bom_revision', 'sequence', 'component_item_revision',
            'component_item_code', 'component_revision', 'quantity', 'unit',
            'scrap_percent', 'is_phantom', 'is_optional', 'reference_designator',
            'notes', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'component_item_code', 'component_revision', 'created_at', 'updated_at']

    def validate(self, attrs):
        instance = self.instance
        bom_revision = attrs.get('bom_revision') or getattr(instance, 'bom_revision', None)
        if instance:
            ensure_bom_line_editable(instance)
        elif bom_revision and bom_revision.status in BOMRevision.LOCKED_STATUSES:
            raise serializers.ValidationError({'bom_revision': 'Released BOM revisions are immutable.'})
        quantity = attrs.get('quantity', getattr(instance, 'quantity', None))
        scrap_percent = attrs.get('scrap_percent', getattr(instance, 'scrap_percent', 0))
        if quantity is not None and quantity <= 0:
            raise serializers.ValidationError({'quantity': 'Quantity must be greater than zero.'})
        if scrap_percent is not None and scrap_percent < 0:
            raise serializers.ValidationError({'scrap_percent': 'Scrap percentage cannot be negative.'})
        component = attrs.get('component_item_revision') or getattr(instance, 'component_item_revision', None)
        if bom_revision and component and component.pk == bom_revision.bom.parent_item_revision_id:
            raise serializers.ValidationError({'component_item_revision': 'A BOM line cannot reference the parent item revision.'})
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


class BOMRevisionSerializer(serializers.ModelSerializer):
    lines = BOMLineSerializer(many=True, read_only=True)
    parent_item_revision = serializers.UUIDField(source='bom.parent_item_revision_id', read_only=True)
    parent_item_code = serializers.CharField(source='bom.parent_item_revision.item.item_code', read_only=True)
    released_by_username = serializers.CharField(source='released_by.username', read_only=True, default=None)
    approved_by_username = serializers.CharField(source='approved_by.username', read_only=True, default=None)
    submitted_by_username = serializers.CharField(source='submitted_by.username', read_only=True, default=None)
    superseded_by_revision = serializers.CharField(source='superseded_by.revision', read_only=True, default=None)
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = BOMRevision
        fields = [
            'id', 'bom', 'parent_item_revision', 'parent_item_code', 'revision',
            'description', 'effective_from', 'effective_to', 'status', 'is_active',
            'submitted_by', 'submitted_by_username', 'submitted_at',
            'approved_by', 'approved_by_username', 'approved_at',
            'released_by', 'released_by_username', 'released_at',
            'superseded_at', 'superseded_by', 'superseded_by_revision',
            'created_by', 'created_at', 'updated_at', 'lines', 'allowed_actions',
        ]
        read_only_fields = [
            'id', 'parent_item_revision', 'parent_item_code', 'status', 'submitted_by',
            'submitted_by_username', 'submitted_at', 'approved_by', 'approved_by_username',
            'approved_at', 'released_by', 'released_by_username', 'released_at',
            'superseded_at', 'superseded_by', 'superseded_by_revision', 'created_by',
            'created_at', 'updated_at', 'lines', 'allowed_actions',
        ]

    def get_allowed_actions(self, obj):
        if obj.status == BOMRevision.STATUS_DRAFT:
            return ['submit', 'clone']
        if obj.status == BOMRevision.STATUS_UNDER_REVIEW:
            return ['return_to_draft', 'approve', 'clone']
        if obj.status == BOMRevision.STATUS_APPROVED:
            return ['return_to_review', 'release', 'supersede', 'clone']
        if obj.status in {BOMRevision.STATUS_RELEASED, BOMRevision.STATUS_SUPERSEDED}:
            return ['obsolete', 'clone']
        return ['clone']

    def validate_revision(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('BOM revision code is required.')
        return value

    def validate(self, attrs):
        ensure_bom_revision_editable(self.instance, attrs)
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


class BOMSerializer(serializers.ModelSerializer):
    parent_item_code = serializers.CharField(source='parent_item_revision.item.item_code', read_only=True)
    parent_revision = serializers.CharField(source='parent_item_revision.revision', read_only=True)
    revisions = BOMRevisionSerializer(many=True, read_only=True)

    class Meta:
        model = BOM
        fields = [
            'id', 'parent_item_revision', 'parent_item_code', 'parent_revision',
            'bom_code', 'bom_type', 'description', 'is_active', 'created_by',
            'created_at', 'updated_at', 'revisions',
        ]
        read_only_fields = ['id', 'parent_item_code', 'parent_revision', 'created_by', 'created_at', 'updated_at', 'revisions']

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)


class EngineeringReadinessSerializer(serializers.Serializer):
    item_revision_id = serializers.CharField()
    item_code = serializers.CharField()
    revision_code = serializers.CharField()
    make_or_buy = serializers.CharField()
    item_revision_status = serializers.CharField()
    has_released_bom = serializers.BooleanField()
    has_released_opc = serializers.BooleanField()
    bom_ready = serializers.BooleanField()
    opc_ready = serializers.BooleanField()
    release_ready = serializers.BooleanField()
    blockers = serializers.ListField(child=serializers.CharField())
    warnings = serializers.ListField(child=serializers.CharField())


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
            'status', 'tracking_mode', 'make_or_buy', 'is_active', 'drawing_no', 'specification',
            'default_revision', 'weight', 'metadata', 'revisions',
            'manufacturing_variants', 'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def validate_item_code(self, value):
        return (value or '').strip()

    def validate_base_unit(self, value):
        return (value or '').strip().upper()

    def validate(self, attrs):
        organization = attrs.get('organization') or getattr(self.instance, 'organization', None)
        item_type = attrs.get('item_type') or getattr(self.instance, 'item_type', None)
        category = attrs.get('category') or getattr(self.instance, 'category', None)
        code_scheme = attrs.get('code_scheme')
        item_code = attrs.get('item_code') or getattr(self.instance, 'item_code', '')

        if organization and item_code:
            duplicate = Item.objects.filter(organization=organization, item_code__iexact=item_code.strip())
            if self.instance:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError({'item_code': 'Item code must be unique within the organization.'})

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
        try:
            return super().create(validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)

    def update(self, instance, validated_data):
        validated_data.pop('code_scheme', None)
        try:
            return super().update(instance, validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)


class ItemClassificationSerializer(serializers.ModelSerializer):
    parent_code = serializers.CharField(source='parent.code', read_only=True, default=None)
    children_count = serializers.IntegerField(source='children.count', read_only=True)

    class Meta:
        model = ItemClassification
        fields = ['id', 'organization', 'parent', 'parent_code', 'code', 'name', 'description', 'path', 'coding_prefix', 'sort_order', 'is_active', 'children_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'path', 'children_count', 'created_at', 'updated_at']


class AttributeDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttributeDefinition
        fields = ['id', 'organization', 'code', 'name', 'description', 'data_type', 'unit_dimension', 'default_unit', 'is_required', 'is_searchable', 'is_identity_defining', 'is_code_bearing', 'is_duplicate_key', 'is_revision_controlled', 'sort_order', 'is_active', 'validation_metadata', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ClassificationAttributeSerializer(serializers.ModelSerializer):
    attribute = AttributeDefinitionSerializer(source='attribute_definition', read_only=True)
    effective_required = serializers.BooleanField(read_only=True)
    effective_code_bearing = serializers.BooleanField(read_only=True)
    effective_identity_defining = serializers.BooleanField(read_only=True)
    effective_duplicate_key = serializers.BooleanField(read_only=True)

    class Meta:
        model = ClassificationAttribute
        fields = ['id', 'classification', 'attribute_definition', 'attribute', 'required_override', 'code_bearing_override', 'identity_defining_override', 'duplicate_key_override', 'default_unit', 'display_order', 'inherited', 'is_active', 'effective_required', 'effective_code_bearing', 'effective_identity_defining', 'effective_duplicate_key']
        read_only_fields = ['id', 'effective_required', 'effective_code_bearing', 'effective_identity_defining', 'effective_duplicate_key']


class ItemRevisionAttributeValueSerializer(serializers.ModelSerializer):
    attribute = AttributeDefinitionSerializer(source='attribute_definition', read_only=True)

    class Meta:
        model = ItemRevisionAttributeValue
        fields = ['id', 'item_revision', 'attribute_definition', 'attribute', 'value_text', 'value_decimal', 'value_integer', 'value_boolean', 'value_date', 'value_choice', 'unit', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ItemCodingSchemeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemCodingScheme
        fields = ['id', 'organization', 'code', 'name', 'strategy', 'separator', 'maximum_length', 'case_policy', 'sequence_scope', 'sequence_length', 'is_default', 'is_active', 'description', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class AttributeEncodingOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttributeEncodingOption
        fields = ['id', 'rule', 'source_value', 'encoded_value', 'sort_order', 'is_active']
        read_only_fields = ['id']


class AttributeEncodingRuleSerializer(serializers.ModelSerializer):
    options = AttributeEncodingOptionSerializer(many=True, read_only=True)

    class Meta:
        model = AttributeEncodingRule
        fields = ['id', 'organization', 'code', 'name', 'encoding_type', 'canonical_unit', 'multiplier', 'precision', 'rounding_policy', 'zero_pad_width', 'text_case', 'max_length', 'true_token', 'false_token', 'date_pattern', 'prefix', 'suffix', 'is_active', 'options', 'created_at', 'updated_at']
        read_only_fields = ['id', 'options', 'created_at', 'updated_at']


class ItemCodingTemplateSegmentSerializer(serializers.ModelSerializer):
    attribute = AttributeDefinitionSerializer(source='attribute_definition', read_only=True)
    rule = AttributeEncodingRuleSerializer(source='encoding_rule', read_only=True)

    class Meta:
        model = ItemCodingTemplateSegment
        fields = ['id', 'template', 'position', 'segment_type', 'literal_value', 'classification_level', 'attribute_definition', 'attribute', 'encoding_rule', 'rule', 'width', 'required', 'fallback_value', 'prefix', 'suffix']
        read_only_fields = ['id', 'attribute', 'rule']


class ItemCodingTemplateSerializer(serializers.ModelSerializer):
    segments = ItemCodingTemplateSegmentSerializer(many=True, read_only=True)
    coding_scheme_detail = ItemCodingSchemeSerializer(source='coding_scheme', read_only=True)
    classification_detail = ItemClassificationSerializer(source='classification', read_only=True)

    class Meta:
        model = ItemCodingTemplate
        fields = ['id', 'coding_scheme', 'coding_scheme_detail', 'classification', 'classification_detail', 'version', 'status', 'effective_from', 'effective_to', 'fallback_sequence_enabled', 'is_active', 'description', 'segments', 'activated_at', 'retired_at', 'created_at', 'updated_at']
        read_only_fields = ['id', 'status', 'segments', 'activated_at', 'retired_at', 'created_at', 'updated_at']

    def update(self, instance, validated_data):
        validated_data.pop('status', None)
        return super().update(instance, validated_data)


class ItemIdentifierSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemIdentifier
        fields = ['id', 'item', 'identifier_type', 'value', 'normalized_value', 'organization_name', 'is_primary', 'is_verified', 'effective_from', 'effective_to', 'created_at', 'updated_at']
        read_only_fields = ['id', 'normalized_value', 'created_at', 'updated_at']


class CodePreviewSerializer(serializers.Serializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=CodingOrganization.objects.all())
    classification = serializers.PrimaryKeyRelatedField(queryset=ItemClassification.objects.all())
    coding_scheme = serializers.PrimaryKeyRelatedField(queryset=ItemCodingScheme.objects.all(), required=False, allow_null=True)
    attributes = serializers.DictField(child=serializers.JSONField(), required=False)
    manual_code = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    identifiers = serializers.ListField(child=serializers.DictField(), required=False)


class ControlledItemCreateSerializer(CodePreviewSerializer):
    item_type = serializers.PrimaryKeyRelatedField(queryset=ItemType.objects.all())
    name = serializers.CharField()
    base_unit = serializers.CharField(default='EA')
    make_or_buy = serializers.ChoiceField(choices=Item.MAKE_OR_BUY_CHOICES, default='MAKE')
    duplicate_override = serializers.BooleanField(default=False)
