import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from auditlog.services import log_event
from .engineering_domain_errors import *
from .models import (
    CodeDefinition, CodeDefinitionVersion, CodeOptionEncoding, CodeSegment, Item, ItemRevision,
    ParameterDefinition, ParameterMetadataField, ParameterMetadataValue, ParameterOption,
    PartParameterValue, PartTechnicalValue, RevisionTechnicalValue, SequenceDefinition,
    StructureDefinition, StructureParameter, TechnicalDataTemplate, TechnicalFieldDefinition,
)


def _raise_model_errors(exc):
    if hasattr(exc, 'message_dict'):
        raise EngineeringValidationError('Validation failed.', fields=exc.message_dict) from exc
    raise EngineeringValidationError(str(exc)) from exc


def _save(obj):
    try:
        obj.save()
        return obj
    except DjangoValidationError as exc:
        _raise_model_errors(exc)
    except IntegrityError as exc:
        raise EngineeringValidationError('Database constraint rejected the operation.', metadata={'database_error': str(exc)}) from exc


def normalized_token(value):
    return str(value or '').strip().upper()


def normalize_value(parameter, value):
    if not isinstance(value, dict):
        value = {'value': value}
    dt = parameter.data_type
    try:
        if dt in {ParameterDefinition.TYPE_TEXT, ParameterDefinition.TYPE_LONG_TEXT}:
            return {'value': str(value.get('value', '')).strip()}
        if dt == ParameterDefinition.TYPE_INTEGER:
            return {'value': int(value.get('value'))}
        if dt == ParameterDefinition.TYPE_DECIMAL:
            return {'value': str(Decimal(str(value.get('value'))).normalize())}
        if dt == ParameterDefinition.TYPE_BOOLEAN:
            return {'value': bool(value.get('value'))}
        if dt == ParameterDefinition.TYPE_DATE:
            return {'value': str(value.get('value'))}
        if dt == ParameterDefinition.TYPE_SINGLE_SELECT:
            return {'option_id': str(value.get('option_id'))}
        if dt == ParameterDefinition.TYPE_MULTI_SELECT:
            return {'option_ids': sorted(str(v) for v in value.get('option_ids', []))}
        if dt == ParameterDefinition.TYPE_UNIT_VALUE:
            return {'value': str(Decimal(str(value.get('value'))).normalize()), 'unit': normalized_token(value.get('unit'))}
        if dt == ParameterDefinition.TYPE_RANGE:
            return {'minimum': str(Decimal(str(value.get('minimum'))).normalize()), 'maximum': str(Decimal(str(value.get('maximum'))).normalize()), 'unit': normalized_token(value.get('unit'))}
        if dt == ParameterDefinition.TYPE_REFERENCE:
            return {'target_type': str(value.get('target_type')), 'target_id': str(value.get('target_id'))}
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise EngineeringValidationError('Invalid structured value.', fields={parameter.code: ['Value does not match Parameter data type.']}) from exc
    raise EngineeringValidationError('Unsupported Parameter data type.', fields={parameter.code: [dt]})


class ParameterDefinitionService:
    def create(self, *, actor, organization, code, name, data_type, description='', default_unit='', searchable=True, active=True):
        obj = ParameterDefinition(organization=organization, code=code, name=name, data_type=data_type, description=description, default_unit=default_unit, searchable=searchable, active=active, created_by=actor, updated_by=actor)
        _save(obj); log_event('engineering_parameter_created', target=obj, category='business'); return obj

    def update(self, parameter, *, actor, **changes):
        if 'code' in changes and changes['code'] != parameter.code and parameter.structure_usages.exists():
            raise EngineeringValidationError('Parameter code cannot change after meaningful use.', fields={'code': ['Parameter is used.']})
        if 'data_type' in changes and changes['data_type'] != parameter.data_type and parameter.part_values.exists():
            raise EngineeringValidationError('Parameter data type cannot change after stored values exist.', fields={'data_type': ['Stored values exist.']})
        for key, value in changes.items():
            if hasattr(parameter, key): setattr(parameter, key, value)
        parameter.updated_by = actor
        _save(parameter); log_event('engineering_parameter_updated', target=parameter, category='business'); return parameter

    def activate(self, parameter, *, actor): return self.update(parameter, actor=actor, active=True)
    def deactivate(self, parameter, *, actor): return self.update(parameter, actor=actor, active=False)

    def clone(self, parameter, *, actor, code=None, name=None):
        clone = self.create(actor=actor, organization=parameter.organization, code=code or f'{parameter.code}_COPY', name=name or f'{parameter.name} Copy', data_type=parameter.data_type, description=parameter.description, default_unit=parameter.default_unit, searchable=parameter.searchable, active=False)
        for opt in parameter.options.order_by('sort_order'):
            _save(ParameterOption(parameter=clone, display_label=opt.display_label, stored_value=opt.stored_value, description=opt.description, sort_order=opt.sort_order, active=opt.active, metadata=opt.metadata))
        return clone

    def bulk_create(self, *, actor, organization, rows, partial=False):
        errors, seen, created = {}, set(), []
        for index, row in enumerate(rows):
            code = normalized_token(row.get('code'))
            if not code or code in seen or ParameterDefinition.objects.filter(organization=organization, code=code).exists(): errors[index] = {'code': ['Duplicate or blank parameter code.']}
            seen.add(code)
            opts = [normalized_token(o.get('stored_value')) for o in row.get('options', [])]
            if len(opts) != len(set(opts)): errors.setdefault(index, {})['options'] = ['Duplicate option value.']
        if errors and not partial: return {'created': [], 'errors': errors, 'committed': False}
        with transaction.atomic():
            for i, row in enumerate(rows):
                if i in errors: continue
                p = self.create(actor=actor, organization=organization, code=row['code'], name=row['name'], data_type=row['data_type'], description=row.get('description', ''), default_unit=row.get('default_unit', ''))
                self.bulk_options(p, options=row.get('options', [])); created.append(p)
        return {'created': created, 'errors': errors, 'committed': True}

    def bulk_options(self, parameter, *, options):
        with transaction.atomic():
            parameter.options.all().delete(); result = []; seen = set()
            for index, row in enumerate(options):
                stored = normalized_token(row.get('stored_value'))
                if not stored or stored in seen: raise EngineeringValidationError('Duplicate or blank option value.', fields={f'options.{index}.stored_value': ['Invalid.']})
                seen.add(stored)
                result.append(_save(ParameterOption(parameter=parameter, display_label=row.get('display_label') or stored, stored_value=stored, description=row.get('description',''), sort_order=row.get('sort_order',(index+1)*10), active=row.get('active', True), metadata=row.get('metadata', {}))))
            return result


class ParameterMetadataService:
    def create_field(self, *, actor, organization, **data):
        obj = ParameterMetadataField(organization=organization, **data); _save(obj); return obj
    def update_field(self, field, **changes):
        for k,v in changes.items():
            if hasattr(field,k): setattr(field,k,v)
        return _save(field)
    def set_values(self, parameter, *, values):
        result=[]
        with transaction.atomic():
            for field_id, value in values.items():
                field=ParameterMetadataField.objects.get(pk=field_id)
                obj,_=ParameterMetadataValue.objects.get_or_create(parameter=parameter, field_definition=field, defaults={'value': value})
                obj.value=value; result.append(_save(obj))
        return result


class StructureDefinitionService:
    def create(self, *, actor, organization, code, name, description='', classification=None):
        obj=StructureDefinition(organization=organization, code=code, name=name, description=description, classification=classification, created_by=actor, updated_by=actor)
        _save(obj); log_event('engineering_structure_created', target=obj, category='business'); return obj
    def update(self, structure, *, actor, **changes):
        for k,v in changes.items():
            if hasattr(structure,k): setattr(structure,k,v)
        structure.updated_by=actor; return _save(structure)
    def set_parameters(self, structure, *, rows):
        if structure.parts.exists():
            existing={str(x.parameter_id) for x in structure.parameters.all()}; incoming={str(x['parameter']) for x in rows}
            if existing-incoming: raise EngineeringValidationError('Used StructureParameters cannot be removed.', fields={'parameters':['Structure has Parts.']})
        with transaction.atomic():
            structure.parameters.all().delete(); out=[]
            for i,row in enumerate(rows):
                p=ParameterDefinition.objects.get(pk=row['parameter'])
                out.append(_save(StructureParameter(structure=structure, parameter=p, sort_order=row.get('sort_order',(i+1)*10), display_group=row.get('display_group','Primary Characteristics'), requiredness=row.get('requiredness', StructureParameter.OPTIONAL), identity_defining=row.get('identity_defining',False), default_value=row.get('default_value',{}), visibility_condition=row.get('visibility_condition',{}), required_condition=row.get('required_condition',{}), active=row.get('active',True))))
            return out
    def reorder(self, structure, *, ordered_ids):
        with transaction.atomic():
            for i,pk in enumerate(ordered_ids):
                sp=structure.parameters.select_for_update().get(pk=pk); sp.sort_order=(i+1)*10; _save(sp)
        return list(structure.parameters.order_by('sort_order'))


class StructureValidationService:
    def validate(self, structure):
        errors={}; warnings=[]; params=list(structure.parameters.select_related('parameter').order_by('sort_order'))
        if not params: errors['parameters']=['Structure has no parameters.']
        if not any(p.identity_defining and p.active for p in params): warnings.append('No identity-defining parameter is configured.')
        if any(not p.parameter.active for p in params): errors.setdefault('parameters',[]).append('Inactive Parameter is assigned.')
        return {'ready': not errors, 'status': 'READY' if not errors else 'BLOCKED', 'errors': errors, 'warnings': warnings, 'parameter_count': len(params)}
class CodeDefinitionService:
    def create(self, *, actor, organization, structure, code, name, description='', separator='-', maximum_length=80, is_default=False):
        obj=CodeDefinition(organization=organization, structure=structure, code=code, name=name, description=description, separator=separator, maximum_length=maximum_length, is_default=is_default, created_by=actor)
        _save(obj); log_event('engineering_code_definition_created', target=obj, category='business'); return obj
    def set_default(self, definition):
        with transaction.atomic():
            CodeDefinition.objects.select_for_update().filter(structure=definition.structure, is_default=True).exclude(pk=definition.pk).update(is_default=False)
            definition.is_default=True; _save(definition); return definition
    def resolve_default_active(self, structure):
        definition=CodeDefinition.objects.filter(structure=structure, is_default=True).first() or CodeDefinition.objects.filter(structure=structure).first()
        if not definition: raise CodeDefinitionNotReadyError('No Code Definition exists for this Structure.')
        version=definition.versions.filter(status=CodeDefinitionVersion.STATUS_ACTIVE).first()
        if not version: raise CodeDefinitionNotReadyError('No Active Code Definition Version exists for this Structure.')
        return definition, version


class CodeDefinitionValidationService:
    def validate(self, version):
        errors={}; warnings=[]; segments=list(version.segments.select_related('parameter').prefetch_related('option_encodings').order_by('sort_order'))
        if not segments: errors['segments']=['Code Definition Version has no segments.']
        if not any(s.segment_type==CodeSegment.TYPE_SEQUENCE for s in segments): warnings.append('No sequence segment is configured.')
        for s in segments:
            try: s.full_clean()
            except DjangoValidationError as exc: errors[f'segments.{s.id}']=exc.message_dict if hasattr(exc,'message_dict') else exc.messages
            if s.segment_type==CodeSegment.TYPE_SEQUENCE and not version.sequences.exists(): errors[f'segments.{s.id}.sequence']=['SequenceDefinition is required.']
        return {'valid': not errors, 'activation_ready': not errors, 'errors': errors, 'warnings': warnings, 'segment_count': len(segments)}


class CodeDefinitionVersionService:
    def create_initial_draft(self, definition, *, actor):
        number=(definition.versions.order_by('-version_number').values_list('version_number', flat=True).first() or 0)+1
        return _save(CodeDefinitionVersion(code_definition=definition, version_number=number, status=CodeDefinitionVersion.STATUS_DRAFT, created_by=actor))
    def clone(self, version, *, actor):
        new=self.create_initial_draft(version.code_definition, actor=actor); new.configuration_snapshot=version.configuration_snapshot; _save(new)
        for s in version.segments.order_by('sort_order'):
            c=_save(CodeSegment(version=new, segment_type=s.segment_type, sort_order=s.sort_order, parameter=s.parameter, fixed_value=s.fixed_value, requiredness=s.requiredness, width=s.width, padding=s.padding, prefix=s.prefix, suffix=s.suffix, transform=s.transform, fallback=s.fallback, condition=s.condition, configuration=s.configuration))
            for e in s.option_encodings.all(): _save(CodeOptionEncoding(code_segment=c, parameter_option=e.parameter_option, encoded_token=e.encoded_token, active=e.active))
        for q in version.sequences.all(): _save(SequenceDefinition(code_definition_version=new, scope=q.scope, scope_key=q.scope_key, starting_value=q.starting_value, current_value=q.starting_value-1, width=q.width, padding_character=q.padding_character, reset_policy=q.reset_policy))
        log_event('engineering_code_version_cloned', target=new, category='business', extra={'source': str(version.pk)}); return new
    def activate(self, version, *, actor):
        if version.status != CodeDefinitionVersion.STATUS_DRAFT: raise InvalidVersionTransitionError('Only Draft versions can be activated.')
        with transaction.atomic():
            definition=CodeDefinition.objects.select_for_update().get(pk=version.code_definition_id)
            locked=CodeDefinitionVersion.objects.select_for_update().get(pk=version.pk)
            report=CodeDefinitionValidationService().validate(locked)
            if not report['activation_ready']: raise CodeDefinitionNotReadyError('Version is not activation-ready.', fields=report['errors'])
            for old in CodeDefinitionVersion.objects.select_for_update().filter(code_definition=definition, status=CodeDefinitionVersion.STATUS_ACTIVE).exclude(pk=locked.pk):
                old.status=CodeDefinitionVersion.STATUS_SUPERSEDED; old.superseded_at=timezone.now(); old.superseded_by=actor; _save(old)
            locked.status=CodeDefinitionVersion.STATUS_ACTIVE; locked.activated_at=timezone.now(); locked.activated_by=actor; _save(locked)
            definition.active_version=locked.version_number; definition.status=StructureDefinition.STATUS_ACTIVE; _save(definition)
            log_event('engineering_code_version_activated', target=locked, category='business'); return locked


class CodeSegmentService:
    def _ensure_draft(self, version):
        if version.status != CodeDefinitionVersion.STATUS_DRAFT: raise ImmutableVersionError('Only Draft versions can be mutated.')
    def set_segments(self, version, *, rows):
        self._ensure_draft(version)
        with transaction.atomic():
            version.segments.all().delete(); result=[]
            for i,row in enumerate(rows):
                p=ParameterDefinition.objects.get(pk=row['parameter']) if row.get('parameter') else None
                seg=_save(CodeSegment(version=version, segment_type=row['segment_type'], sort_order=row.get('sort_order',(i+1)*10), parameter=p, fixed_value=row.get('fixed_value',''), requiredness=row.get('requiredness',StructureParameter.REQUIRED), width=row.get('width'), padding=row.get('padding',''), prefix=row.get('prefix',''), suffix=row.get('suffix',''), transform=row.get('transform',{}), fallback=row.get('fallback',{}), condition=row.get('condition',{}), configuration=row.get('configuration',{})))
                for enc in row.get('option_encodings',[]):
                    _save(CodeOptionEncoding(code_segment=seg, parameter_option=ParameterOption.objects.get(pk=enc['parameter_option']), encoded_token=enc['encoded_token'], active=enc.get('active', True)))
                result.append(seg)
            return result
    def reorder(self, version, *, ordered_ids):
        self._ensure_draft(version)
        with transaction.atomic():
            for i,pk in enumerate(ordered_ids):
                s=version.segments.select_for_update().get(pk=pk); s.sort_order=(i+1)*10; _save(s)
        return list(version.segments.order_by('sort_order'))


@dataclass
class EncodedSegment:
    label: str
    token: str
    value: object
    segment_type: str
    sequence: dict | None = None


class ParameterCodec:
    def encode(self, segment, value):
        p=segment.parameter
        if value in ({}, None, ''):
            if segment.requiredness == StructureParameter.REQUIRED and not segment.fallback: raise CodeGenerationError('Required coding parameter is missing.', fields={p.code:['Required.']})
            value=segment.fallback or {}
        normalized=normalize_value(p, value); token=self._token_for(segment, p, normalized)
        if segment.transform.get('upper'): token=token.upper()
        if segment.width and token and len(token)<segment.width: token=token.rjust(segment.width, segment.padding or '0')
        if segment.width and token and len(token)>segment.width and segment.configuration.get('truncate'): token=token[:segment.width]
        if token: token=f'{segment.prefix}{token}{segment.suffix}'
        return EncodedSegment(p.name, token, normalized, CodeSegment.TYPE_PARAMETER)
    def _token_for(self, segment, p, normalized):
        if p.data_type == ParameterDefinition.TYPE_SINGLE_SELECT:
            opt=ParameterOption.objects.get(pk=normalized['option_id'], parameter=p)
            if not opt.active: raise CodeGenerationError('Inactive option cannot be used.', fields={p.code:['Inactive option.']})
            enc=segment.option_encodings.filter(parameter_option=opt, active=True).first()
            if not enc: raise CodeGenerationError('Missing option token mapping.', fields={p.code:['No token mapping.']})
            return enc.encoded_token
        if p.data_type == ParameterDefinition.TYPE_MULTI_SELECT:
            tokens=[]
            for oid in normalized.get('option_ids',[]):
                enc=segment.option_encodings.filter(parameter_option_id=oid, active=True).first()
                if not enc: raise CodeGenerationError('Missing option token mapping.', fields={p.code:['No token mapping.']})
                tokens.append(enc.encoded_token)
            return segment.configuration.get('multi_separator','+').join(tokens)
        if p.data_type == ParameterDefinition.TYPE_BOOLEAN: return 'Y' if normalized['value'] else 'N'
        if p.data_type == ParameterDefinition.TYPE_DATE: return normalized['value'].replace('-','')
        if p.data_type == ParameterDefinition.TYPE_UNIT_VALUE: return f"{normalized['value']}{normalized['unit']}"
        if p.data_type == ParameterDefinition.TYPE_RANGE: return f"{normalized['minimum']}-{normalized['maximum']}{normalized['unit']}"
        if p.data_type == ParameterDefinition.TYPE_REFERENCE: return normalized['target_id']
        return str(normalized.get('value',''))


class SequenceAllocationService:
    def next_value(self, version, *, preview, prefix='', year=None, idempotency_key=''):
        seq=self._resolve(version, prefix=prefix, year=year); nxt=max(seq.current_value+1, seq.starting_value)
        if len(str(nxt))>seq.width: raise SequenceExhaustedError('Sequence width exhausted.', metadata={'width': seq.width, 'next_value': nxt})
        token=str(nxt).rjust(seq.width, seq.padding_character); meta={'scope':seq.scope,'scope_key':seq.scope_key,'preview':preview,'issued_value':None if preview else nxt,'display_value':token}
        if preview: return token, meta
        with transaction.atomic():
            locked=SequenceDefinition.objects.select_for_update().get(pk=seq.pk); nxt=max(locked.current_value+1, locked.starting_value)
            if len(str(nxt))>locked.width:
                locked.exhausted_at=timezone.now(); locked.save(update_fields=['exhausted_at']); raise SequenceExhaustedError('Sequence width exhausted.', metadata={'width': locked.width, 'next_value': nxt})
            locked.current_value=nxt; _save(locked); return str(nxt).rjust(locked.width, locked.padding_character), {**meta, 'issued_value': nxt, 'display_value': str(nxt).rjust(locked.width, locked.padding_character)}
    def _resolve(self, version, *, prefix='', year=None):
        rows=version.sequences.all()
        if rows.filter(scope=SequenceDefinition.SCOPE_PREFIX).exists(): return rows.get(scope=SequenceDefinition.SCOPE_PREFIX, scope_key=normalized_token(prefix))
        if rows.filter(scope=SequenceDefinition.SCOPE_CALENDAR_YEAR).exists(): return rows.get(scope=SequenceDefinition.SCOPE_CALENDAR_YEAR, scope_key=str(year or timezone.localdate().year))
        seq=rows.filter(scope__in=[SequenceDefinition.SCOPE_CODE_DEFINITION, SequenceDefinition.SCOPE_GLOBAL, SequenceDefinition.SCOPE_STRUCTURE]).first() or rows.first()
        if not seq: raise SequenceAllocationError('No SequenceDefinition is configured.')
        return seq

class CodeGenerationService:
    def generate(self, *, organization, version, parameter_values, preview=True, sequence_allocator=None, idempotency_key=''):
        definition = version.code_definition
        definition.refresh_from_db()
        if definition.organization_id != organization.pk: raise CrossOrganizationError('Code Definition organization mismatch.')
        emitted=[]; decoded=[]; warnings=[]; seq_meta=None; prefix=''
        for s in version.segments.select_related('parameter').prefetch_related('option_encodings').order_by('sort_order'):
            enc=self._encode_segment(s, parameter_values, preview=preview, sequence_allocator=sequence_allocator, prefix=prefix)
            if enc.token:
                emitted.append(enc.token)
                if s.segment_type != CodeSegment.TYPE_SEQUENCE: prefix='-'.join(emitted)
            decoded.append({'label': enc.label, 'token': enc.token, 'value': enc.value, 'segment_type': enc.segment_type})
            if enc.sequence: seq_meta=enc.sequence; warnings.append('Preview sequence is not committed; final code may differ if another Part is created first.') if preview else None
        code=definition.separator.join([x for x in emitted if x])
        sep=definition.separator
        if sep and (code.startswith(sep) or code.endswith(sep) or sep*2 in code): raise CodeGenerationError('Generated code contains malformed separators.')
        if len(code)>definition.maximum_length: raise CodeGenerationError('Generated code exceeds maximum length.', metadata={'maximum_length': definition.maximum_length})
        return {'generated_code': code, 'decoded_segments': decoded, 'warnings': warnings, 'sequence': seq_meta, 'version_id': str(version.pk), 'code_definition_id': str(definition.pk)}
    def _encode_segment(self, s, parameter_values, *, preview, sequence_allocator, prefix=''):
        if s.segment_type == CodeSegment.TYPE_FIXED_TEXT: return EncodedSegment('Fixed text', f'{s.prefix}{s.fixed_value}{s.suffix}', s.fixed_value, s.segment_type)
        if s.segment_type == CodeSegment.TYPE_SEPARATOR: return EncodedSegment('Separator', '', '', s.segment_type)
        if s.segment_type == CodeSegment.TYPE_DATE:
            token=timezone.localdate().strftime(s.configuration.get('format','%Y%m%d')); return EncodedSegment('Date', f'{s.prefix}{token}{s.suffix}', token, s.segment_type)
        if s.segment_type == CodeSegment.TYPE_SEQUENCE:
            if not sequence_allocator: raise CodeGenerationError('Sequence allocator is required.')
            token, meta=sequence_allocator.next_value(s.version, preview=preview, prefix=prefix); return EncodedSegment('Sequence', f'{s.prefix}{token}{s.suffix}', token, s.segment_type, meta)
        if s.segment_type == CodeSegment.TYPE_PARAMETER:
            return ParameterCodec().encode(s, parameter_values.get(s.parameter.code) or parameter_values.get(str(s.parameter_id)))
        raise CodeGenerationError('Unsupported segment type.', fields={'segment_type':[s.segment_type]})


class PartIdentityService:
    def build_identity(self, *, organization, structure, parameter_values):
        payload={'organization': str(organization.pk), 'structure': str(structure.pk), 'parameters': {}}
        for sp in structure.parameters.select_related('parameter').filter(identity_defining=True, active=True).order_by('parameter__code'):
            raw=parameter_values.get(sp.parameter.code) or parameter_values.get(str(sp.parameter_id))
            if raw in ({}, None, ''):
                if sp.requiredness == StructureParameter.REQUIRED: raise EngineeringValidationError('Identity parameter is required.', fields={sp.parameter.code:['Required.']})
                continue
            payload['parameters'][sp.parameter.code]=normalize_value(sp.parameter, raw)
        encoded=json.dumps(payload, sort_keys=True, separators=(',',':'))
        return {'identity_payload': payload, 'identity_hash': hashlib.sha256(encoded.encode()).hexdigest()}


class DuplicateDetectionService:
    def check(self, *, organization, candidate_code='', identity_hash=''):
        out=[]
        if candidate_code:
            for item in Item.objects.filter(organization=organization, item_code__iexact=candidate_code)[:5]: out.append({'level':'BLOCKING_DUPLICATE','kind':'PART_NUMBER','item_id':str(item.pk),'part_number':item.item_code,'reason':'Part Number already exists.'})
        if identity_hash:
            for item in Item.objects.filter(organization=organization, semantic_identity_hash=identity_hash)[:10]: out.append({'level':'BLOCKING_DUPLICATE','kind':'SEMANTIC_IDENTITY','item_id':str(item.pk),'part_number':item.item_code,'reason':'Identity-defining parameter values match an existing Part.'})
        return out
    def raise_if_blocking(self, duplicates):
        blocking=[d for d in duplicates if d['level']=='BLOCKING_DUPLICATE']
        if blocking: raise DuplicatePartError('A Part with the same semantic identity or Part Number already exists.', metadata={'duplicates': blocking, 'existing_part_number': blocking[0].get('part_number')})


class CodePreviewService:
    def preview(self, *, version, organization, parameter_values, tolerate_missing=False):
        try:
            gen=CodeGenerationService().generate(organization=organization, version=version, parameter_values=parameter_values, preview=True, sequence_allocator=SequenceAllocationService())
            identity=PartIdentityService().build_identity(organization=organization, structure=version.code_definition.structure, parameter_values=parameter_values)
            dups=DuplicateDetectionService().check(organization=organization, candidate_code=gen['generated_code'], identity_hash=identity['identity_hash'])
            return {**gen, **identity, 'duplicates': dups, 'preview_only': True}
        except EngineeringValidationError:
            if tolerate_missing: return {'generated_code':'', 'warnings':['Sample output unavailable until required values are supplied.']}
            raise


class CodeDecodeService:
    def decode(self, *, code, version=None, definition=None, structure=None):
        if version: candidates=[version]
        elif definition: candidates=list(definition.versions.filter(status=CodeDefinitionVersion.STATUS_ACTIVE))
        elif structure: candidates=list(CodeDefinitionVersion.objects.filter(code_definition__structure=structure, status=CodeDefinitionVersion.STATUS_ACTIVE))
        else: candidates=list(CodeDefinitionVersion.objects.filter(status=CodeDefinitionVersion.STATUS_ACTIVE)[:20])
        matches=[self._decode_with_version(code, v) for v in candidates]; matches=[m for m in matches if m['confidence']>0]
        if len(matches)>1: raise AmbiguousDecodeError('Code matches multiple definitions.', metadata={'candidate_count': len(matches)})
        if not matches: raise CodeDecodeError('Code could not be decoded.')
        return matches[0]
    def _decode_with_version(self, code, version):
        tokens=code.split(version.code_definition.separator) if version.code_definition.separator else [code]
        segments=[s for s in version.segments.order_by('sort_order') if s.segment_type != CodeSegment.TYPE_SEPARATOR]
        if len(tokens)!=len(segments): return {'confidence':0,'matched_version':str(version.pk),'segments':[],'warnings':['Segment count mismatch.']}
        rows=[]
        for token,s in zip(tokens,segments): rows.append({'token': token, 'segment_type': s.segment_type, 'label': s.parameter.name if s.parameter_id else s.segment_type, 'value': self._decode_token(token,s)})
        return {'confidence':100,'matched_definition':str(version.code_definition_id),'matched_version':str(version.pk),'segments':rows,'warnings':[]}
    def _decode_token(self, token, s):
        if s.segment_type == CodeSegment.TYPE_FIXED_TEXT: return s.fixed_value if token == s.fixed_value else None
        if s.segment_type == CodeSegment.TYPE_SEQUENCE: return {'value': token}
        if s.segment_type == CodeSegment.TYPE_PARAMETER and s.parameter.data_type == ParameterDefinition.TYPE_SINGLE_SELECT:
            rows=list(s.option_encodings.filter(encoded_token__iexact=token, active=True).select_related('parameter_option'))
            if len(rows)==1: return {'option_id': str(rows[0].parameter_option_id), 'stored_value': rows[0].parameter_option.stored_value}
            if len(rows)>1: raise AmbiguousDecodeError('Token maps to multiple options.', metadata={'token': token})
        return {'value': token}


class TechnicalDataTemplateService:
    def create(self, *, actor, organization, structure, code, name, description=''):
        return _save(TechnicalDataTemplate(organization=organization, structure=structure, code=code, name=name, description=description, created_by=actor))
    def set_fields(self, template, *, rows):
        with transaction.atomic():
            template.fields.all().delete(); out=[]
            for i,row in enumerate(rows):
                out.append(_save(TechnicalFieldDefinition(template=template, code=row['code'], label=row['label'], description=row.get('description',''), data_type=row['data_type'], display_group=row.get('display_group','General'), scope=row.get('scope',TechnicalFieldDefinition.SCOPE_REVISION), requiredness=row.get('requiredness',TechnicalFieldDefinition.REQ_OPTIONAL), unit=row.get('unit',''), default_value=row.get('default_value',{}), validation_config=row.get('validation_config',{}), options=row.get('options',[]), sort_order=row.get('sort_order',(i+1)*10), active=row.get('active',True))))
            return out
    def resolve(self, structure): return TechnicalDataTemplate.objects.filter(structure=structure, active=True).order_by('-version').first()


class TechnicalDataValidationService:
    def validate(self, *, template, part_values=None, revision_values=None):
        from .models import _validate_structured_value
        errors={}; warnings=[]; part_values=part_values or {}; revision_values=revision_values or {}; required=provided=0
        if not template: return {'valid': True, 'errors': {}, 'warnings': [], 'completion': {'required':0,'provided':0}}
        for f in template.fields.filter(active=True):
            bucket=part_values if f.scope==TechnicalFieldDefinition.SCOPE_PART else revision_values
            value=bucket.get(f.code) or bucket.get(str(f.pk))
            if f.requiredness==TechnicalFieldDefinition.REQ_REQUIRED:
                required+=1
                if value in ({},None,''): errors[f.code]=['Required technical value is missing.']; continue
            if f.requiredness==TechnicalFieldDefinition.REQ_RECOMMENDED and value in ({},None,''): warnings.append(f'{f.label} is recommended.')
            if value not in ({},None,''):
                provided+=1
                try: _validate_structured_value(f.data_type, value, 'value')
                except DjangoValidationError as exc: errors[f.code]=exc.message_dict.get('value', exc.messages) if hasattr(exc,'message_dict') else exc.messages
        return {'valid': not errors, 'errors': errors, 'warnings': warnings, 'completion': {'required':required,'provided':provided}}


class PartCreationService:
    def preview(self, *, organization, structure, parameter_values, code_definition=None, version=None):
        if not code_definition or not version: code_definition, version = CodeDefinitionService().resolve_default_active(structure)
        return CodePreviewService().preview(version=version, organization=organization, parameter_values=parameter_values)
    def create(self, *, actor, organization, structure, item_type, name, parameter_values, part_technical_values=None, revision_technical_values=None, code_definition=None, version=None, idempotency_key='', base_unit='EA', make_or_buy='MAKE'):
        if idempotency_key:
            existing=Item.objects.filter(organization=organization, coding_snapshot__request_id=idempotency_key).first()
            if existing: return {'item': existing, 'revision': existing.revisions.order_by('created_at').first(), 'idempotent_replay': True, 'coding_snapshot': existing.coding_snapshot}
        with transaction.atomic():
            if not code_definition or not version: code_definition, version = CodeDefinitionService().resolve_default_active(structure)
            if item_type.organization_id != organization.pk: raise CrossOrganizationError('Item Type organization mismatch.')
            identity=PartIdentityService().build_identity(organization=organization, structure=structure, parameter_values=parameter_values)
            prev=CodeGenerationService().generate(organization=organization, version=version, parameter_values=parameter_values, preview=True, sequence_allocator=SequenceAllocationService())
            DuplicateDetectionService().raise_if_blocking(DuplicateDetectionService().check(organization=organization, candidate_code=prev['generated_code'], identity_hash=identity['identity_hash']))
            final=CodeGenerationService().generate(organization=organization, version=version, parameter_values=parameter_values, preview=False, sequence_allocator=SequenceAllocationService(), idempotency_key=idempotency_key)
            DuplicateDetectionService().raise_if_blocking(DuplicateDetectionService().check(organization=organization, candidate_code=final['generated_code'], identity_hash=identity['identity_hash']))
            template=TechnicalDataTemplateService().resolve(structure); report=TechnicalDataValidationService().validate(template=template, part_values=part_technical_values, revision_values=revision_technical_values)
            if not report['valid']: raise TechnicalDataValidationError('Technical data failed validation.', fields=report['errors'], metadata={'warnings': report['warnings']})
            snapshot=self._snapshot(structure, code_definition, version, final, identity, idempotency_key)
            item=_save(Item(organization=organization, item_type=item_type, structure=structure, code_definition=code_definition, code_definition_version=version, item_code=final['generated_code'], name=name, base_unit=base_unit, make_or_buy=make_or_buy, status='ACTIVE', semantic_identity_payload=identity['identity_payload'], semantic_identity_hash=identity['identity_hash'], coding_snapshot=snapshot, code_generation_strategy='PHASE_1F', generated_code_locked=True, created_by=actor))
            rev=_save(ItemRevision(item=item, revision=item.default_revision or 'R0', title=name))
            for sp in structure.parameters.select_related('parameter').filter(active=True):
                raw=parameter_values.get(sp.parameter.code) or parameter_values.get(str(sp.parameter_id))
                if raw in ({},None,''): continue
                norm=normalize_value(sp.parameter, raw)
                _save(PartParameterValue(part=item, structure_parameter=sp, parameter=sp.parameter, value=norm, normalized_value=json.dumps(norm, sort_keys=True), display_value=self._display_value(sp.parameter,norm)))
            if template:
                for f in template.fields.filter(scope=TechnicalFieldDefinition.SCOPE_PART, active=True):
                    v=(part_technical_values or {}).get(f.code) or (part_technical_values or {}).get(str(f.pk))
                    if v not in ({},None,''): _save(PartTechnicalValue(part=item, technical_field=f, value=v))
                for f in template.fields.filter(scope=TechnicalFieldDefinition.SCOPE_REVISION, active=True):
                    v=(revision_technical_values or {}).get(f.code) or (revision_technical_values or {}).get(str(f.pk))
                    if v not in ({},None,''): _save(RevisionTechnicalValue(revision=rev, technical_field=f, value=v))
            log_event('engineering_part_created', target=item, category='business', extra={'part_number': item.item_code, 'request_id': idempotency_key}); return {'item': item, 'revision': rev, 'idempotent_replay': False, 'coding_snapshot': snapshot}
    def _display_value(self, parameter, norm):
        if parameter.data_type == ParameterDefinition.TYPE_SINGLE_SELECT: return ParameterOption.objects.get(pk=norm['option_id']).display_label
        return ','.join(str(x) for x in norm.values())
    def _snapshot(self, structure, definition, version, generation, identity, request_id):
        return {'schema_version':1,'request_id':request_id,'structure':{'id':str(structure.pk),'code':structure.code,'name':structure.name},'code_definition':{'id':str(definition.pk),'code':definition.code,'name':definition.name},'code_definition_version':{'id':str(version.pk),'version_number':version.version_number},'separator':definition.separator,'segments':generation['decoded_segments'],'final_part_number':generation['generated_code'],'identity_payload':identity['identity_payload'],'identity_hash':identity['identity_hash'],'sequence':generation.get('sequence'),'generated_at':timezone.now().isoformat(),'generator':'phase1f-b'}
