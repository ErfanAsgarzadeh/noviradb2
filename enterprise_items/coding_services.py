from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from auditlog.services import log_event

from .exceptions import EngineeringLifecycleError, EngineeringPermissionError
from .models import (
    AttributeDefinition,
    AttributeEncodingOption,
    AttributeEncodingRule,
    ClassificationAttribute,
    CodingOrganization,
    Item,
    ItemCodeSequence,
    ItemCodingScheme,
    ItemCodingTemplate,
    ItemCodingTemplateSegment,
    ItemIdentifier,
    ItemRevision,
    ItemRevisionAttributeValue,
    get_effective_classification_attributes,
    normalize_identifier,
)
from .permissions import can_manage_engineering, is_engineering_releaser

SAFE_CODE_RE = re.compile(r'^[A-Za-z0-9._-]+$')
UNIT_FACTORS = {
    ('M', 'MM'): Decimal('1000'),
    ('CM', 'MM'): Decimal('10'),
    ('MM', 'MM'): Decimal('1'),
    ('KW', 'KW'): Decimal('1'),
    ('W', 'KW'): Decimal('0.001'),
    ('EA', 'EA'): Decimal('1'),
}


@dataclass
class NormalizedAttribute:
    definition: AttributeDefinition
    value: Any
    unit: str
    assignment: ClassificationAttribute | None = None


def _require_manager(actor):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError('You do not have permission to manage part master configuration.')


def _require_template_activator(actor):
    if not is_engineering_releaser(actor):
        raise EngineeringPermissionError('You do not have permission to activate coding templates.')


def _allowed_attributes(classification):
    result = {}
    for assignment in get_effective_classification_attributes(classification).order_by('classification__path', 'display_order'):
        result[assignment.attribute_definition.code] = assignment
    return result


def normalize_value(definition: AttributeDefinition, raw: dict | Any, unit: str = '') -> tuple[Any, str]:
    if isinstance(raw, dict):
        unit = raw.get('unit', unit) or ''
        raw = raw.get('value')
    unit = (unit or definition.default_unit or '').strip().upper()
    if definition.data_type == AttributeDefinition.TYPE_TEXT:
        return str(raw or '').strip(), unit
    if definition.data_type == AttributeDefinition.TYPE_DECIMAL:
        try:
            return Decimal(str(raw)), unit
        except (InvalidOperation, TypeError):
            raise ValidationError({definition.code: 'Expected a decimal value.'})
    if definition.data_type == AttributeDefinition.TYPE_INTEGER:
        try:
            return int(raw), unit
        except (TypeError, ValueError):
            raise ValidationError({definition.code: 'Expected an integer value.'})
    if definition.data_type == AttributeDefinition.TYPE_BOOLEAN:
        if isinstance(raw, bool):
            return raw, unit
        if str(raw).strip().lower() in {'true', '1', 'yes', 'y'}:
            return True, unit
        if str(raw).strip().lower() in {'false', '0', 'no', 'n'}:
            return False, unit
        raise ValidationError({definition.code: 'Expected a boolean value.'})
    if definition.data_type == AttributeDefinition.TYPE_DATE:
        if isinstance(raw, date):
            return raw, unit
        try:
            return date.fromisoformat(str(raw)), unit
        except (TypeError, ValueError):
            raise ValidationError({definition.code: 'Expected an ISO date.'})
    if definition.data_type == AttributeDefinition.TYPE_CHOICE:
        normalized = str(raw or '').strip().upper()
        choices = [str(choice).strip().upper() for choice in definition.validation_metadata.get('choices', [])]
        if normalized not in choices:
            raise ValidationError({definition.code: 'Value is not one of the controlled choices.'})
        return normalized, unit
    raise ValidationError({definition.code: 'Unsupported attribute data type.'})


def normalize_attribute_payload(classification, attributes: dict[str, Any], *, require_required=True) -> dict[str, NormalizedAttribute]:
    assignments = _allowed_attributes(classification)
    normalized = {}
    errors = {}
    for code, raw in (attributes or {}).items():
        key = str(code).strip().upper()
        assignment = assignments.get(key)
        if not assignment:
            errors[key] = 'Attribute is not valid for the selected classification.'
            continue
        try:
            value, unit = normalize_value(assignment.attribute_definition, raw)
            normalized[key] = NormalizedAttribute(assignment.attribute_definition, value, unit or assignment.default_unit, assignment)
        except ValidationError as exc:
            errors.update(exc.message_dict if hasattr(exc, 'message_dict') else {key: exc.messages})
    if require_required:
        for code, assignment in assignments.items():
            if assignment.effective_required and code not in normalized:
                errors[code] = 'Required attribute is missing.'
    if errors:
        raise ValidationError(errors)
    return normalized


def canonicalize_for_identity(classification, normalized: dict[str, NormalizedAttribute]) -> dict:
    payload = {'classification': classification.code, 'attributes': {}}
    for code in sorted(normalized):
        attr = normalized[code]
        assignment = attr.assignment
        if assignment and not assignment.effective_identity_defining:
            continue
        value = attr.value
        if isinstance(value, Decimal):
            value = str(value.normalize())
        elif isinstance(value, date):
            value = value.isoformat()
        payload['attributes'][code] = {'value': value, 'unit': attr.unit or ''}
    return payload


def semantic_hash(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _date_applies(template, at: date) -> bool:
    return (template.effective_from is None or template.effective_from <= at) and (template.effective_to is None or at <= template.effective_to)


def resolve_template(classification, scheme=None, at: date | None = None):
    at = at or timezone.localdate()
    qs = ItemCodingTemplate.objects.select_related('coding_scheme', 'classification').filter(
        classification=classification,
        status=ItemCodingTemplate.STATUS_ACTIVE,
        is_active=True,
        coding_scheme__is_active=True,
    )
    if scheme:
        qs = qs.filter(coding_scheme=scheme)
    else:
        qs = qs.order_by('-coding_scheme__is_default', '-version')
    for template in qs.order_by('-version'):
        if _date_applies(template, at):
            return template
    raise EngineeringLifecycleError({'template': 'No active coding template applies to this classification.'})


def _apply_case(value: str, scheme: ItemCodingScheme) -> str:
    if scheme.case_policy == ItemCodingScheme.CASE_UPPER:
        return value.upper()
    if scheme.case_policy == ItemCodingScheme.CASE_LOWER:
        return value.lower()
    return value


def _encode_numeric(rule: AttributeEncodingRule, value: Decimal, unit: str) -> str:
    canonical = (rule.canonical_unit or unit or '').upper()
    factor = UNIT_FACTORS.get(((unit or canonical).upper(), canonical), Decimal('1'))
    scaled = value * factor * rule.multiplier
    quant = Decimal(1).scaleb(-rule.precision)
    if rule.rounding_policy == AttributeEncodingRule.ROUND_REJECT and scaled != scaled.quantize(quant):
        raise ValidationError('Numeric value cannot be represented uniquely by this encoding rule.')
    rounding = ROUND_DOWN if rule.rounding_policy == AttributeEncodingRule.ROUND_DOWN else ROUND_HALF_UP
    rendered = scaled.quantize(quant, rounding=rounding)
    if rule.precision == 0:
        rendered = int(rendered)
    text = str(rendered).replace('.', '')
    if rule.zero_pad_width:
        text = text.zfill(rule.zero_pad_width)
    return f'{rule.prefix}{text}{rule.suffix}'


def encode_attribute(rule: AttributeEncodingRule | None, attr: NormalizedAttribute) -> str:
    value = attr.value
    if not rule:
        return str(value).strip().upper() if not isinstance(value, Decimal) else str(value.normalize()).replace('.', '')
    if rule.encoding_type == AttributeEncodingRule.TYPE_LOOKUP:
        source = str(value).strip().upper()
        option = AttributeEncodingOption.objects.filter(rule=rule, source_value=source, is_active=True).first()
        if not option:
            raise ValidationError(f'No lookup encoding option for {attr.definition.code}={source}.')
        return option.encoded_value
    if rule.encoding_type == AttributeEncodingRule.TYPE_NUMERIC:
        return _encode_numeric(rule, Decimal(value), attr.unit)
    if rule.encoding_type == AttributeEncodingRule.TYPE_NORMALIZED_TEXT:
        text = re.sub(r'[^A-Za-z0-9]+', '_', str(value).strip())[:rule.max_length]
        return text.upper() if rule.text_case == ItemCodingScheme.CASE_UPPER else text.lower() if rule.text_case == ItemCodingScheme.CASE_LOWER else text
    if rule.encoding_type == AttributeEncodingRule.TYPE_BOOLEAN_TOKEN:
        return rule.true_token if bool(value) else rule.false_token
    if rule.encoding_type == AttributeEncodingRule.TYPE_DATE_PATTERN:
        if rule.date_pattern == 'YYYYMMDD':
            return value.strftime('%Y%m%d')
        if rule.date_pattern == 'YYYYMM':
            return value.strftime('%Y%m')
        if rule.date_pattern == 'YYMM':
            return value.strftime('%y%m')
    if rule.encoding_type == AttributeEncodingRule.TYPE_REFERENCE_CODE:
        return str(value).strip().upper()
    raise ValidationError('Unsupported encoding rule.')


def _classification_segment(classification, level):
    parts = classification.path.split('/') if classification.path else [classification.code]
    if level is None:
        return classification.coding_prefix or classification.code
    if level >= len(parts):
        raise ValidationError('Classification level is outside the classification path.')
    return parts[level]


def _sequence_scope_key(template: ItemCodingTemplate):
    scheme = template.coding_scheme
    if scheme.sequence_scope == ItemCodingScheme.SCOPE_GLOBAL:
        return 'GLOBAL'
    if scheme.sequence_scope == ItemCodingScheme.SCOPE_COMPANY:
        return str(scheme.organization_id)
    if scheme.sequence_scope == ItemCodingScheme.SCOPE_DOMAIN:
        return template.classification.path.split('/')[0] if template.classification.path else template.classification.code
    return str(template.classification_id)


def reserve_sequence(template: ItemCodingTemplate) -> int:
    scope_key = _sequence_scope_key(template)
    seq, _ = ItemCodeSequence.objects.select_for_update().get_or_create(coding_scheme=template.coding_scheme, scope_key=scope_key)
    value = seq.next_sequence
    seq.next_sequence += 1
    seq.save(update_fields=['next_sequence', 'updated_at'])
    return value


def build_code(template, normalized, *, consume_sequence=False, provisional_sequence=None):
    scheme = template.coding_scheme
    segments = []
    breakdown = []
    for segment in template.segments.select_related('attribute_definition', 'encoding_rule').order_by('position'):
        if segment.segment_type == ItemCodingTemplateSegment.TYPE_CHECK_DIGIT:
            raise ValidationError({'segments': 'CHECK_DIGIT segments are reserved but not supported.'})
        if segment.segment_type == ItemCodingTemplateSegment.TYPE_LITERAL:
            value = segment.literal_value
            label = 'Literal'
        elif segment.segment_type == ItemCodingTemplateSegment.TYPE_CLASSIFICATION_CODE:
            value = _classification_segment(template.classification, segment.classification_level)
            label = 'Classification'
        elif segment.segment_type == ItemCodingTemplateSegment.TYPE_ATTRIBUTE:
            attr = normalized.get(segment.attribute_definition.code)
            if not attr:
                if segment.required:
                    raise ValidationError({segment.attribute_definition.code: 'Required code-bearing attribute is missing.'})
                value = segment.fallback_value
            else:
                value = encode_attribute(segment.encoding_rule, attr)
            label = segment.attribute_definition.name
        elif segment.segment_type == ItemCodingTemplateSegment.TYPE_SEQUENCE:
            raw = reserve_sequence(template) if consume_sequence else (provisional_sequence or 1)
            value = str(raw).zfill(segment.width or scheme.sequence_length)
            label = 'Sequence'
        else:
            raise ValidationError({'segments': 'Unsupported segment type.'})
        if value in (None, '') and segment.required:
            raise ValidationError({'segments': f'Segment {segment.position} did not produce a value.'})
        value = f'{segment.prefix}{value}{segment.suffix}'
        value = _apply_case(str(value), scheme)
        segments.append(value)
        breakdown.append({'position': segment.position, 'type': segment.segment_type, 'label': label, 'value': value})
    code = scheme.separator.join([segment for segment in segments if segment])
    code = _apply_case(code, scheme)
    if len(code) > scheme.maximum_length:
        raise ValidationError({'item_code': 'Generated part number exceeds the coding scheme maximum length.'})
    if not SAFE_CODE_RE.match(code):
        raise ValidationError({'item_code': 'Generated part number contains unsafe characters.'})
    return code, breakdown


def validate_template(template: ItemCodingTemplate):
    errors = {}
    segments = list(template.segments.select_related('attribute_definition', 'encoding_rule'))
    if not segments:
        errors['segments'] = 'Template requires at least one segment.'
    positions = [s.position for s in segments]
    if len(positions) != len(set(positions)):
        errors['position'] = 'Segment positions must be unique.'
    for segment in segments:
        segment_errors = {}
        if segment.segment_type == ItemCodingTemplateSegment.TYPE_LITERAL and not segment.literal_value.strip():
            segment_errors['literal_value'] = 'Literal segments require a literal value.'
        if segment.segment_type == ItemCodingTemplateSegment.TYPE_ATTRIBUTE:
            if not segment.attribute_definition_id:
                segment_errors['attribute_definition'] = 'Attribute segments require an attribute definition.'
            else:
                allowed = get_effective_classification_attributes(template.classification).filter(attribute_definition=segment.attribute_definition, is_active=True).exists()
                if not allowed:
                    segment_errors['attribute_definition'] = 'Attribute is not effective for this template classification.'
        if segment.segment_type == ItemCodingTemplateSegment.TYPE_SEQUENCE and (not segment.width or segment.width < 1 or segment.width > 12):
            segment_errors['width'] = 'Sequence segments require a width between 1 and 12.'
        if segment.segment_type == ItemCodingTemplateSegment.TYPE_CHECK_DIGIT:
            segment_errors['segment_type'] = 'CHECK_DIGIT is reserved but not supported yet.'
        if segment_errors:
            errors[str(segment.position)] = segment_errors
    if errors:
        raise EngineeringLifecycleError(errors)
    return {'valid': True, 'warnings': []}


def activate_template(template, *, actor):
    _require_template_activator(actor)
    with transaction.atomic():
        template = ItemCodingTemplate.objects.select_for_update().select_related('coding_scheme', 'classification').get(pk=template.pk)
        validate_template(template)
        today = template.effective_from or timezone.localdate()
        overlaps = ItemCodingTemplate.objects.select_for_update().filter(
            coding_scheme=template.coding_scheme,
            classification=template.classification,
            status=ItemCodingTemplate.STATUS_ACTIVE,
            is_active=True,
        ).exclude(pk=template.pk)
        for existing in overlaps:
            if _periods_overlap(today, template.effective_to, existing.effective_from or date.min, existing.effective_to):
                raise EngineeringLifecycleError({'effective_from': 'An active effective template already applies to this classification.'})
        template.status = ItemCodingTemplate.STATUS_ACTIVE
        template.effective_from = today
        template.activated_by = actor
        template.activated_at = timezone.now()
        template.save(update_fields=['status', 'effective_from', 'activated_by', 'activated_at', 'updated_at'])
        log_event('item_coding_template_activated', target=template, category='business', extra={'template_id': str(template.pk)})
        return template


def retire_template(template, *, actor):
    _require_template_activator(actor)
    template.status = ItemCodingTemplate.STATUS_RETIRED
    template.retired_by = actor
    template.retired_at = timezone.now()
    template.is_active = False
    template.save(update_fields=['status', 'retired_by', 'retired_at', 'is_active', 'updated_at'])
    log_event('item_coding_template_retired', target=template, category='business', extra={'template_id': str(template.pk)})
    return template


def clone_template(template, *, actor, version=None):
    _require_manager(actor)
    with transaction.atomic():
        source = ItemCodingTemplate.objects.prefetch_related('segments').get(pk=template.pk)
        next_version = version or ((ItemCodingTemplate.objects.filter(coding_scheme=source.coding_scheme, classification=source.classification).order_by('-version').first().version or 0) + 1)
        clone = ItemCodingTemplate.objects.create(
            coding_scheme=source.coding_scheme,
            classification=source.classification,
            version=next_version,
            status=ItemCodingTemplate.STATUS_DRAFT,
            fallback_sequence_enabled=source.fallback_sequence_enabled,
            description=source.description,
            created_by=actor,
        )
        for segment in source.segments.all():
            ItemCodingTemplateSegment.objects.create(
                template=clone, position=segment.position, segment_type=segment.segment_type, literal_value=segment.literal_value,
                classification_level=segment.classification_level, attribute_definition=segment.attribute_definition,
                encoding_rule=segment.encoding_rule, width=segment.width, required=segment.required,
                fallback_value=segment.fallback_value, prefix=segment.prefix, suffix=segment.suffix,
            )
        log_event('item_coding_template_cloned', target=clone, category='business', extra={'source_template_id': str(source.pk)})
        return clone


def _periods_overlap(a_start, a_end, b_start, b_end):
    return a_start <= (b_end or date.max) and b_start <= (a_end or date.max)


def duplicate_check(*, organization, candidate_code='', semantic_payload=None, semantic_identity_hash='', identifiers=None, name=''):
    matches = []
    if candidate_code:
        qs = Item.objects.filter(organization=organization, item_code__iexact=candidate_code)
        for item in qs[:5]:
            matches.append({'level': 'BLOCKING_DUPLICATE', 'kind': 'ITEM_CODE', 'item': str(item.pk), 'item_code': item.item_code})
    if semantic_identity_hash:
        for item in Item.objects.filter(organization=organization, semantic_identity_hash=semantic_identity_hash)[:10]:
            if item.semantic_identity_payload == semantic_payload:
                matches.append({'level': 'BLOCKING_DUPLICATE', 'kind': 'SEMANTIC_IDENTITY', 'item': str(item.pk), 'item_code': item.item_code})
            else:
                matches.append({'level': 'POSSIBLE_DUPLICATE', 'kind': 'SEMANTIC_HASH_COLLISION', 'item': str(item.pk), 'item_code': item.item_code})
    for identifier in identifiers or []:
        id_type = identifier.get('identifier_type')
        value = normalize_identifier(identifier.get('value'), id_type)
        org_name = identifier.get('organization_name', '') or ''
        if not value:
            continue
        qs = ItemIdentifier.objects.filter(identifier_type=id_type, normalized_value=value)
        if id_type in {ItemIdentifier.TYPE_MANUFACTURER_PART_NUMBER, ItemIdentifier.TYPE_SUPPLIER_PART_NUMBER}:
            qs = qs.filter(organization_name=org_name)
        for found in qs.select_related('item')[:5]:
            level = 'BLOCKING_DUPLICATE' if id_type in {ItemIdentifier.TYPE_MANUFACTURER_PART_NUMBER, ItemIdentifier.TYPE_DRAWING_NUMBER, ItemIdentifier.TYPE_BARCODE} else 'POSSIBLE_DUPLICATE'
            matches.append({'level': level, 'kind': id_type, 'item': str(found.item_id), 'item_code': found.item.item_code})
    normalized_name = (name or '').strip().lower()
    if normalized_name:
        for item in Item.objects.filter(organization=organization)[:200]:
            if item.name and item.name.strip().lower() != normalized_name and SequenceMatcher(None, normalized_name, item.name.strip().lower()).ratio() >= 0.75:
                matches.append({'level': 'POSSIBLE_DUPLICATE', 'kind': 'SIMILAR_NAME', 'item': str(item.pk), 'item_code': item.item_code})
    return matches


def preview_code(*, organization, classification, attributes, coding_scheme=None, manual_code='', identifiers=None, name=''):
    if coding_scheme and coding_scheme.organization_id != organization.pk:
        raise ValidationError({'coding_scheme': 'Coding scheme belongs to another organization.'})
    template = None
    normalized = normalize_attribute_payload(classification, attributes, require_required=True)
    if coding_scheme and coding_scheme.strategy in {ItemCodingScheme.STRATEGY_MANUAL_CONTROLLED, ItemCodingScheme.STRATEGY_LEGACY}:
        candidate = _apply_case((manual_code or '').strip(), coding_scheme)
        if not candidate:
            raise ValidationError({'manual_code': 'Manual part number is required for this strategy.'})
        breakdown = [{'position': 1, 'type': 'MANUAL', 'label': coding_scheme.strategy, 'value': candidate}]
    else:
        template = resolve_template(classification, coding_scheme)
        validate_template(template)
        candidate, breakdown = build_code(template, normalized, consume_sequence=False)
        coding_scheme = template.coding_scheme
    payload = canonicalize_for_identity(classification, normalized)
    hash_value = semantic_hash(payload)
    duplicates = duplicate_check(organization=organization, candidate_code=candidate, semantic_payload=payload, semantic_identity_hash=hash_value, identifiers=identifiers, name=name)
    return {
        'candidate_code': candidate,
        'is_provisional': any(row['type'] == ItemCodingTemplateSegment.TYPE_SEQUENCE for row in breakdown),
        'template': str(template.pk) if template else None,
        'template_version': template.version if template else None,
        'coding_scheme': str(coding_scheme.pk) if coding_scheme else None,
        'strategy': coding_scheme.strategy if coding_scheme else '',
        'segment_breakdown': breakdown,
        'validation_errors': {},
        'warnings': ['Sequence value is provisional until final creation.'] if any(row['type'] == ItemCodingTemplateSegment.TYPE_SEQUENCE for row in breakdown) else [],
        'semantic_identity_payload': payload,
        'semantic_identity_hash': hash_value,
        'duplicates': duplicates,
    }


def create_item_from_code(*, actor, organization: CodingOrganization, item_type, classification, name, base_unit='EA', make_or_buy='MAKE', attributes=None, coding_scheme=None, manual_code='', identifiers=None, duplicate_override=False):
    _require_manager(actor)
    if coding_scheme and coding_scheme.strategy in {ItemCodingScheme.STRATEGY_MANUAL_CONTROLLED, ItemCodingScheme.STRATEGY_LEGACY} and not is_engineering_releaser(actor):
        raise EngineeringPermissionError('Manual controlled or legacy part number entry requires release-level engineering permission.')
    with transaction.atomic():
        normalized = normalize_attribute_payload(classification, attributes or {}, require_required=True)
        template = None
        if coding_scheme and coding_scheme.strategy in {ItemCodingScheme.STRATEGY_MANUAL_CONTROLLED, ItemCodingScheme.STRATEGY_LEGACY}:
            candidate = _apply_case((manual_code or '').strip(), coding_scheme)
            if not candidate:
                raise ValidationError({'manual_code': 'Manual part number is required for this strategy.'})
            breakdown = [{'position': 1, 'type': 'MANUAL', 'label': coding_scheme.strategy, 'value': candidate}]
        else:
            template = resolve_template(classification, coding_scheme)
            validate_template(template)
            candidate, breakdown = build_code(template, normalized, consume_sequence=True)
            coding_scheme = template.coding_scheme
        payload = canonicalize_for_identity(classification, normalized)
        hash_value = semantic_hash(payload)
        duplicates = duplicate_check(organization=organization, candidate_code=candidate, semantic_payload=payload, semantic_identity_hash=hash_value, identifiers=identifiers, name=name)
        blocking = [dup for dup in duplicates if dup['level'] == 'BLOCKING_DUPLICATE']
        if blocking and not duplicate_override:
            raise EngineeringLifecycleError({'duplicates': blocking})
        try:
            item = Item.objects.create(
                organization=organization, item_code=candidate, name=name, item_type=item_type, classification=classification,
                base_unit=base_unit, make_or_buy=make_or_buy, status='ACTIVE', is_active=True,
                semantic_identity_payload=payload, semantic_identity_hash=hash_value, coding_scheme=coding_scheme,
                coding_template=template, code_generation_strategy=coding_scheme.strategy if coding_scheme else '', generated_code_locked=True,
                created_by=actor,
            )
        except IntegrityError as exc:
            raise EngineeringLifecycleError({'item_code': 'Part number was already allocated.'}) from exc
        revision = ItemRevision.objects.create(item=item, revision=item.default_revision or 'R0', title=name, created_at=timezone.now())
        for code, attr in normalized.items():
            value_kwargs = {'item_revision': revision, 'attribute_definition': attr.definition, 'unit': attr.unit, 'created_by': actor}
            if attr.definition.data_type == AttributeDefinition.TYPE_TEXT:
                value_kwargs['value_text'] = attr.value
            elif attr.definition.data_type == AttributeDefinition.TYPE_DECIMAL:
                value_kwargs['value_decimal'] = attr.value
            elif attr.definition.data_type == AttributeDefinition.TYPE_INTEGER:
                value_kwargs['value_integer'] = attr.value
            elif attr.definition.data_type == AttributeDefinition.TYPE_BOOLEAN:
                value_kwargs['value_boolean'] = attr.value
            elif attr.definition.data_type == AttributeDefinition.TYPE_DATE:
                value_kwargs['value_date'] = attr.value
            elif attr.definition.data_type == AttributeDefinition.TYPE_CHOICE:
                value_kwargs['value_choice'] = attr.value
            ItemRevisionAttributeValue.objects.create(**value_kwargs)
        for identifier in identifiers or []:
            ItemIdentifier.objects.create(item=item, created_by=actor, **identifier)
        log_event('item_code_generated', target=item, category='business', extra={'item_code': item.item_code, 'strategy': item.code_generation_strategy, 'template_id': str(template.pk) if template else None, 'duplicate_override': duplicate_override})
        if manual_code:
            log_event('item_manual_code_used', target=item, category='business', extra={'item_code': item.item_code, 'strategy': item.code_generation_strategy})
        return {'item': item, 'revision': revision, 'segment_breakdown': breakdown, 'duplicates': duplicates}


def governance_decision_for_attribute_change(attribute_definition: AttributeDefinition):
    if attribute_definition.is_code_bearing and attribute_definition.is_identity_defining:
        return 'CREATE_NEW_ITEM'
    if attribute_definition.is_revision_controlled:
        return 'CREATE_NEW_REVISION'
    if attribute_definition.is_identity_defining:
        return 'CREATE_NEW_ITEM'
    return 'NOT_ALLOWED'
