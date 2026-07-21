from rest_framework import serializers
from .models import (
    CodeDefinition, CodeDefinitionVersion, CodeSegment, CodingOrganization, Item, ItemType,
    ParameterDefinition, ParameterMetadataField, ParameterOption, StructureDefinition,
    StructureParameter, TechnicalDataTemplate, TechnicalFieldDefinition,
)


class ParameterReadSerializer(serializers.ModelSerializer):
    option_count = serializers.IntegerField(read_only=True, default=0)
    class Meta:
        model = ParameterDefinition
        fields = ['id','organization','code','name','description','data_type','default_unit','searchable','active','option_count','created_at','updated_at']


class ParameterCreateSerializer(serializers.Serializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=CodingOrganization.objects.all())
    code = serializers.CharField(max_length=80)
    name = serializers.CharField(max_length=180)
    data_type = serializers.ChoiceField(choices=ParameterDefinition.DATA_TYPE_CHOICES)
    description = serializers.CharField(required=False, allow_blank=True)
    default_unit = serializers.CharField(required=False, allow_blank=True, max_length=32)
    searchable = serializers.BooleanField(required=False, default=True)
    active = serializers.BooleanField(required=False, default=True)


class ParameterUpdateSerializer(serializers.Serializer):
    code = serializers.CharField(required=False, max_length=80)
    name = serializers.CharField(required=False, max_length=180)
    data_type = serializers.ChoiceField(required=False, choices=ParameterDefinition.DATA_TYPE_CHOICES)
    description = serializers.CharField(required=False, allow_blank=True)
    default_unit = serializers.CharField(required=False, allow_blank=True, max_length=32)
    searchable = serializers.BooleanField(required=False)
    active = serializers.BooleanField(required=False)


class ParameterOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterOption
        fields = ['id','display_label','stored_value','description','sort_order','active','metadata']


class BulkParameterSerializer(serializers.Serializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=CodingOrganization.objects.all())
    rows = serializers.ListField(child=serializers.DictField())
    partial = serializers.BooleanField(required=False, default=False)


class MetadataFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterMetadataField
        fields = ['id','organization','code','label','description','data_type','group','requiredness','unit','default_value','validation_config','options','sort_order','active']


class StructureReadSerializer(serializers.ModelSerializer):
    parameter_count = serializers.IntegerField(read_only=True, default=0)
    class Meta:
        model = StructureDefinition
        fields = ['id','organization','code','name','description','classification','status','version','active','parameter_count','created_at','updated_at']


class StructureCreateSerializer(serializers.Serializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=CodingOrganization.objects.all())
    code = serializers.CharField(max_length=80)
    name = serializers.CharField(max_length=180)
    description = serializers.CharField(required=False, allow_blank=True)
    classification = serializers.UUIDField(required=False, allow_null=True)


class StructureParameterSerializer(serializers.ModelSerializer):
    parameter_code = serializers.CharField(source='parameter.code', read_only=True)
    parameter_name = serializers.CharField(source='parameter.name', read_only=True)
    class Meta:
        model = StructureParameter
        fields = ['id','structure','parameter','parameter_code','parameter_name','sort_order','display_group','requiredness','identity_defining','default_value','visibility_condition','required_condition','display_label_override','help_text_override','active']


class CodeDefinitionReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodeDefinition
        fields = ['id','organization','structure','code','name','description','separator','maximum_length','status','active_version','is_default','created_at']


class CodeDefinitionCreateSerializer(serializers.Serializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=CodingOrganization.objects.all())
    structure = serializers.PrimaryKeyRelatedField(queryset=StructureDefinition.objects.all())
    code = serializers.CharField(max_length=80)
    name = serializers.CharField(max_length=180)
    description = serializers.CharField(required=False, allow_blank=True)
    separator = serializers.CharField(required=False, allow_blank=True, max_length=8)
    maximum_length = serializers.IntegerField(required=False, min_value=1, default=80)
    is_default = serializers.BooleanField(required=False, default=False)


class CodeVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodeDefinitionVersion
        fields = ['id','code_definition','version_number','status','configuration_snapshot','created_at','activated_at','superseded_at']


class CodeSegmentSerializer(serializers.ModelSerializer):
    option_encodings = serializers.SerializerMethodField()
    class Meta:
        model = CodeSegment
        fields = ['id','version','segment_type','sort_order','parameter','fixed_value','requiredness','width','padding','prefix','suffix','transform','fallback','condition','configuration','option_encodings']
    def get_option_encodings(self, obj):
        return [{'id': str(e.id), 'parameter_option': str(e.parameter_option_id), 'encoded_token': e.encoded_token, 'active': e.active} for e in obj.option_encodings.all()]


class SegmentListUpdateSerializer(serializers.Serializer):
    rows = serializers.ListField(child=serializers.DictField())


class PreviewSerializer(serializers.Serializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=CodingOrganization.objects.all())
    structure = serializers.PrimaryKeyRelatedField(queryset=StructureDefinition.objects.all())
    code_definition = serializers.PrimaryKeyRelatedField(queryset=CodeDefinition.objects.all(), required=False, allow_null=True)
    version = serializers.PrimaryKeyRelatedField(queryset=CodeDefinitionVersion.objects.all(), required=False, allow_null=True)
    parameter_values = serializers.DictField(required=False, default=dict)


class PartCreateSerializer(PreviewSerializer):
    item_type = serializers.PrimaryKeyRelatedField(queryset=ItemType.objects.all())
    name = serializers.CharField(max_length=255)
    part_technical_values = serializers.DictField(required=False, default=dict)
    revision_technical_values = serializers.DictField(required=False, default=dict)
    idempotency_key = serializers.CharField(required=False, allow_blank=True)
    base_unit = serializers.CharField(required=False, default='EA')
    make_or_buy = serializers.CharField(required=False, default='MAKE')


class TechnicalTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TechnicalDataTemplate
        fields = ['id','organization','structure','code','name','description','status','version','active','created_at']


class TechnicalFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = TechnicalFieldDefinition
        fields = ['id','template','code','label','description','data_type','display_group','scope','requiredness','unit','default_value','validation_config','options','sort_order','active']


class DecodeSerializer(serializers.Serializer):
    code = serializers.CharField()
    version = serializers.PrimaryKeyRelatedField(queryset=CodeDefinitionVersion.objects.all(), required=False, allow_null=True)
    code_definition = serializers.PrimaryKeyRelatedField(queryset=CodeDefinition.objects.all(), required=False, allow_null=True)
    structure = serializers.PrimaryKeyRelatedField(queryset=StructureDefinition.objects.all(), required=False, allow_null=True)