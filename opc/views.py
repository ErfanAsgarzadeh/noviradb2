from django.db.models import Count
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import OPCDiagram, OPCEdge, OPCNode
from .serializers import (
    OPCDiagramSerializer,
    OPCDiagramSummarySerializer,
    OPCEdgeSerializer,
    OPCNodeSerializer,
)


class OPCDiagramViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = OPCDiagram.objects.prefetch_related('nodes', 'edges').annotate(
            node_count=Count('nodes', distinct=True),
            edge_count=Count('edges', distinct=True),
        )
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(part_code__icontains=search) | queryset.filter(title__icontains=search)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset.distinct()

    def get_serializer_class(self):
        if self.action == 'list':
            return OPCDiagramSummarySerializer
        return OPCDiagramSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        diagram = self.get_object()
        serializer = OPCDiagramSerializer(diagram)
        return Response(serializer.data)


class OPCNodeViewSet(viewsets.ModelViewSet):
    serializer_class = OPCNodeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = OPCNode.objects.select_related('diagram')
        diagram_id = self.request.query_params.get('diagram')
        if diagram_id:
            queryset = queryset.filter(diagram_id=diagram_id)
        return queryset


class OPCEdgeViewSet(viewsets.ModelViewSet):
    serializer_class = OPCEdgeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = OPCEdge.objects.select_related('diagram', 'source', 'target')
        diagram_id = self.request.query_params.get('diagram')
        if diagram_id:
            queryset = queryset.filter(diagram_id=diagram_id)
        return queryset
