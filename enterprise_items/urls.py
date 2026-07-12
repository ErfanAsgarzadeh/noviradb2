from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CodeSchemeViewSet,
    CodingOrganizationViewSet,
    ItemCategoryViewSet,
    ItemRevisionViewSet,
    ItemTypeViewSet,
    ItemViewSet,
    ManufacturingVariantViewSet,
    ProcessStageCodeViewSet,
    VariantInputViewSet,
)

router = DefaultRouter()
router.register('organizations', CodingOrganizationViewSet, basename='item-organization')
router.register('item-types', ItemTypeViewSet, basename='item-type')
router.register('categories', ItemCategoryViewSet, basename='item-category')
router.register('code-schemes', CodeSchemeViewSet, basename='code-scheme')
router.register('items', ItemViewSet, basename='enterprise-item')
router.register('item-revisions', ItemRevisionViewSet, basename='item-revision')
router.register('manufacturing-variants', ManufacturingVariantViewSet, basename='manufacturing-variant')
router.register('variant-inputs', VariantInputViewSet, basename='variant-input')
router.register('stage-codes', ProcessStageCodeViewSet, basename='stage-code')

urlpatterns = [
    path('', include(router.urls)),
]
