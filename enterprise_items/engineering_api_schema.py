from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import CanManageEngineering


ENGINEERING_API_CONTRACT = {
    'schema_version': 'phase1f-b',
    'base_path': '/api/engineering/',
    'permissions': {
        'read': 'authenticated engineering viewer',
        'write': 'CanManageEngineering',
        'activation': 'CanManageEngineering service transition checks',
    },
    'idempotency': 'Part creation accepts idempotency_key and replays the existing Item without allocating another sequence value.',
    'error_shape': {'error': {'code': 'stable_domain_code', 'message': 'human-readable message', 'fields': {}, 'metadata': {}}},
    'endpoints': [
        {'method': 'GET', 'path': 'parameters/', 'response': 'ParameterReadSerializer'},
        {'method': 'POST', 'path': 'parameters/', 'request': 'ParameterCreateSerializer', 'response': 'ParameterReadSerializer', 'service': 'ParameterDefinitionService.create'},
        {'method': 'PATCH', 'path': 'parameters/{id}/', 'request': 'ParameterUpdateSerializer', 'service': 'ParameterDefinitionService.update'},
        {'method': 'POST', 'path': 'parameters/bulk/', 'request': 'BulkParameterSerializer', 'service': 'ParameterDefinitionService.bulk_create'},
        {'method': 'POST', 'path': 'parameters/{id}/options/bulk/', 'request': 'ParameterOptionSerializer[]', 'service': 'ParameterDefinitionService.bulk_options'},
        {'method': 'GET', 'path': 'parameter-metadata-fields/', 'response': 'MetadataFieldSerializer'},
        {'method': 'POST', 'path': 'parameter-metadata-fields/', 'request': 'MetadataFieldSerializer', 'service': 'ParameterMetadataService.create_field'},
        {'method': 'GET', 'path': 'structures/', 'response': 'StructureReadSerializer'},
        {'method': 'POST', 'path': 'structures/', 'request': 'StructureCreateSerializer', 'service': 'StructureDefinitionService.create'},
        {'method': 'PUT', 'path': 'structures/{id}/parameters/', 'request': 'StructureParameterSerializer[]', 'service': 'StructureDefinitionService.set_parameters'},
        {'method': 'POST', 'path': 'structures/{id}/validate/', 'response': 'StructureValidationService.validate'},
        {'method': 'GET', 'path': 'code-definitions/', 'response': 'CodeDefinitionReadSerializer'},
        {'method': 'POST', 'path': 'code-definitions/', 'request': 'CodeDefinitionCreateSerializer', 'service': 'CodeDefinitionService.create'},
        {'method': 'POST', 'path': 'code-definitions/{id}/versions/', 'service': 'CodeDefinitionVersionService.create_initial_draft'},
        {'method': 'POST', 'path': 'code-definitions/{id}/versions/{version_id}/', 'request': {'action': 'clone|activate|validate'}, 'service': 'CodeDefinitionVersionService|CodeDefinitionValidationService'},
        {'method': 'PUT', 'path': 'code-definitions/{id}/versions/{version_id}/segments/', 'request': 'SegmentListUpdateSerializer', 'service': 'CodeSegmentService.set_segments'},
        {'method': 'POST', 'path': 'code-definitions/{id}/simulate/', 'request': 'PreviewSerializer', 'service': 'CodePreviewService.preview'},
        {'method': 'POST', 'path': 'code-definitions/{id}/decode/', 'request': 'DecodeSerializer', 'service': 'CodeDecodeService.decode'},
        {'method': 'GET', 'path': 'technical-templates/', 'response': 'TechnicalTemplateSerializer'},
        {'method': 'POST', 'path': 'technical-templates/', 'request': 'TechnicalTemplateSerializer', 'service': 'TechnicalDataTemplateService.create'},
        {'method': 'PUT', 'path': 'technical-templates/{id}/fields/', 'request': 'TechnicalFieldSerializer[]', 'service': 'TechnicalDataTemplateService.set_fields'},
        {'method': 'POST', 'path': 'technical-templates/{id}/validate/', 'service': 'TechnicalDataValidationService.validate'},
        {'method': 'POST', 'path': 'parts/preview/', 'request': 'PreviewSerializer', 'service': 'PartCreationService.preview'},
        {'method': 'POST', 'path': 'parts/create/', 'request': 'PartCreateSerializer', 'service': 'PartCreationService.create'},
        {'method': 'POST', 'path': 'parts/decode/', 'request': 'DecodeSerializer', 'service': 'CodeDecodeService.decode'},
        {'method': 'GET', 'path': 'parts/{id}/engineering-context/', 'service': 'EngineeringPartViewSet.engineering_context'},
    ],
    'examples': {
        'preview': {'generated_code': 'BLT-CS-050-001', 'preview_only': True, 'warnings': ['Preview sequence is not committed; final code may differ if another Part is created first.']},
        'duplicate': {'error': {'code': 'duplicate_part', 'message': 'A Part with the same semantic identity or Part Number already exists.', 'metadata': {'existing_part_number': 'BLT-CS-050-001'}}},
        'decode': {'confidence': 100, 'segments': [{'segment_type': 'PARAMETER', 'token': 'CS'}]},
    },
}


class EngineeringSchemaView(APIView):
    permission_classes = [CanManageEngineering]

    def get(self, request):
        return Response(ENGINEERING_API_CONTRACT)