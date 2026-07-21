from django.db.models import Count
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .engineering_domain_errors import EngineeringDomainError, EngineeringValidationError
from .engineering_domain_services import (
    CodeDecodeService, CodeDefinitionService, CodeDefinitionValidationService, CodeDefinitionVersionService,
    CodeSegmentService, ParameterDefinitionService, ParameterMetadataService, PartCreationService,
    StructureDefinitionService, StructureValidationService, TechnicalDataTemplateService, TechnicalDataValidationService,
)
from .engineering_api_serializers import *
from .models import CodeDefinition, CodeDefinitionVersion, Item, ParameterDefinition, ParameterMetadataField, StructureDefinition, TechnicalDataTemplate
from .permissions import CanManageEngineering


def domain_response(exc):
    return Response(exc.as_payload(), status=getattr(exc, 'status_code', 400))


class EngineeringParameterViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, CanManageEngineering]
    def list(self, request):
        qs = ParameterDefinition.objects.annotate(option_count=Count('options')).order_by('code')
        org = request.query_params.get('organization')
        if org: qs = qs.filter(organization_id=org)
        return Response(ParameterReadSerializer(qs, many=True).data)
    def retrieve(self, request, pk=None): return Response(ParameterReadSerializer(ParameterDefinition.objects.annotate(option_count=Count('options')).get(pk=pk)).data)
    def create(self, request):
        ser=ParameterCreateSerializer(data=request.data); ser.is_valid(raise_exception=True)
        try: obj=ParameterDefinitionService().create(actor=request.user, **ser.validated_data); return Response(ParameterReadSerializer(obj).data, status=201)
        except EngineeringDomainError as exc: return domain_response(exc)
    def partial_update(self, request, pk=None):
        ser=ParameterUpdateSerializer(data=request.data, partial=True); ser.is_valid(raise_exception=True)
        try: return Response(ParameterReadSerializer(ParameterDefinitionService().update(ParameterDefinition.objects.get(pk=pk), actor=request.user, **ser.validated_data)).data)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        try: return Response(ParameterReadSerializer(ParameterDefinitionService().activate(ParameterDefinition.objects.get(pk=pk), actor=request.user)).data)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        try: return Response(ParameterReadSerializer(ParameterDefinitionService().deactivate(ParameterDefinition.objects.get(pk=pk), actor=request.user)).data)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        try: return Response(ParameterReadSerializer(ParameterDefinitionService().clone(ParameterDefinition.objects.get(pk=pk), actor=request.user, code=request.data.get('code'), name=request.data.get('name'))).data, status=201)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=False, methods=['post'])
    def bulk(self, request):
        ser=BulkParameterSerializer(data=request.data); ser.is_valid(raise_exception=True)
        try:
            result=ParameterDefinitionService().bulk_create(actor=request.user, **ser.validated_data)
            return Response({'created': ParameterReadSerializer(result['created'], many=True).data, 'errors': result['errors'], 'committed': result['committed']}, status=201 if result['committed'] else 400)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['post'], url_path='options/bulk')
    def options_bulk(self, request, pk=None):
        try: return Response(ParameterOptionSerializer(ParameterDefinitionService().bulk_options(ParameterDefinition.objects.get(pk=pk), options=request.data.get('options', [])), many=True).data)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['get','put'])
    def metadata(self, request, pk=None):
        parameter=ParameterDefinition.objects.get(pk=pk)
        if request.method == 'GET': return Response([{'field': str(v.field_definition_id), 'value': v.value} for v in parameter.metadata_values.all()])
        try: return Response([{'field': str(v.field_definition_id), 'value': v.value} for v in ParameterMetadataService().set_values(parameter, values=request.data.get('values', {}))])
        except EngineeringDomainError as exc: return domain_response(exc)


class EngineeringMetadataFieldViewSet(viewsets.ModelViewSet):
    permission_classes=[IsAuthenticated, CanManageEngineering]
    queryset=ParameterMetadataField.objects.all().order_by('code')
    serializer_class=MetadataFieldSerializer


class EngineeringStructureViewSet(viewsets.ViewSet):
    permission_classes=[IsAuthenticated, CanManageEngineering]
    def list(self, request): return Response(StructureReadSerializer(StructureDefinition.objects.annotate(parameter_count=Count('parameters')).order_by('code'), many=True).data)
    def retrieve(self, request, pk=None): return Response(StructureReadSerializer(StructureDefinition.objects.annotate(parameter_count=Count('parameters')).get(pk=pk)).data)
    def create(self, request):
        ser=StructureCreateSerializer(data=request.data); ser.is_valid(raise_exception=True)
        try: return Response(StructureReadSerializer(StructureDefinitionService().create(actor=request.user, **ser.validated_data)).data, status=201)
        except EngineeringDomainError as exc: return domain_response(exc)
    def partial_update(self, request, pk=None):
        try: return Response(StructureReadSerializer(StructureDefinitionService().update(StructureDefinition.objects.get(pk=pk), actor=request.user, **request.data)).data)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['get','put'])
    def parameters(self, request, pk=None):
        s=StructureDefinition.objects.get(pk=pk)
        if request.method=='GET': return Response(StructureParameterSerializer(s.parameters.all(), many=True).data)
        try: return Response(StructureParameterSerializer(StructureDefinitionService().set_parameters(s, rows=request.data.get('rows', [])), many=True).data)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['post'], url_path='parameters/reorder')
    def parameters_reorder(self, request, pk=None):
        try: return Response(StructureParameterSerializer(StructureDefinitionService().reorder(StructureDefinition.objects.get(pk=pk), ordered_ids=request.data.get('ordered_ids', [])), many=True).data)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None): return Response(StructureValidationService().validate(StructureDefinition.objects.get(pk=pk)))


class EngineeringCodeDefinitionViewSet(viewsets.ViewSet):
    permission_classes=[IsAuthenticated, CanManageEngineering]
    def list(self, request): return Response(CodeDefinitionReadSerializer(CodeDefinition.objects.select_related('structure').order_by('code'), many=True).data)
    def retrieve(self, request, pk=None): return Response(CodeDefinitionReadSerializer(CodeDefinition.objects.get(pk=pk)).data)
    def create(self, request):
        ser=CodeDefinitionCreateSerializer(data=request.data); ser.is_valid(raise_exception=True)
        try: return Response(CodeDefinitionReadSerializer(CodeDefinitionService().create(actor=request.user, **ser.validated_data)).data, status=201)
        except EngineeringDomainError as exc: return domain_response(exc)
    def partial_update(self, request, pk=None):
        obj=CodeDefinition.objects.get(pk=pk)
        for k,v in request.data.items():
            if hasattr(obj,k): setattr(obj,k,v)
        try: obj.save(); return Response(CodeDefinitionReadSerializer(obj).data)
        except Exception as exc: return domain_response(EngineeringValidationError(str(exc)))
    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        src=CodeDefinition.objects.get(pk=pk); obj=CodeDefinitionService().create(actor=request.user, organization=src.organization, structure=src.structure, code=request.data.get('code', f'{src.code}_COPY'), name=request.data.get('name', f'{src.name} Copy'), separator=src.separator, maximum_length=src.maximum_length)
        return Response(CodeDefinitionReadSerializer(obj).data, status=201)
    @action(detail=True, methods=['get','post'])
    def versions(self, request, pk=None):
        d=CodeDefinition.objects.get(pk=pk)
        if request.method=='GET': return Response(CodeVersionSerializer(d.versions.all(), many=True).data)
        return Response(CodeVersionSerializer(CodeDefinitionVersionService().create_initial_draft(d, actor=request.user)).data, status=201)
    @action(detail=True, methods=['get','patch','post'], url_path='versions/(?P<version_id>[^/.]+)')
    def version_detail(self, request, pk=None, version_id=None):
        v=CodeDefinitionVersion.objects.get(pk=version_id, code_definition_id=pk)
        if request.method=='GET': return Response(CodeVersionSerializer(v).data)
        if request.method=='PATCH':
            if v.status != CodeDefinitionVersion.STATUS_DRAFT: return domain_response(ImmutableVersionError('Only Draft versions can be updated.'))
            v.configuration_snapshot=request.data.get('configuration_snapshot', v.configuration_snapshot); v.save(); return Response(CodeVersionSerializer(v).data)
        action_name=request.data.get('action')
        try:
            if action_name=='clone': return Response(CodeVersionSerializer(CodeDefinitionVersionService().clone(v, actor=request.user)).data, status=201)
            if action_name=='activate': return Response(CodeVersionSerializer(CodeDefinitionVersionService().activate(v, actor=request.user)).data)
            if action_name=='validate': return Response(CodeDefinitionValidationService().validate(v))
            return Response({'detail':'Unknown action.'}, status=400)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['get','put'], url_path='versions/(?P<version_id>[^/.]+)/segments')
    def version_segments(self, request, pk=None, version_id=None):
        v=CodeDefinitionVersion.objects.get(pk=version_id, code_definition_id=pk)
        if request.method=='GET': return Response(CodeSegmentSerializer(v.segments.all(), many=True).data)
        try: return Response(CodeSegmentSerializer(CodeSegmentService().set_segments(v, rows=request.data.get('rows', [])), many=True).data)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['post'], url_path='versions/(?P<version_id>[^/.]+)/segments/reorder')
    def segment_reorder(self, request, pk=None, version_id=None):
        try: return Response(CodeSegmentSerializer(CodeSegmentService().reorder(CodeDefinitionVersion.objects.get(pk=version_id, code_definition_id=pk), ordered_ids=request.data.get('ordered_ids', [])), many=True).data)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['post'])
    def simulate(self, request, pk=None):
        ser=PreviewSerializer(data=request.data); ser.is_valid(raise_exception=True)
        try: return Response(PartCreationService().preview(**ser.validated_data))
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['post'])
    def decode(self, request, pk=None):
        ser=DecodeSerializer(data=request.data); ser.is_valid(raise_exception=True)
        try: return Response(CodeDecodeService().decode(code=ser.validated_data['code'], definition=CodeDefinition.objects.get(pk=pk)))
        except EngineeringDomainError as exc: return domain_response(exc)
class EngineeringTechnicalTemplateViewSet(viewsets.ViewSet):
    permission_classes=[IsAuthenticated, CanManageEngineering]
    def list(self, request): return Response(TechnicalTemplateSerializer(TechnicalDataTemplate.objects.order_by('code'), many=True).data)
    def retrieve(self, request, pk=None): return Response(TechnicalTemplateSerializer(TechnicalDataTemplate.objects.get(pk=pk)).data)
    def create(self, request):
        ser=TechnicalTemplateSerializer(data=request.data); ser.is_valid(raise_exception=True)
        try: return Response(TechnicalTemplateSerializer(TechnicalDataTemplateService().create(actor=request.user, organization=ser.validated_data['organization'], structure=ser.validated_data['structure'], code=ser.validated_data['code'], name=ser.validated_data['name'], description=ser.validated_data.get('description',''))).data, status=201)
        except EngineeringDomainError as exc: return domain_response(exc)
    def partial_update(self, request, pk=None):
        obj=TechnicalDataTemplate.objects.get(pk=pk)
        for k,v in request.data.items():
            if hasattr(obj,k): setattr(obj,k,v)
        try: obj.save(); return Response(TechnicalTemplateSerializer(obj).data)
        except Exception as exc: return domain_response(EngineeringValidationError(str(exc)))
    @action(detail=True, methods=['get','put'])
    def fields(self, request, pk=None):
        t=TechnicalDataTemplate.objects.get(pk=pk)
        if request.method=='GET': return Response(TechnicalFieldSerializer(t.fields.all(), many=True).data)
        try: return Response(TechnicalFieldSerializer(TechnicalDataTemplateService().set_fields(t, rows=request.data.get('rows', [])), many=True).data)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['post'], url_path='fields/reorder')
    def fields_reorder(self, request, pk=None):
        t=TechnicalDataTemplate.objects.get(pk=pk)
        for i, field_id in enumerate(request.data.get('ordered_ids', [])):
            f=t.fields.get(pk=field_id); f.sort_order=(i+1)*10; f.save()
        return Response(TechnicalFieldSerializer(t.fields.all(), many=True).data)
    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None): return Response(TechnicalDataValidationService().validate(template=TechnicalDataTemplate.objects.get(pk=pk), part_values=request.data.get('part_values', {}), revision_values=request.data.get('revision_values', {})))


class EngineeringPartViewSet(viewsets.ViewSet):
    permission_classes=[IsAuthenticated, CanManageEngineering]
    @action(detail=False, methods=['post'])
    def preview(self, request):
        ser=PreviewSerializer(data=request.data); ser.is_valid(raise_exception=True)
        try: return Response(PartCreationService().preview(**ser.validated_data))
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=False, methods=['post'], url_path='create')
    def create_part(self, request):
        ser=PartCreateSerializer(data=request.data); ser.is_valid(raise_exception=True)
        try:
            result=PartCreationService().create(actor=request.user, **ser.validated_data)
            item=result['item']; rev=result['revision']
            return Response({'item': {'id': str(item.pk), 'part_number': item.item_code, 'name': item.name}, 'revision': {'id': str(rev.pk), 'revision': rev.revision} if rev else None, 'idempotent_replay': result['idempotent_replay'], 'coding_snapshot': result['coding_snapshot']}, status=200 if result['idempotent_replay'] else 201)
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=False, methods=['post'])
    def decode(self, request):
        ser=DecodeSerializer(data=request.data); ser.is_valid(raise_exception=True)
        try: return Response(CodeDecodeService().decode(code=ser.validated_data['code'], version=ser.validated_data.get('version'), definition=ser.validated_data.get('code_definition'), structure=ser.validated_data.get('structure')))
        except EngineeringDomainError as exc: return domain_response(exc)
    @action(detail=True, methods=['get'], url_path='engineering-context')
    def engineering_context(self, request, pk=None):
        item=Item.objects.select_related('structure','code_definition','code_definition_version').get(pk=pk)
        return Response({'part': {'id': str(item.pk), 'part_number': item.item_code, 'name': item.name}, 'structure': StructureReadSerializer(item.structure).data if item.structure else None, 'code_definition': CodeDefinitionReadSerializer(item.code_definition).data if item.code_definition else None, 'code_definition_version': CodeVersionSerializer(item.code_definition_version).data if item.code_definition_version else None, 'coding_snapshot': item.coding_snapshot, 'parameter_values': list(item.parameter_values.values('parameter__code','value','normalized_value','display_value')), 'technical_values': list(item.technical_values.values('technical_field__code','value')), 'legacy_coded': bool(item.coding_scheme_id or item.coding_template_id)})
