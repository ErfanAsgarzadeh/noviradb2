from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .engineering_api_schema import EngineeringSchemaView
from .engineering_api_views import (
    EngineeringCodeDefinitionViewSet,
    EngineeringMetadataFieldViewSet,
    EngineeringParameterViewSet,
    EngineeringPartViewSet,
    EngineeringStructureViewSet,
    EngineeringTechnicalTemplateViewSet,
)

router = DefaultRouter()
router.register('parameters', EngineeringParameterViewSet, basename='engineering-parameter')
router.register('parameter-metadata-fields', EngineeringMetadataFieldViewSet, basename='engineering-parameter-metadata-field')
router.register('structures', EngineeringStructureViewSet, basename='engineering-structure')
router.register('code-definitions', EngineeringCodeDefinitionViewSet, basename='engineering-code-definition')
router.register('technical-templates', EngineeringTechnicalTemplateViewSet, basename='engineering-technical-template')
router.register('parts', EngineeringPartViewSet, basename='engineering-part')

urlpatterns = [path('schema/', EngineeringSchemaView.as_view(), name='engineering-schema'), path('', include(router.urls))]