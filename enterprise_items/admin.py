from django.contrib import admin

from .models import (
    BOM,
    BOMLine,
    BOMRevision,
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


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 0
    readonly_fields = ('created_at', 'updated_at')


@admin.register(BOM)
class BOMAdmin(admin.ModelAdmin):
    list_display = ('bom_code', 'parent_item_revision', 'bom_type', 'is_active', 'created_at')
    list_filter = ('bom_type', 'is_active')
    search_fields = ('bom_code', 'parent_item_revision__item__item_code', 'parent_item_revision__revision')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(BOMRevision)
class BOMRevisionAdmin(admin.ModelAdmin):
    list_display = ('bom', 'revision', 'status', 'effective_from', 'effective_to', 'released_by', 'released_at')
    list_filter = ('status', 'effective_from', 'effective_to')
    search_fields = ('bom__bom_code', 'bom__parent_item_revision__item__item_code', 'revision')
    readonly_fields = ('submitted_at', 'approved_at', 'released_at', 'superseded_at', 'created_at', 'updated_at')
    inlines = [BOMLineInline]


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('item_code', 'name', 'organization', 'item_type', 'status', 'make_or_buy', 'tracking_mode', 'is_active')
    list_filter = ('organization', 'status', 'make_or_buy', 'tracking_mode', 'is_active')
    search_fields = ('item_code', 'name', 'drawing_no')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ItemRevision)
class ItemRevisionAdmin(admin.ModelAdmin):
    list_display = ('item', 'revision', 'status', 'effective_from', 'effective_to', 'released_by', 'released_at')
    list_filter = ('status', 'effective_from', 'effective_to')
    search_fields = ('item__item_code', 'revision', 'title', 'drawing_no')
    readonly_fields = ('submitted_at', 'approved_at', 'released_at', 'superseded_at', 'created_at', 'updated_at')


admin.site.register(CodingOrganization)
admin.site.register(ItemType)
admin.site.register(ItemCategory)
admin.site.register(CodeScheme)
admin.site.register(ManufacturingVariant)
admin.site.register(VariantInput)
admin.site.register(ProcessStageCode)

from .models import (
    AttributeDefinition,
    AttributeEncodingOption,
    AttributeEncodingRule,
    ClassificationAttribute,
    ItemClassification,
    ItemCodingScheme,
    ItemCodingTemplate,
    ItemCodingTemplateSegment,
    ItemIdentifier,
    ItemRevisionAttributeValue,
)


class ClassificationAttributeInline(admin.TabularInline):
    model = ClassificationAttribute
    extra = 0


@admin.register(ItemClassification)
class ItemClassificationAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'path', 'organization', 'parent', 'is_active')
    list_filter = ('organization', 'is_active')
    search_fields = ('code', 'name', 'path')
    inlines = [ClassificationAttributeInline]


@admin.register(AttributeDefinition)
class AttributeDefinitionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'data_type', 'organization', 'is_required', 'is_code_bearing', 'is_identity_defining', 'is_active')
    list_filter = ('organization', 'data_type', 'is_active')
    search_fields = ('code', 'name')


class AttributeEncodingOptionInline(admin.TabularInline):
    model = AttributeEncodingOption
    extra = 0


@admin.register(AttributeEncodingRule)
class AttributeEncodingRuleAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'encoding_type', 'organization', 'is_active')
    list_filter = ('organization', 'encoding_type', 'is_active')
    search_fields = ('code', 'name')
    inlines = [AttributeEncodingOptionInline]


class ItemCodingTemplateSegmentInline(admin.TabularInline):
    model = ItemCodingTemplateSegment
    extra = 0


@admin.register(ItemCodingScheme)
class ItemCodingSchemeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'strategy', 'organization', 'is_default', 'is_active')
    list_filter = ('strategy', 'is_active')
    search_fields = ('code', 'name')


@admin.register(ItemCodingTemplate)
class ItemCodingTemplateAdmin(admin.ModelAdmin):
    list_display = ('classification', 'coding_scheme', 'version', 'status', 'effective_from', 'effective_to')
    list_filter = ('status', 'coding_scheme')
    inlines = [ItemCodingTemplateSegmentInline]


@admin.register(ItemRevisionAttributeValue)
class ItemRevisionAttributeValueAdmin(admin.ModelAdmin):
    list_display = ('item_revision', 'attribute_definition', 'unit')
    search_fields = ('item_revision__item__item_code', 'attribute_definition__code')


@admin.register(ItemIdentifier)
class ItemIdentifierAdmin(admin.ModelAdmin):
    list_display = ('item', 'identifier_type', 'normalized_value', 'organization_name', 'is_primary', 'is_verified')
    list_filter = ('identifier_type', 'is_primary', 'is_verified')
    search_fields = ('value', 'normalized_value', 'item__item_code')
