from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

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
from .serializers import (
    CodeSchemeSerializer,
    CodingOrganizationSerializer,
    ItemCategorySerializer,
    ItemRevisionSerializer,
    ItemSerializer,
    ItemTypeSerializer,
    ManufacturingVariantSerializer,
    ProcessStageCodeSerializer,
    VariantInputSerializer,
)


class CodingOrganizationViewSet(viewsets.ModelViewSet):
    queryset = CodingOrganization.objects.all()
    serializer_class = CodingOrganizationSerializer
    permission_classes = [IsAuthenticated]


class ItemTypeViewSet(viewsets.ModelViewSet):
    queryset = ItemType.objects.select_related('organization')
    serializer_class = ItemTypeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        organization_id = self.request.query_params.get('organization')
        if organization_id:
            queryset = queryset.filter(organization_id=organization_id)
        return queryset


class ItemCategoryViewSet(viewsets.ModelViewSet):
    queryset = ItemCategory.objects.select_related('organization', 'parent')
    serializer_class = ItemCategorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        organization_id = self.request.query_params.get('organization')
        if organization_id:
            queryset = queryset.filter(organization_id=organization_id)
        return queryset


class CodeSchemeViewSet(viewsets.ModelViewSet):
    queryset = CodeScheme.objects.select_related('organization', 'item_type')
    serializer_class = CodeSchemeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        organization_id = self.request.query_params.get('organization')
        if organization_id:
            queryset = queryset.filter(organization_id=organization_id)
        item_type_id = self.request.query_params.get('item_type')
        if item_type_id:
            queryset = queryset.filter(item_type_id=item_type_id)
        return queryset

    @action(detail=True, methods=['post'])
    def generate(self, request, pk=None):
        scheme = self.get_object()
        category = None
        category_id = request.data.get('category')
        if category_id:
            category = ItemCategory.objects.get(pk=category_id, organization=scheme.organization)
        revision = request.data.get('revision', '')
        stage = request.data.get('stage', '')
        return Response({'item_code': scheme.generate_code(category=category, revision=revision, stage=stage)})

    @action(detail=True, methods=['post'])
    def preview(self, request, pk=None):
        scheme = self.get_object()
        category = None
        category_id = request.data.get('category')
        if category_id:
            category = ItemCategory.objects.get(pk=category_id, organization=scheme.organization)
        revision = request.data.get('revision', '')
        stage = request.data.get('stage', '')
        return Response({'item_code': scheme.preview(category=category, revision=revision, stage=stage)})


class ItemViewSet(viewsets.ModelViewSet):
    queryset = Item.objects.select_related('organization', 'item_type', 'category', 'created_by').prefetch_related(
        'revisions',
        'manufacturing_variants',
        'manufacturing_variants__inputs',
        'manufacturing_variants__stage_codes',
    )
    serializer_class = ItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(item_code__icontains=search) |
                Q(name__icontains=search) |
                Q(drawing_no__icontains=search)
            )
        organization_id = self.request.query_params.get('organization')
        if organization_id:
            queryset = queryset.filter(organization_id=organization_id)
        item_type_id = self.request.query_params.get('item_type')
        if item_type_id:
            queryset = queryset.filter(item_type_id=item_type_id)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ItemRevisionViewSet(viewsets.ModelViewSet):
    queryset = ItemRevision.objects.select_related('item')
    serializer_class = ItemRevisionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        item_id = self.request.query_params.get('item')
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        return queryset


class ManufacturingVariantViewSet(viewsets.ModelViewSet):
    queryset = ManufacturingVariant.objects.select_related('item', 'opc_diagram').prefetch_related('inputs', 'stage_codes')
    serializer_class = ManufacturingVariantSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        item_id = self.request.query_params.get('item')
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        return queryset


class VariantInputViewSet(viewsets.ModelViewSet):
    queryset = VariantInput.objects.select_related('variant', 'linked_item')
    serializer_class = VariantInputSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        variant_id = self.request.query_params.get('variant')
        if variant_id:
            queryset = queryset.filter(variant_id=variant_id)
        return queryset


class ProcessStageCodeViewSet(viewsets.ModelViewSet):
    queryset = ProcessStageCode.objects.select_related('variant')
    serializer_class = ProcessStageCodeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        variant_id = self.request.query_params.get('variant')
        if variant_id:
            queryset = queryset.filter(variant_id=variant_id)
        return queryset
