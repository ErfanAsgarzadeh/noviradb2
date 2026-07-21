from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .coding_services import preview_code, validate_template, build_code, normalize_attribute_payload, canonicalize_for_identity, semantic_hash, duplicate_check
from .exceptions import EngineeringLifecycleError, EngineeringPermissionError
from .models import (
    AttributeEncodingOption,
    AttributeEncodingRule,
    CodingOrganization,
    Item,
    ItemClassification,
    ItemCodingScheme,
    ItemCodingTemplate,
    ItemCodingTemplateSegment,
    ItemType,
)

PARAMETER_LABELS = {
    'LITERAL': 'Fixed text',
    'CLASSIFICATION_CODE': 'Fixed text',
    'ATTRIBUTE': 'Coding characteristic',
    'SEQUENCE': 'Automatic number',
    'CHECK_DIGIT': 'Check digit',
}

STATUS_MAP = {
    ItemCodingTemplate.STATUS_DRAFT: 'DRAFT',
    ItemCodingTemplate.STATUS_ACTIVE: 'ACTIVE',
    ItemCodingTemplate.STATUS_RETIRED: 'INACTIVE',
}


def _template_qs():
    return ItemCodingTemplate.objects.select_related('coding_scheme', 'classification', 'created_by').prefetch_related(
        'segments', 'segments__attribute_definition', 'segments__encoding_rule', 'segments__encoding_rule__options'
    )


def configuration_mode_for_template(template: ItemCodingTemplate) -> str:
    unsupported = template.segments.filter(segment_type=ItemCodingTemplateSegment.TYPE_CHECK_DIGIT).exists()
    return 'ADVANCED' if unsupported else 'GUIDED'


def profile_from_template(template: ItemCodingTemplate) -> dict[str, Any]:
    scheme = template.coding_scheme
    sequence_segment = template.segments.filter(segment_type=ItemCodingTemplateSegment.TYPE_SEQUENCE).order_by('position').first()
    prefix = ''
    first = template.segments.order_by('position').first()
    if first:
        if first.segment_type == ItemCodingTemplateSegment.TYPE_LITERAL:
            prefix = first.literal_value
        elif first.segment_type == ItemCodingTemplateSegment.TYPE_CLASSIFICATION_CODE:
            prefix = template.classification.coding_prefix or template.classification.code
    return {
        'id': str(template.pk),
        'name': f'{template.classification.name} / {scheme.name}',
        'code': scheme.code,
        'description': template.description or scheme.description,
        'classification': str(template.classification_id),
        'classification_path': template.classification.path,
        'include_descendants': False,
        'strategy': scheme.strategy,
        'configuration_mode': configuration_mode_for_template(template),
        'family_prefix': prefix,
        'separator': scheme.separator,
        'sequence_enabled': bool(sequence_segment),
        'sequence_length': sequence_segment.width if sequence_segment else scheme.sequence_length,
        'sequence_scope': scheme.sequence_scope,
        'maximum_code_length': scheme.maximum_length,
        'duplicate_policy': 'BLOCKING_SEMANTIC_AND_CODE',
        'description_pattern': '{name}',
        'status': STATUS_MAP.get(template.status, template.status),
        'active_version': template.version,
        'template': str(template.pk),
        'coding_scheme': str(scheme.pk),
        'created_at': template.created_at,
        'created_by': getattr(template.created_by, 'username', '') if template.created_by_id else '',
        'updated_at': template.updated_at,
        'parameters': [parameter_from_segment(segment) for segment in template.segments.all().order_by('position')],
        'health': health_for_template(template),
    }


def parameter_from_segment(segment: ItemCodingTemplateSegment) -> dict[str, Any]:
    attr = segment.attribute_definition
    rule = segment.encoding_rule
    options = []
    if rule and rule.encoding_type == AttributeEncodingRule.TYPE_LOOKUP:
        options = [
            {
                'id': str(option.pk),
                'display_label': option.source_value,
                'stored_value': option.source_value,
                'encoded_token': option.encoded_value,
                'active': option.is_active,
            }
            for option in rule.options.all().order_by('sort_order', 'source_value')
        ]
    if segment.segment_type == ItemCodingTemplateSegment.TYPE_SEQUENCE:
        parameter_type = 'AUTOMATIC_SEQUENCE'
    elif segment.segment_type in {ItemCodingTemplateSegment.TYPE_LITERAL, ItemCodingTemplateSegment.TYPE_CLASSIFICATION_CODE}:
        parameter_type = 'FIXED_TEXT'
    elif attr and attr.data_type == 'INTEGER':
        parameter_type = 'INTEGER'
    elif attr and attr.data_type == 'DECIMAL':
        parameter_type = 'DECIMAL'
    elif attr and attr.data_type == 'BOOLEAN':
        parameter_type = 'BOOLEAN'
    elif attr and attr.data_type == 'DATE':
        parameter_type = 'DATE'
    elif rule and rule.encoding_type == AttributeEncodingRule.TYPE_LOOKUP:
        parameter_type = 'OPTION_LIST'
    else:
        parameter_type = 'TEXT'
    return {
        'id': str(segment.pk),
        'key': attr.code if attr else f'SEGMENT_{segment.position}',
        'business_label': attr.name if attr else PARAMETER_LABELS.get(segment.segment_type, segment.segment_type),
        'technical_name': attr.code if attr else segment.segment_type,
        'description': getattr(attr, 'description', '') if attr else '',
        'help_text': 'Select a controlled value.' if options else '',
        'order': segment.position,
        'parameter_type': parameter_type,
        'source_attribute': str(attr.pk) if attr else None,
        'code_bearing': segment.segment_type in {ItemCodingTemplateSegment.TYPE_ATTRIBUTE, ItemCodingTemplateSegment.TYPE_SEQUENCE, ItemCodingTemplateSegment.TYPE_LITERAL, ItemCodingTemplateSegment.TYPE_CLASSIFICATION_CODE},
        'requiredness': 'REQUIRED' if segment.required else 'OPTIONAL',
        'default_value': segment.fallback_value,
        'unit': getattr(attr, 'default_unit', '') if attr else '',
        'encoding_rule': str(rule.pk) if rule else None,
        'encoding_rule_code': rule.code if rule else '',
        'maximum_token_width': segment.width,
        'active': True,
        'options': options,
        'fixed_value': segment.literal_value if segment.segment_type == ItemCodingTemplateSegment.TYPE_LITERAL else '',
        'preview_token': segment.literal_value or segment.fallback_value or '',
    }


def health_for_template(template: ItemCodingTemplate) -> dict[str, Any]:
    try:
        result = validate_template(template)
        return {'status': 'READY', 'errors': {}, 'warnings': result.get('warnings', [])}
    except EngineeringLifecycleError as exc:
        return {'status': 'BLOCKED', 'errors': exc.args[0] if exc.args else {}, 'warnings': []}


def list_profiles() -> list[dict[str, Any]]:
    return [profile_from_template(template) for template in _template_qs().all().order_by('classification__path', '-version')]


def get_profile(profile_id: str) -> dict[str, Any]:
    return profile_from_template(_template_qs().get(pk=profile_id))


def resolve_profile(*, classification: ItemClassification) -> dict[str, Any]:
    lineage = []
    node = classification
    while node:
        lineage.append(node)
        node = node.parent
    for index, candidate in enumerate(lineage):
        template = _template_qs().filter(classification=candidate, status=ItemCodingTemplate.STATUS_ACTIVE, is_active=True, coding_scheme__is_active=True).order_by('-version').first()
        if template:
            profile = profile_from_template(template)
            return {
                'selected_profile': profile,
                'selected_version': template.version,
                'source_classification': str(candidate.pk),
                'direct': index == 0,
                'resolution_type': 'EXACT' if index == 0 else 'ANCESTOR',
                'strategy': template.coding_scheme.strategy,
                'warnings': [] if index == 0 else ['Profile inherited from nearest active ancestor.'],
            }
    raise EngineeringLifecycleError({'profile': 'No active Coding Profile applies to this classification.'})


@transaction.atomic
def create_profile(*, actor, organization: CodingOrganization, classification: ItemClassification, code: str, name: str, strategy='HYBRID', family_prefix='', separator='-', sequence_enabled=True, sequence_length=5, maximum_code_length=80, description='') -> dict[str, Any]:
    scheme = ItemCodingScheme.objects.create(
        organization=organization,
        code=code,
        name=name,
        strategy=strategy,
        separator=separator or '-',
        maximum_length=maximum_code_length,
        sequence_scope=ItemCodingScheme.SCOPE_CLASSIFICATION,
        sequence_length=sequence_length,
        is_default=False,
        is_active=True,
        description=description,
        created_by=actor,
    )
    template = ItemCodingTemplate.objects.create(
        coding_scheme=scheme,
        classification=classification,
        version=1,
        status=ItemCodingTemplate.STATUS_DRAFT,
        description=description,
        created_by=actor,
    )
    position = 10
    if family_prefix:
        ItemCodingTemplateSegment.objects.create(template=template, position=position, segment_type=ItemCodingTemplateSegment.TYPE_LITERAL, literal_value=family_prefix)
        position += 10
    if sequence_enabled:
        ItemCodingTemplateSegment.objects.create(template=template, position=position, segment_type=ItemCodingTemplateSegment.TYPE_SEQUENCE, width=sequence_length)
    return profile_from_template(_template_qs().get(pk=template.pk))


def preview_with_profile(*, organization, classification, attributes, profile=None, manual_code='', identifiers=None, name='') -> dict[str, Any]:
    template = _template_qs().get(pk=profile) if profile else _template_qs().get(pk=resolve_profile(classification=classification)['selected_profile']['id'])
    data = preview_code(organization=organization, classification=classification, attributes=attributes or {}, coding_scheme=template.coding_scheme, manual_code=manual_code, identifiers=identifiers, name=name)
    data['resolved_profile'] = profile_from_template(template)
    data['profile_version'] = template.version
    data['preview_sequence_status'] = 'SIMULATED' if data.get('is_provisional') else 'NOT_USED'
    data['decoded_values'] = decode_code(part_number=data['candidate_code'], profile=template.pk, classification=classification).get('decoded_values', [])
    data['human_readable_description'] = name or data['candidate_code']
    data['identity_summary'] = data.get('semantic_identity_payload', {})
    data['blocking_errors'] = []
    return data


def decode_code(*, part_number: str, profile=None, classification=None) -> dict[str, Any]:
    candidates = _template_qs().all()
    if profile:
        candidates = candidates.filter(pk=profile)
    if classification:
        candidates = candidates.filter(classification=classification)
    matches = []
    for template in candidates:
        decoded = _decode_against_template(part_number, template)
        if decoded['status'] in {'DECODED', 'PARTIALLY_DECODED'}:
            matches.append(decoded)
    if not matches:
        return {'status': 'NOT_RECOGNIZED', 'warnings': ['No Coding Profile could decode this Part Number.'], 'unknown_segments': [part_number], 'matches': []}
    if len(matches) > 1 and not profile:
        return {'status': 'AMBIGUOUS', 'warnings': ['Multiple Coding Profiles can decode this Part Number. Select a profile.'], 'matches': matches}
    result = matches[0]
    item = Item.objects.filter(item_code__iexact=part_number).select_related('classification').first()
    result['matched_part'] = {'id': str(item.pk), 'item_code': item.item_code, 'name': item.name} if item else None
    result['registered_technical_data'] = []
    return result


def _decode_against_template(part_number: str, template: ItemCodingTemplate) -> dict[str, Any]:
    scheme = template.coding_scheme
    tokens = part_number.split(scheme.separator) if scheme.separator else [part_number]
    segments = list(template.segments.all().order_by('position'))
    if len(tokens) != len(segments):
        return {'status': 'NOT_RECOGNIZED', 'profile': profile_from_template(template), 'warnings': ['Segment count does not match.'], 'unknown_segments': tokens}
    breakdown = []
    decoded_values = []
    warnings = []
    partial = False
    for token, segment in zip(tokens, segments):
        row = {'position': segment.position, 'type': segment.segment_type, 'token': token, 'label': '', 'decoded': None, 'message': ''}
        if segment.segment_type == ItemCodingTemplateSegment.TYPE_LITERAL:
            row['label'] = 'Fixed text'
            if token.upper() != segment.literal_value.upper():
                row['message'] = f'{token} does not match fixed token {segment.literal_value}.'
                partial = True
            else:
                row['decoded'] = segment.literal_value
        elif segment.segment_type == ItemCodingTemplateSegment.TYPE_CLASSIFICATION_CODE:
            from .coding_services import _classification_segment
            expected = _classification_segment(template.classification, segment.classification_level)
            row['label'] = 'Classification'
            if token.upper() != str(expected).upper():
                row['message'] = f'{token} does not match classification token {expected}.'
                partial = True
            else:
                row['decoded'] = expected
        elif segment.segment_type == ItemCodingTemplateSegment.TYPE_SEQUENCE:
            row['label'] = 'Automatic number'
            row['decoded'] = {'sequence': token}
        elif segment.segment_type == ItemCodingTemplateSegment.TYPE_ATTRIBUTE:
            attr = segment.attribute_definition
            row['label'] = attr.name if attr else 'Attribute'
            decoded = _decode_attribute_token(token, segment)
            row.update(decoded)
            if decoded.get('message'):
                partial = True
            if decoded.get('decoded') is not None:
                decoded_values.append({'key': attr.code if attr else '', 'label': attr.name if attr else '', 'value': decoded['decoded'], 'token': token})
        breakdown.append(row)
    return {
        'status': 'PARTIALLY_DECODED' if partial else 'DECODED',
        'matched_profile': profile_from_template(template),
        'profile_version': template.version,
        'segment_breakdown': breakdown,
        'decoded_values': decoded_values,
        'human_readable_explanation': 'Decoded using active Coding Profile.' if not partial else 'Some segments could not be decoded.',
        'warnings': warnings,
        'unknown_segments': [row['token'] for row in breakdown if row.get('message')],
    }


def _decode_attribute_token(token: str, segment: ItemCodingTemplateSegment) -> dict[str, Any]:
    rule = segment.encoding_rule
    if not rule:
        return {'decoded': token, 'message': ''}
    if rule.encoding_type == AttributeEncodingRule.TYPE_LOOKUP:
        options = AttributeEncodingOption.objects.filter(rule=rule, encoded_value__iexact=token, is_active=True)
        count = options.count()
        if count == 1:
            option = options.first()
            return {'decoded': {'display_label': option.source_value, 'stored_value': option.source_value, 'encoded_token': option.encoded_value}, 'message': ''}
        if count > 1:
            return {'decoded': None, 'message': f'{token} is ambiguous for {segment.attribute_definition.name}.'}
        return {'decoded': None, 'message': f'{token} is not a recognized value for {segment.attribute_definition.name}.'}
    if rule.encoding_type == AttributeEncodingRule.TYPE_BOOLEAN_TOKEN:
        if token == rule.true_token:
            return {'decoded': True, 'message': ''}
        if token == rule.false_token:
            return {'decoded': False, 'message': ''}
        return {'decoded': None, 'message': f'{token} is not a recognized Boolean token.'}
    return {'decoded': token, 'message': ''}
