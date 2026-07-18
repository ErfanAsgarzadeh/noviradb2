from django.db.models import Count
from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.serializers import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.permissions import CanManageEngineering
from rest_framework.response import Response

from .models import OPCDiagram, OPCEdge, OPCNode
from .services import (
    approve_opc_revision,
    clone_opc_revision,
    ensure_edge_editable,
    ensure_node_editable,
    obsolete_opc_revision,
    release_opc_revision,
    return_opc_revision_to_draft,
    return_opc_revision_to_review,
    submit_opc_revision,
    validate_opc_graph,
)
from .serializers import (
    OPCDiagramSerializer,
    OPCDiagramSummarySerializer,
    OPCEdgeSerializer,
    OPCNodeSerializer,
)


def _validation_payload(exc):
    if hasattr(exc, 'message_dict'):
        return exc.message_dict
    if hasattr(exc, 'messages'):
        return exc.messages
    return str(exc)


class OPCDiagramViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = OPCDiagram.objects.select_related('item_revision__item', 'submitted_by', 'approved_by', 'released_by', 'superseded_by').prefetch_related('nodes', 'edges').annotate(
            node_count=Count('nodes', distinct=True),
            edge_count=Count('edges', distinct=True),
        )
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(part_code__icontains=search) | queryset.filter(title__icontains=search)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        item_revision_id = self.request.query_params.get('item_revision')
        if item_revision_id:
            queryset = queryset.filter(item_revision_id=item_revision_id)
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


    def _handle_lifecycle(self, service, **kwargs):
        try:
            diagram = service(self.get_object(), actor=self.request.user, **kwargs)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(OPCDiagramSerializer(diagram).data)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        return self._handle_lifecycle(submit_opc_revision)

    @action(detail=True, methods=['post'], url_path='return-to-draft')
    def return_to_draft(self, request, pk=None):
        return self._handle_lifecycle(return_opc_revision_to_draft)

    @action(detail=True, methods=['post'], url_path='return-to-review')
    def return_to_review(self, request, pk=None):
        return self._handle_lifecycle(return_opc_revision_to_review)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._handle_lifecycle(approve_opc_revision)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        effective_from = request.data.get('effective_from')
        parsed_date = parse_date(effective_from) if effective_from else None
        if effective_from and not parsed_date:
            raise DRFValidationError({'effective_from': 'Use YYYY-MM-DD date format.'})
        return self._handle_lifecycle(release_opc_revision, effective_from=parsed_date, supersede_current=False)

    @action(detail=True, methods=['post'])
    def supersede(self, request, pk=None):
        effective_from = request.data.get('effective_from')
        parsed_date = parse_date(effective_from) if effective_from else None
        if effective_from and not parsed_date:
            raise DRFValidationError({'effective_from': 'Use YYYY-MM-DD date format.'})
        return self._handle_lifecycle(release_opc_revision, effective_from=parsed_date, supersede_current=True)

    @action(detail=True, methods=['post'])
    def obsolete(self, request, pk=None):
        return self._handle_lifecycle(obsolete_opc_revision)

    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        revision_code = (request.data.get('revision') or '').strip()
        if not revision_code:
            raise DRFValidationError({'revision': 'New OPC revision code is required.'})
        return self._handle_lifecycle(clone_opc_revision, revision_code=revision_code)

    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None):
        return Response(validate_opc_graph(self.get_object()))

    @action(detail=False, methods=['get'], url_path='released')
    def released(self, request):
        item_revision_id = request.query_params.get('item_revision')
        if not item_revision_id:
            raise DRFValidationError({'item_revision': 'This query parameter is required.'})
        diagram = OPCDiagram.objects.filter(item_revision_id=item_revision_id, status=OPCDiagram.STATUS_RELEASED).order_by('-released_at', '-created_at').first()
        if not diagram:
            return Response(None, status=404)
        return Response(OPCDiagramSerializer(diagram).data)


class OPCNodeViewSet(viewsets.ModelViewSet):
    serializer_class = OPCNodeSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = OPCNode.objects.select_related('diagram')
        diagram_id = self.request.query_params.get('diagram')
        if diagram_id:
            queryset = queryset.filter(diagram_id=diagram_id)
        return queryset

    def perform_destroy(self, instance):
        try:
            ensure_node_editable(instance)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        instance.delete()


class OPCEdgeViewSet(viewsets.ModelViewSet):
    serializer_class = OPCEdgeSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = OPCEdge.objects.select_related('diagram', 'source', 'target')
        diagram_id = self.request.query_params.get('diagram')
        if diagram_id:
            queryset = queryset.filter(diagram_id=diagram_id)
        return queryset

    def perform_destroy(self, instance):
        try:
            ensure_edge_editable(instance)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        instance.delete()
