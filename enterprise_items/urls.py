from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BOMLineViewSet,
    BOMRevisionViewSet,
    AttributeDefinitionViewSet,
    AttributeEncodingOptionViewSet,
    AttributeEncodingRuleViewSet,
    ClassificationAttributeViewSet,
    BOMViewSet,
    EngineeringReadinessViewSet,
    CodeSchemeViewSet,
    CodingOrganizationViewSet,
    CodingViewSet,
    ItemCategoryViewSet,
    ItemClassificationViewSet,
    ItemCodingSchemeViewSet,
    ItemCodingTemplateSegmentViewSet,
    ItemCodingTemplateViewSet,
    ItemIdentifierViewSet,
    ItemRevisionAttributeValueViewSet,
    ItemRevisionViewSet,
    ItemTypeViewSet,
    ItemViewSet,
    ManufacturingVariantViewSet,
    PartCodingWorkflowViewSet,
    ProcessStageCodeViewSet,
    VariantInputViewSet,
)

router = DefaultRouter()
router.register('organizations', CodingOrganizationViewSet, basename='item-organization')
router.register('item-types', ItemTypeViewSet, basename='item-type')
router.register('categories', ItemCategoryViewSet, basename='item-category')
router.register('classifications', ItemClassificationViewSet, basename='item-classification')
router.register('attribute-definitions', AttributeDefinitionViewSet, basename='attribute-definition')
router.register('classification-attributes', ClassificationAttributeViewSet, basename='classification-attribute')
router.register('item-coding-schemes', ItemCodingSchemeViewSet, basename='item-coding-scheme')
router.register('encoding-rules', AttributeEncodingRuleViewSet, basename='attribute-encoding-rule')
router.register('encoding-options', AttributeEncodingOptionViewSet, basename='attribute-encoding-option')
router.register('coding-templates', ItemCodingTemplateViewSet, basename='item-coding-template')
router.register('coding-template-segments', ItemCodingTemplateSegmentViewSet, basename='item-coding-template-segment')
router.register('revision-attribute-values', ItemRevisionAttributeValueViewSet, basename='item-revision-attribute-value')
router.register('item-identifiers', ItemIdentifierViewSet, basename='item-identifier')
router.register('part-coding', PartCodingWorkflowViewSet, basename='part-coding')
router.register('coding', CodingViewSet, basename='coding')
router.register('code-schemes', CodeSchemeViewSet, basename='code-scheme')
router.register('items', ItemViewSet, basename='enterprise-item')
router.register('boms', BOMViewSet, basename='bom')
router.register('bom-revisions', BOMRevisionViewSet, basename='bom-revision')
router.register('bom-lines', BOMLineViewSet, basename='bom-line')
router.register('engineering-readiness', EngineeringReadinessViewSet, basename='engineering-readiness')
router.register('item-revisions', ItemRevisionViewSet, basename='item-revision')
router.register('manufacturing-variants', ManufacturingVariantViewSet, basename='manufacturing-variant')
router.register('variant-inputs', VariantInputViewSet, basename='variant-input')
router.register('stage-codes', ProcessStageCodeViewSet, basename='stage-code')

urlpatterns = [
    path('', include(router.urls)),
]
