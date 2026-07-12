from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import OPCDiagramViewSet, OPCEdgeViewSet, OPCNodeViewSet

router = DefaultRouter()
router.register('diagrams', OPCDiagramViewSet, basename='opc-diagram')
router.register('nodes', OPCNodeViewSet, basename='opc-node')
router.register('edges', OPCEdgeViewSet, basename='opc-edge')

urlpatterns = [
    path('', include(router.urls)),
]
