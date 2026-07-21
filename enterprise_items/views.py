from django.db.models import Q
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.serializers import ValidationError as DRFValidationError

from auditlog.services import log_event

from .exceptions import EngineeringLifecycleError, EngineeringPermissionError
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
    ItemCodingScheme,
    ItemCodingTemplate,
    ItemCodingTemplateSegment,
    ItemIdentifier,
    ItemRevisionAttributeValue,
    Item,
    ItemCategory,
    ItemRevision,
    ItemType,
    ManufacturingVariant,
    ProcessStageCode,
    VariantInput,
)
from .permissions import CanManageEngineering
from .serializers import (
    BOMLineSerializer,
    BOMRevisionSerializer,
    BOMSerializer,
    AttributeDefinitionSerializer,
    AttributeEncodingOptionSerializer,
    AttributeEncodingRuleSerializer,
    ClassificationAttributeSerializer,
    CodePreviewSerializer,
    ControlledItemCreateSerializer,
    EngineeringReadinessSerializer,
    CodeSchemeSerializer,
    CodingOrganizationSerializer,
    ItemCategorySerializer,
    ItemClassificationSerializer,
    ItemCodingSchemeSerializer,
    ItemCodingTemplateSegmentSerializer,
    ItemCodingTemplateSerializer,
    ItemIdentifierSerializer,
    ItemRevisionAttributeValueSerializer,
    ItemRevisionSerializer,
    ItemSerializer,
    ItemTypeSerializer,
    ManufacturingVariantSerializer,
    ProcessStageCodeSerializer,
    VariantInputSerializer,
)
from .coding_services import (
    activate_template,
    clone_template,
    create_item_from_code,
    duplicate_check,
    preview_code,
    retire_template,
    validate_template,
)
from .coding_profile_services import (
    create_profile as create_coding_profile,
    decode_code as decode_profile_code,
    get_profile as get_coding_profile,
    list_profiles as list_coding_profiles,
    preview_with_profile,
    resolve_profile as resolve_coding_profile,
)
from .bom_services import (
    approve_bom_revision,
    clone_bom_revision,
    ensure_bom_line_editable,
    ensure_bom_revision_deletable,
    obsolete_bom_revision,
    release_bom_revision,
    return_bom_revision_to_draft,
    return_bom_revision_to_review,
    submit_bom_revision,
    validate_bom_revision_for_release,
)
from .readiness import evaluate_item_revision_readiness
from .workspace_services import (
    apply_part_list_filters,
    audit_history_for_item,
    change_item_status,
    dashboard_metrics,
    part_detail_summary,
    part_list_row,
    revision_attribute_workspace,
    revision_summary,
    sort_part_list,
)
from .services import (
    approve_revision,
    ensure_revision_deletable,
    obsolete_revision,
    release_revision,
    return_revision_to_draft,
    return_revision_to_review,
    submit_revision_for_review,
)


def _validation_payload(exc):
    if hasattr(exc, 'message_dict'):
        return exc.message_dict
    if hasattr(exc, 'messages'):
        return exc.messages
    return str(exc)


class ItemClassificationViewSet(viewsets.ModelViewSet):
    queryset = ItemClassification.objects.select_related('organization', 'parent').prefetch_related('children')
    serializer_class = ItemClassificationSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        qs = super().get_queryset()
        organization = self.request.query_params.get('organization')
        parent = self.request.query_params.get('parent')
        if organization:
            qs = qs.filter(organization_id=organization)
        if parent == 'null':
            qs = qs.filter(parent__isnull=True)
        elif parent:
            qs = qs.filter(parent_id=parent)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        log_event('item_classification_created', target=instance, category='business')

    def perform_update(self, serializer):
        before = serializer.instance.path
        instance = serializer.save()
        log_event('item_classification_updated', target=instance, category='business', changes={'path': {'old': before, 'new': instance.path}})

    @action(detail=False, methods=['get'])
    def tree(self, request):
        qs = self.get_queryset().filter(parent__isnull=True).order_by('sort_order', 'code')
        def node(obj):
            return {**ItemClassificationSerializer(obj).data, 'children': [node(child) for child in obj.children.all().order_by('sort_order', 'code')]}
        return Response([node(root) for root in qs])

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        obj = self.get_object()
        obj.is_active = True
        obj.save(update_fields=['is_active', 'updated_at'])
        log_event('item_classification_activated', target=obj, category='business')
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        obj = self.get_object()
        if obj.children.filter(is_active=True).exists() or obj.items.exists():
            raise DRFValidationError({'classification': 'Classification with active children or items cannot be deactivated.'})
        obj.is_active = False
        obj.save(update_fields=['is_active', 'updated_at'])
        log_event('item_classification_deactivated', target=obj, category='business')
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['get'], url_path='effective-attributes')
    def effective_attributes(self, request, pk=None):
        from .models import get_effective_classification_attributes
        assignments = get_effective_classification_attributes(self.get_object())
        return Response(ClassificationAttributeSerializer(assignments, many=True).data)


class AttributeDefinitionViewSet(viewsets.ModelViewSet):
    queryset = AttributeDefinition.objects.select_related('organization')
    serializer_class = AttributeDefinitionSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        qs = super().get_queryset()
        org = self.request.query_params.get('organization')
        if org:
            qs = qs.filter(organization_id=org)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        log_event('attribute_definition_created', target=instance, category='business')

    def perform_update(self, serializer):
        instance = serializer.save()
        log_event('attribute_definition_updated', target=instance, category='business')

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        obj = self.get_object(); obj.is_active = True; obj.save(update_fields=['is_active', 'updated_at'])
        log_event('attribute_definition_activated', target=obj, category='business')
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        obj = self.get_object(); obj.is_active = False; obj.save(update_fields=['is_active', 'updated_at'])
        log_event('attribute_definition_deactivated', target=obj, category='business')
        return Response(self.get_serializer(obj).data)


class ClassificationAttributeViewSet(viewsets.ModelViewSet):
    queryset = ClassificationAttribute.objects.select_related('classification', 'attribute_definition')
    serializer_class = ClassificationAttributeSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        qs = super().get_queryset()
        classification = self.request.query_params.get('classification')
        if classification:
            qs = qs.filter(classification_id=classification)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        log_event('classification_attribute_assigned', target=instance, category='business')

    def perform_update(self, serializer):
        instance = serializer.save()
        log_event('classification_attribute_updated', target=instance, category='business')


class ItemCodingSchemeViewSet(viewsets.ModelViewSet):
    queryset = ItemCodingScheme.objects.select_related('organization')
    serializer_class = ItemCodingSchemeSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        qs = super().get_queryset()
        org = self.request.query_params.get('organization')
        if org:
            qs = qs.filter(organization_id=org)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        log_event('item_coding_scheme_created', target=instance, category='business')

    def perform_update(self, serializer):
        instance = serializer.save()
        log_event('item_coding_scheme_updated', target=instance, category='business')

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        obj = self.get_object(); obj.is_active = True; obj.save(update_fields=['is_active', 'updated_at'])
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        obj = self.get_object(); obj.is_active = False; obj.save(update_fields=['is_active', 'updated_at'])
        return Response(self.get_serializer(obj).data)


class AttributeEncodingRuleViewSet(viewsets.ModelViewSet):
    queryset = AttributeEncodingRule.objects.prefetch_related('options')
    serializer_class = AttributeEncodingRuleSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        log_event('attribute_encoding_rule_created', target=instance, category='business')

    def perform_update(self, serializer):
        instance = serializer.save()
        log_event('attribute_encoding_rule_updated', target=instance, category='business')


class AttributeEncodingOptionViewSet(viewsets.ModelViewSet):
    queryset = AttributeEncodingOption.objects.select_related('rule')
    serializer_class = AttributeEncodingOptionSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]


class ItemCodingTemplateViewSet(viewsets.ModelViewSet):
    queryset = ItemCodingTemplate.objects.select_related('coding_scheme', 'classification').prefetch_related('segments')
    serializer_class = ItemCodingTemplateSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        qs = super().get_queryset()
        classification = self.request.query_params.get('classification')
        status_value = self.request.query_params.get('status')
        if classification:
            qs = qs.filter(classification_id=classification)
        if status_value:
            qs = qs.filter(status=status_value)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        log_event('item_coding_template_created', target=instance, category='business')

    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None):
        try:
            return Response(validate_template(self.get_object()))
        except EngineeringLifecycleError as exc:
            return Response({'valid': False, 'errors': exc.args[0]}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        try:
            return Response(self.get_serializer(activate_template(self.get_object(), actor=request.user)).data)
        except (EngineeringLifecycleError, EngineeringPermissionError) as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def retire(self, request, pk=None):
        try:
            return Response(self.get_serializer(retire_template(self.get_object(), actor=request.user)).data)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc))

    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        try:
            clone = clone_template(self.get_object(), actor=request.user, version=request.data.get('version'))
            return Response(self.get_serializer(clone).data, status=status.HTTP_201_CREATED)
        except (EngineeringLifecycleError, EngineeringPermissionError) as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)


class ItemCodingTemplateSegmentViewSet(viewsets.ModelViewSet):
    queryset = ItemCodingTemplateSegment.objects.select_related('template', 'attribute_definition', 'encoding_rule')
    serializer_class = ItemCodingTemplateSegmentSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        qs = super().get_queryset()
        template = self.request.query_params.get('template')
        if template:
            qs = qs.filter(template_id=template)
        return qs


class ItemRevisionAttributeValueViewSet(viewsets.ModelViewSet):
    queryset = ItemRevisionAttributeValue.objects.select_related('item_revision__item', 'attribute_definition')
    serializer_class = ItemRevisionAttributeValueSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        qs = super().get_queryset()
        revision = self.request.query_params.get('item_revision')
        if revision:
            qs = qs.filter(item_revision_id=revision)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        log_event('item_revision_attribute_value_created', target=instance, category='business')


class ItemIdentifierViewSet(viewsets.ModelViewSet):
    queryset = ItemIdentifier.objects.select_related('item')
    serializer_class = ItemIdentifierSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        qs = super().get_queryset()
        item = self.request.query_params.get('item')
        if item:
            qs = qs.filter(item_id=item)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        log_event('item_identifier_created', target=instance, category='business')


class PartCodingWorkflowViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, CanManageEngineering]

    @action(detail=False, methods=['post'], url_path='preview')
    def preview(self, request):
        serializer = CodePreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            return Response(preview_code(**data))
        except (DjangoValidationError, EngineeringLifecycleError) as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='duplicate-check')
    def duplicates(self, request):
        serializer = CodePreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            preview = preview_code(**data)
            return Response({'duplicates': preview['duplicates'], 'semantic_identity_hash': preview['semantic_identity_hash']})
        except (DjangoValidationError, EngineeringLifecycleError) as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='controlled-create')
    def controlled_create(self, request):
        serializer = ControlledItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = create_item_from_code(actor=request.user, **serializer.validated_data)
            return Response({
                'item': ItemSerializer(result['item']).data,
                'revision': ItemRevisionSerializer(result['revision']).data,
                'segment_breakdown': result['segment_breakdown'],
                'duplicates': result['duplicates'],
            }, status=status.HTTP_201_CREATED)
        except (DjangoValidationError, EngineeringLifecycleError, EngineeringPermissionError) as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)


class CodingViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, CanManageEngineering]

    @action(detail=False, methods=['get', 'post'], url_path='profiles')
    def profiles(self, request):
        if request.method == 'GET':
            return Response({'results': list_coding_profiles()})
        try:
            organization = CodingOrganization.objects.get(pk=request.data.get('organization'))
            classification = ItemClassification.objects.get(pk=request.data.get('classification'))
            profile = create_coding_profile(
                actor=request.user,
                organization=organization,
                classification=classification,
                code=request.data.get('code', ''),
                name=request.data.get('name', ''),
                strategy=request.data.get('strategy') or ItemCodingScheme.STRATEGY_HYBRID,
                family_prefix=request.data.get('family_prefix', ''),
                separator=request.data.get('separator', '-'),
                sequence_enabled=bool(request.data.get('sequence_enabled', True)),
                sequence_length=int(request.data.get('sequence_length') or 5),
                maximum_code_length=int(request.data.get('maximum_code_length') or 80),
                description=request.data.get('description', ''),
            )
            return Response(profile, status=status.HTTP_201_CREATED)
        except (DjangoValidationError, EngineeringLifecycleError, ValueError) as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path=r'profiles/(?P<profile_id>[0-9a-f-]{36})')
    def profile_detail(self, request, profile_id=None):
        return Response(get_coding_profile(profile_id))

    @action(detail=False, methods=['post'], url_path=r'profiles/(?P<profile_id>[^/.]+)/validate')
    def profile_validate(self, request, profile_id=None):
        template = ItemCodingTemplate.objects.get(pk=profile_id)
        try:
            return Response(validate_template(template))
        except EngineeringLifecycleError as exc:
            return Response({'valid': False, 'errors': exc.args[0] if exc.args else {}}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path=r'profiles/(?P<profile_id>[^/.]+)/activate')
    def profile_activate(self, request, profile_id=None):
        template = ItemCodingTemplate.objects.get(pk=profile_id)
        try:
            activated = activate_template(template, actor=request.user)
            return Response(get_coding_profile(str(activated.pk)))
        except (EngineeringLifecycleError, EngineeringPermissionError) as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path=r'profiles/(?P<profile_id>[^/.]+)/clone')
    def profile_clone(self, request, profile_id=None):
        template = ItemCodingTemplate.objects.get(pk=profile_id)
        try:
            clone = clone_template(template, actor=request.user, version=request.data.get('version'))
            return Response(get_coding_profile(str(clone.pk)), status=status.HTTP_201_CREATED)
        except (EngineeringLifecycleError, EngineeringPermissionError) as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path=r'profiles/(?P<profile_id>[^/.]+)/explain')
    def profile_explain(self, request, profile_id=None):
        profile = get_coding_profile(profile_id)
        return Response({'profile': profile, 'explanation': 'This Coding Profile is backed by one Coding Scheme, one Coding Template, ordered Template Segments, and optional Encoding Rules.'})

    @action(detail=False, methods=['post'], url_path='profiles/resolve')
    def resolve(self, request):
        try:
            classification = ItemClassification.objects.get(pk=request.data.get('classification'))
            return Response(resolve_coding_profile(classification=classification))
        except (ItemClassification.DoesNotExist, EngineeringLifecycleError) as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='preview')
    def preview(self, request):
        serializer = CodePreviewSerializer(data={
            **request.data,
            'coding_scheme': request.data.get('coding_scheme') or None,
        })
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            return Response(preview_with_profile(
                organization=data['organization'],
                classification=data['classification'],
                attributes=data.get('attributes') or {},
                profile=request.data.get('profile') or request.data.get('coding_profile'),
                manual_code=data.get('manual_code', ''),
                identifiers=data.get('identifiers'),
                name=data.get('name', ''),
            ))
        except (DjangoValidationError, EngineeringLifecycleError) as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='simulate')
    def simulate(self, request):
        response = self.preview(request)
        if response.status_code < 400 and isinstance(response.data, dict):
            response.data['simulation'] = {'committed_sequence': False, 'safe_to_repeat': True}
        return response

    @action(detail=False, methods=['post'], url_path='decode')
    def decode(self, request):
        part_number = (request.data.get('part_number') or '').strip()
        if not part_number:
            return Response({'part_number': 'This field is required.'}, status=status.HTTP_400_BAD_REQUEST)
        classification = None
        if request.data.get('classification'):
            classification = ItemClassification.objects.get(pk=request.data.get('classification'))
        return Response(decode_profile_code(part_number=part_number, profile=request.data.get('profile'), classification=classification))


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
    queryset = Item.objects.select_related('organization', 'item_type', 'category', 'classification', 'coding_scheme', 'coding_template', 'created_by').prefetch_related(
        'revisions',
        'identifiers',
        'manufacturing_variants',
        'manufacturing_variants__inputs',
        'manufacturing_variants__stage_codes',
    )
    serializer_class = ItemSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

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
        make_or_buy = self.request.query_params.get('make_or_buy')
        if make_or_buy:
            queryset = queryset.filter(make_or_buy=make_or_buy)
        tracking_mode = self.request.query_params.get('tracking_mode')
        if tracking_mode:
            queryset = queryset.filter(tracking_mode=tracking_mode)
        is_active = self.request.query_params.get('is_active')
        if is_active in {'true', 'false'}:
            queryset = queryset.filter(is_active=is_active == 'true')
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


    @action(detail=False, methods=['get'], url_path='workspace-dashboard')
    def workspace_dashboard(self, request):
        return Response(dashboard_metrics())

    @action(detail=False, methods=['get'], url_path='workspace')
    def workspace(self, request):
        queryset = Item.objects.select_related('classification', 'coding_scheme', 'coding_template').prefetch_related('revisions', 'identifiers')
        queryset = apply_part_list_filters(queryset, request.query_params)
        queryset = sort_part_list(queryset, request.query_params.get('ordering'))
        paginator = PageNumberPagination()
        paginator.page_size = min(int(request.query_params.get('page_size', 25)), 100)
        page = paginator.paginate_queryset(queryset, request, view=self)
        rows = [part_list_row(item) for item in page]
        return paginator.get_paginated_response(rows)

    @action(detail=True, methods=['get'], url_path='workspace-summary')
    def workspace_summary(self, request, pk=None):
        item = Item.objects.select_related('classification', 'coding_scheme', 'coding_template', 'item_type', 'organization').prefetch_related('revisions', 'identifiers').get(pk=pk)
        return Response(part_detail_summary(item))

    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        return Response({'results': audit_history_for_item(self.get_object())})

    @action(detail=True, methods=['post'], url_path='status-change')
    def status_change(self, request, pk=None):
        try:
            item = change_item_status(self.get_object(), actor=request.user, status=request.data.get('status'), reason=request.data.get('reason', ''))
            return Response(self.get_serializer(item).data)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc))
        except EngineeringLifecycleError as exc:
            return Response(_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        item = self.get_object()
        old_active = item.is_active
        item.is_active = True
        if item.status == 'BLOCKED':
            item.status = 'ACTIVE'
        item.save(update_fields=['is_active', 'status', 'updated_at'])
        log_event(
            'enterprise_item_activated',
            target=item,
            category='business',
            changes={'is_active': {'old': old_active, 'new': True}},
            extra={'item_code': item.item_code},
        )
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        item = self.get_object()
        old_active = item.is_active
        old_status = item.status
        item.is_active = False
        if item.status == 'ACTIVE':
            item.status = 'BLOCKED'
        item.save(update_fields=['is_active', 'status', 'updated_at'])
        log_event(
            'enterprise_item_deactivated',
            target=item,
            category='business',
            changes={
                'is_active': {'old': old_active, 'new': False},
                'status': {'old': old_status, 'new': item.status},
            },
            extra={'item_code': item.item_code},
        )
        return Response(self.get_serializer(item).data)


class ItemRevisionViewSet(viewsets.ModelViewSet):
    queryset = ItemRevision.objects.select_related(
        'item', 'submitted_by', 'approved_by', 'released_by', 'superseded_by'
    )
    serializer_class = ItemRevisionSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]



    @action(detail=True, methods=['get'], url_path='workspace-summary')
    def workspace_summary(self, request, pk=None):
        revision = self.get_object()
        return Response(revision_summary(revision))

    @action(detail=True, methods=['get'], url_path='technical-attributes')
    def technical_attributes(self, request, pk=None):
        revision = ItemRevision.objects.select_related('item__classification').prefetch_related('attribute_values__attribute_definition').get(pk=pk)
        return Response({'results': revision_attribute_workspace(revision)})
    def get_queryset(self):
        queryset = super().get_queryset()
        item_id = self.request.query_params.get('item')
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(revision__icontains=search) |
                Q(title__icontains=search) |
                Q(drawing_no__icontains=search) |
                Q(specification__icontains=search)
            )
        effective_date = self.request.query_params.get('effective_date')
        if effective_date:
            parsed_date = parse_date(effective_date)
            if parsed_date:
                queryset = queryset.filter(effective_from__lte=parsed_date).filter(
                    Q(effective_to__isnull=True) | Q(effective_to__gte=parsed_date)
                )
        return queryset

    def _handle_lifecycle(self, service, **kwargs):
        try:
            revision = service(self.get_object(), actor=self.request.user, **kwargs)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(revision).data)

    def perform_destroy(self, instance):
        try:
            ensure_revision_deletable(instance)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        log_event(
            'item_revision_deleted',
            target=instance,
            category='business',
            extra={'item_id': str(instance.item_id), 'revision': instance.revision},
        )
        instance.delete()

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        return self._handle_lifecycle(submit_revision_for_review)

    @action(detail=True, methods=['post'], url_path='return-to-draft')
    def return_to_draft(self, request, pk=None):
        return self._handle_lifecycle(return_revision_to_draft)

    @action(detail=True, methods=['post'], url_path='return-to-review')
    def return_to_review(self, request, pk=None):
        return self._handle_lifecycle(return_revision_to_review)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._handle_lifecycle(approve_revision)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        effective_from = request.data.get('effective_from')
        parsed_date = parse_date(effective_from) if effective_from else None
        if effective_from and not parsed_date:
            raise DRFValidationError({'effective_from': 'Use YYYY-MM-DD date format.'})
        return self._handle_lifecycle(release_revision, effective_from=parsed_date, supersede_current=False)

    @action(detail=True, methods=['post'])
    def supersede(self, request, pk=None):
        effective_from = request.data.get('effective_from')
        parsed_date = parse_date(effective_from) if effective_from else None
        if effective_from and not parsed_date:
            raise DRFValidationError({'effective_from': 'Use YYYY-MM-DD date format.'})
        return self._handle_lifecycle(release_revision, effective_from=parsed_date, supersede_current=True)

    @action(detail=True, methods=['post'])
    def obsolete(self, request, pk=None):
        return self._handle_lifecycle(obsolete_revision)


class BOMViewSet(viewsets.ModelViewSet):
    queryset = BOM.objects.select_related('parent_item_revision__item', 'created_by').prefetch_related('revisions')
    serializer_class = BOMSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = super().get_queryset()
        item_revision_id = self.request.query_params.get('item_revision')
        if item_revision_id:
            queryset = queryset.filter(parent_item_revision_id=item_revision_id)
        bom_type = self.request.query_params.get('bom_type')
        if bom_type:
            queryset = queryset.filter(bom_type=bom_type)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['get'], url_path='released')
    def released(self, request):
        item_revision_id = request.query_params.get('item_revision')
        if not item_revision_id:
            raise DRFValidationError({'item_revision': 'This query parameter is required.'})
        revision = BOMRevision.objects.filter(
            bom__parent_item_revision_id=item_revision_id,
            status=BOMRevision.STATUS_RELEASED,
        ).select_related('bom__parent_item_revision__item').prefetch_related('lines').order_by('-released_at', '-created_at').first()
        if not revision:
            return Response(None, status=status.HTTP_404_NOT_FOUND)
        return Response(BOMRevisionSerializer(revision).data)


class BOMRevisionViewSet(viewsets.ModelViewSet):
    queryset = BOMRevision.objects.select_related(
        'bom__parent_item_revision__item', 'submitted_by', 'approved_by', 'released_by', 'superseded_by', 'created_by'
    ).prefetch_related('lines__component_item_revision__item')
    serializer_class = BOMRevisionSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = super().get_queryset()
        bom_id = self.request.query_params.get('bom')
        if bom_id:
            queryset = queryset.filter(bom_id=bom_id)
        item_revision_id = self.request.query_params.get('item_revision')
        if item_revision_id:
            queryset = queryset.filter(bom__parent_item_revision_id=item_revision_id)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_destroy(self, instance):
        try:
            ensure_bom_revision_deletable(instance)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        log_event('bom_revision_deleted', target=instance, category='business', extra={'bom_id': str(instance.bom_id), 'revision': instance.revision})
        instance.delete()

    def _handle_lifecycle(self, service, **kwargs):
        try:
            revision = service(self.get_object(), actor=self.request.user, **kwargs)
        except EngineeringPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response(self.get_serializer(revision).data)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        return self._handle_lifecycle(submit_bom_revision)

    @action(detail=True, methods=['post'], url_path='return-to-draft')
    def return_to_draft(self, request, pk=None):
        return self._handle_lifecycle(return_bom_revision_to_draft)

    @action(detail=True, methods=['post'], url_path='return-to-review')
    def return_to_review(self, request, pk=None):
        return self._handle_lifecycle(return_bom_revision_to_review)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._handle_lifecycle(approve_bom_revision)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        effective_from = request.data.get('effective_from')
        parsed_date = parse_date(effective_from) if effective_from else None
        if effective_from and not parsed_date:
            raise DRFValidationError({'effective_from': 'Use YYYY-MM-DD date format.'})
        return self._handle_lifecycle(release_bom_revision, effective_from=parsed_date, supersede_current=False)

    @action(detail=True, methods=['post'])
    def supersede(self, request, pk=None):
        effective_from = request.data.get('effective_from')
        parsed_date = parse_date(effective_from) if effective_from else None
        if effective_from and not parsed_date:
            raise DRFValidationError({'effective_from': 'Use YYYY-MM-DD date format.'})
        return self._handle_lifecycle(release_bom_revision, effective_from=parsed_date, supersede_current=True)

    @action(detail=True, methods=['post'])
    def obsolete(self, request, pk=None):
        return self._handle_lifecycle(obsolete_bom_revision)

    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        revision_code = (request.data.get('revision') or '').strip()
        if not revision_code:
            raise DRFValidationError({'revision': 'New BOM revision code is required.'})
        return self._handle_lifecycle(clone_bom_revision, revision_code=revision_code)

    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None):
        try:
            validate_bom_revision_for_release(self.get_object())
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        return Response({'valid': True, 'errors': {}, 'warnings': []})


class BOMLineViewSet(viewsets.ModelViewSet):
    queryset = BOMLine.objects.select_related('bom_revision__bom__parent_item_revision__item', 'component_item_revision__item')
    serializer_class = BOMLineSerializer
    permission_classes = [IsAuthenticated, CanManageEngineering]

    def get_queryset(self):
        queryset = super().get_queryset()
        bom_revision_id = self.request.query_params.get('bom_revision')
        if bom_revision_id:
            queryset = queryset.filter(bom_revision_id=bom_revision_id)
        return queryset

    def perform_destroy(self, instance):
        try:
            ensure_bom_line_editable(instance)
        except EngineeringLifecycleError as exc:
            raise DRFValidationError(_validation_payload(exc)) from exc
        log_event('bom_line_deleted', target=instance, category='business', extra={'bom_revision_id': str(instance.bom_revision_id), 'sequence': instance.sequence})
        instance.delete()


class EngineeringReadinessViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def retrieve(self, request, pk=None):
        revision = ItemRevision.objects.select_related('item').get(pk=pk)
        data = evaluate_item_revision_readiness(revision)
        return Response(EngineeringReadinessSerializer(data).data)


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
