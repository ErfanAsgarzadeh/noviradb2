from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from auditlog.services import log_event
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.permissions import can_manage_engineering, is_engineering_releaser

from .models import (
    InspectionApplicability,
    InspectionCharacteristic,
    InspectionExecution,
    InspectionMeasurement,
    InspectionPlanRevision,
    InspectionSample,
    ManufacturingOperationPrecedence,
    NonconformanceRecord,
    OperationExecution,
    ProductionInspectionRequirement,
    ProductionLot,
    ProductionOrder,
    ProductionSerial,
    QualityDisposition,
    QualityEvent,
    QualityHold,
)

Q6 = Decimal('0.000001')


class InspectionConflict(EngineeringLifecycleError):
    def __init__(self, inspection: InspectionExecution, expected_version: int):
        super().__init__({
            'code': 'inspection_version_conflict',
            'detail': 'Inspection version conflict.',
            'expected_version': expected_version,
            'current_version': inspection.inspection_version,
            'inspection_execution_id': str(inspection.pk),
        })


class NonconformanceConflict(EngineeringLifecycleError):
    def __init__(self, ncr: NonconformanceRecord, expected_version: int):
        super().__init__({
            'code': 'ncr_version_conflict',
            'detail': 'NCR version conflict.',
            'expected_version': expected_version,
            'current_version': ncr.ncr_version,
            'ncr_id': str(ncr.pk),
        })


def _require_actor(actor):
    if not getattr(actor, 'is_authenticated', False):
        raise EngineeringPermissionError('Authentication is required for quality actions.')


def _require_manager(actor):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError('You do not have permission to manage quality records.')


def _require_releaser(actor):
    if not is_engineering_releaser(actor):
        raise EngineeringPermissionError('You do not have permission to release quality records.')


def decimal_value(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Q6, rounding=ROUND_HALF_UP)


def _signature_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, 'pk'):
        return str(value.pk)
    if isinstance(value, dict):
        return {key: _signature_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_signature_value(item) for item in value]
    return value


def _signature_payload(action: str, payload: dict) -> dict:
    return {'action': action, **{key: _signature_value(value) for key, value in payload.items()}}


def _check_inspection_expected(inspection: InspectionExecution, expected_version):
    if expected_version is None:
        raise ValidationError({'expected_version': 'This field is required.'})
    expected = int(expected_version)
    if inspection.inspection_version != expected:
        raise InspectionConflict(inspection, expected)


def _check_ncr_expected(ncr: NonconformanceRecord, expected_version):
    if expected_version is None:
        raise ValidationError({'expected_version': 'This field is required.'})
    expected = int(expected_version)
    if ncr.ncr_version != expected:
        raise NonconformanceConflict(ncr, expected)


def _event_owner(inspection=None, production_order=None):
    return production_order or inspection.production_order


def _idempotent_event(production_order: ProductionOrder, key: str, payload_signature: dict):
    if not key:
        return None
    existing = QualityEvent.objects.filter(production_order=production_order, idempotency_key=key).first()
    if not existing:
        return None
    if existing.metadata.get('payload_signature') != payload_signature:
        raise EngineeringLifecycleError({'idempotency_key': 'Idempotency key was reused with a different payload.'})
    return existing


def _append_quality_event(*, actor, event_type, inspection=None, production_order=None, idempotency_key='', payload_signature=None, previous_version=0, new_version=0, notes='', metadata=None):
    owner = _event_owner(inspection=inspection, production_order=production_order)
    sequence = (QualityEvent.objects.filter(production_order=owner).aggregate(Max('event_sequence'))['event_sequence__max'] or 0) + 1
    event = QualityEvent.objects.create(
        production_order=owner,
        inspection_execution=inspection,
        event_sequence=sequence,
        event_type=event_type,
        actor=actor,
        event_timestamp=timezone.now(),
        idempotency_key=idempotency_key or f'auto:{sequence}',
        previous_version=previous_version,
        new_version=new_version,
        notes=notes,
        metadata={'payload_signature': payload_signature or {}, **(metadata or {})},
    )
    return event


@transaction.atomic
def release_inspection_plan_revision(revision: InspectionPlanRevision, *, actor):
    _require_releaser(actor)
    revision = InspectionPlanRevision.objects.select_for_update().get(pk=revision.pk)
    if revision.status != InspectionPlanRevision.STATUS_DRAFT:
        raise EngineeringLifecycleError({'status': 'Only draft inspection plan revisions can be released.'})
    if not revision.characteristics.exists():
        raise EngineeringLifecycleError({'characteristics': 'At least one characteristic is required before release.'})
    revision.status = InspectionPlanRevision.STATUS_RELEASED
    revision.released_by = actor
    revision.released_at = timezone.now()
    revision.save(update_fields=['status', 'released_by', 'released_at', 'updated_at'])
    log_event('inspection_plan_revision_released', target=revision, category='business')
    return revision


def _sampling_snapshot(revision: InspectionPlanRevision) -> dict:
    return {
        'characteristics': [
            {
                'id': str(ch.pk),
                'number': ch.characteristic_number,
                'sampling_method': ch.sampling_method,
                'sample_size': ch.sample_size,
                'inspect_all': ch.inspect_all,
                'acceptance_number': ch.acceptance_number,
                'rejection_number': ch.rejection_number,
                'percentage': str(ch.percentage) if ch.percentage is not None else None,
                'mandatory': ch.mandatory,
            }
            for ch in revision.characteristics.order_by('sequence', 'characteristic_number')
        ]
    }


def _applicability_matches(applicability: InspectionApplicability, operation=None, order=None) -> bool:
    if order and applicability.item_revision_id and applicability.item_revision_id != order.item_revision_id:
        return False
    if operation:
        if applicability.opc_node_id and applicability.opc_node_id != operation.source_opc_node_id:
            return False
        if applicability.process_definition_id and applicability.process_definition_id != operation.process_definition_id:
            return False
        if applicability.applicability_type == InspectionApplicability.TYPE_FINAL:
            return False
    else:
        if applicability.applicability_type != InspectionApplicability.TYPE_FINAL:
            return False
    return True


def create_inspection_requirements_for_order(order: ProductionOrder):
    created = []
    apps = InspectionApplicability.objects.select_related('inspection_plan_revision__inspection_plan').filter(
        inspection_plan_revision__status=InspectionPlanRevision.STATUS_RELEASED,
    ).order_by('sequence', 'created_at')
    operations = list(order.operations.select_related('source_opc_node', 'process_definition').order_by('sequence'))
    for operation in operations:
        for app in apps:
            if not _applicability_matches(app, operation=operation, order=order):
                continue
            req, was_created = ProductionInspectionRequirement.objects.get_or_create(
                production_order=order,
                operation=operation,
                inspection_plan_revision=app.inspection_plan_revision,
                defaults={
                    'source_applicability': app,
                    'inspection_type': app.inspection_plan_revision.inspection_plan.inspection_type,
                    'mandatory': app.mandatory,
                    'sampling_policy_snapshot': _sampling_snapshot(app.inspection_plan_revision),
                    'instruction_document_revision': app.inspection_plan_revision.instruction_document_revision,
                    'sequence': app.sequence,
                    'source_metadata_snapshot': {'applicability_type': app.applicability_type},
                },
            )
            if was_created:
                created.append(req)
    for app in apps:
        if not _applicability_matches(app, operation=None, order=order):
            continue
        req, was_created = ProductionInspectionRequirement.objects.get_or_create(
            production_order=order,
            operation=None,
            inspection_plan_revision=app.inspection_plan_revision,
            defaults={
                'source_applicability': app,
                'inspection_type': app.inspection_plan_revision.inspection_plan.inspection_type,
                'mandatory': app.mandatory,
                'sampling_policy_snapshot': _sampling_snapshot(app.inspection_plan_revision),
                'instruction_document_revision': app.inspection_plan_revision.instruction_document_revision,
                'sequence': app.sequence,
                'source_metadata_snapshot': {'applicability_type': app.applicability_type},
            },
        )
        if was_created:
            created.append(req)
    return created


def required_sample_count(requirement: ProductionInspectionRequirement) -> int:
    counts = [int(row.get('sample_size') or 1) for row in requirement.sampling_policy_snapshot.get('characteristics', [])]
    return max(counts or [1])


@transaction.atomic
def ensure_inspections_for_order(order: ProductionOrder, *, actor=None):
    if order.status != ProductionOrder.STATUS_RELEASED:
        raise EngineeringLifecycleError({'production_order': 'Inspection execution requires a released production order.'})
    created = []
    for requirement in order.inspection_requirements.select_related('operation').order_by('operation__sequence', 'sequence'):
        operation_execution = getattr(requirement.operation, 'execution', None) if requirement.operation_id else None
        inspection, was_created = InspectionExecution.objects.get_or_create(
            requirement=requirement,
            operation_execution=operation_execution,
            serial=None,
            lot=None,
            defaults={
                'production_order': order,
                'execution_cycle': operation_execution.cycles.order_by('-cycle_number').first() if operation_execution else None,
                'status': InspectionExecution.STATUS_READY,
            },
        )
        if was_created:
            for number in range(1, required_sample_count(requirement) + 1):
                InspectionSample.objects.create(inspection_execution=inspection, sample_number=number, quantity_represented=Decimal('1'))
            created.append(inspection)
    return created


def allowed_inspection_actions(inspection: InspectionExecution) -> list[str]:
    if inspection.status in {InspectionExecution.STATUS_PENDING, InspectionExecution.STATUS_READY}:
        return ['assign_inspector', 'start', 'place_hold', 'cancel']
    if inspection.status == InspectionExecution.STATUS_IN_PROGRESS:
        return ['record_measurement', 'correct_measurement', 'evaluate', 'complete', 'place_hold', 'create_ncr']
    if inspection.status == InspectionExecution.STATUS_ON_HOLD:
        return ['release_hold']
    if inspection.status == InspectionExecution.STATUS_FAILED:
        return ['create_ncr', 'place_hold']
    return []


def evaluate_measurement_value(characteristic: InspectionCharacteristic, *, numeric_value=None, boolean_value=None, attribute_value='', text_value='', explicit_result='') -> dict:
    kind = characteristic.characteristic_type
    if kind == InspectionCharacteristic.TYPE_NUMERIC:
        if numeric_value is None:
            return {'result': InspectionMeasurement.RESULT_INCOMPLETE, 'code': 'numeric_missing', 'message': 'Numeric value is required.', 'blocking': characteristic.mandatory}
        value = decimal_value(numeric_value)
        lower, upper = characteristic.effective_limits()
        if lower is not None and value < lower:
            return {'result': InspectionMeasurement.RESULT_FAIL, 'code': 'below_lower_limit', 'message': 'Value is below lower specification limit.', 'lower': str(lower), 'upper': str(upper) if upper is not None else None, 'observed': str(value), 'blocking': characteristic.mandatory}
        if upper is not None and value > upper:
            return {'result': InspectionMeasurement.RESULT_FAIL, 'code': 'above_upper_limit', 'message': 'Value is above upper specification limit.', 'lower': str(lower) if lower is not None else None, 'upper': str(upper), 'observed': str(value), 'blocking': characteristic.mandatory}
        return {'result': InspectionMeasurement.RESULT_PASS, 'code': 'within_limits', 'message': 'Value is within specification.', 'lower': str(lower) if lower is not None else None, 'upper': str(upper) if upper is not None else None, 'observed': str(value), 'blocking': False}
    if kind == InspectionCharacteristic.TYPE_BOOLEAN:
        if boolean_value is None:
            return {'result': InspectionMeasurement.RESULT_INCOMPLETE, 'code': 'boolean_missing', 'message': 'Boolean value is required.', 'blocking': characteristic.mandatory}
        passed = bool(boolean_value) == characteristic.expected_boolean
        return {'result': InspectionMeasurement.RESULT_PASS if passed else InspectionMeasurement.RESULT_FAIL, 'code': 'boolean_match' if passed else 'boolean_mismatch', 'message': 'Boolean value matches.' if passed else 'Boolean value does not match.', 'observed': bool(boolean_value), 'blocking': characteristic.mandatory and not passed}
    if kind == InspectionCharacteristic.TYPE_ATTRIBUTE:
        passed = attribute_value in characteristic.allowed_attribute_values
        return {'result': InspectionMeasurement.RESULT_PASS if passed else InspectionMeasurement.RESULT_FAIL, 'code': 'attribute_allowed' if passed else 'attribute_rejected', 'message': 'Attribute value is allowed.' if passed else 'Attribute value is not allowed.', 'observed': attribute_value, 'blocking': characteristic.mandatory and not passed}
    result = (explicit_result or '').upper()
    if result not in {InspectionMeasurement.RESULT_PASS, InspectionMeasurement.RESULT_FAIL}:
        return {'result': InspectionMeasurement.RESULT_INCOMPLETE, 'code': 'explicit_result_required', 'message': 'Text or visual inspection requires explicit PASS or FAIL.', 'blocking': characteristic.mandatory}
    return {'result': result, 'code': 'explicit_result', 'message': f'Inspector recorded {result}.', 'observed': text_value, 'blocking': characteristic.mandatory and result == InspectionMeasurement.RESULT_FAIL}


def active_measurements(inspection: InspectionExecution):
    superseded = set(InspectionMeasurement.objects.filter(inspection_execution=inspection, supersedes_id__isnull=False).values_list('supersedes_id', flat=True))
    return inspection.measurements.exclude(pk__in=superseded).select_related('characteristic', 'sample').order_by('sample__sample_number', 'characteristic__sequence', '-measurement_sequence')


def aggregate_inspection(inspection: InspectionExecution) -> dict:
    characteristics = list(inspection.requirement.inspection_plan_revision.characteristics.order_by('sequence', 'characteristic_number'))
    samples = list(inspection.samples.order_by('sample_number'))
    active = list(active_measurements(inspection))
    by_key = {(item.sample_id, item.characteristic_id): item for item in active}
    missing = []
    fail_count = 0
    pass_count = 0
    incomplete_count = 0
    for sample in samples:
        sample_failed = False
        sample_incomplete = False
        for characteristic in characteristics:
            measurement = by_key.get((sample.pk, characteristic.pk))
            if not measurement:
                if characteristic.mandatory:
                    missing.append(str(characteristic.pk))
                    incomplete_count += 1
                    sample_incomplete = True
                continue
            if measurement.evaluation_result == InspectionMeasurement.RESULT_FAIL:
                fail_count += 1
                if characteristic.mandatory:
                    sample_failed = True
            elif measurement.evaluation_result == InspectionMeasurement.RESULT_PASS:
                pass_count += 1
            else:
                incomplete_count += 1
                if characteristic.mandatory:
                    sample_incomplete = True
        if sample_failed:
            sample.status = InspectionSample.STATUS_FAIL
        elif sample_incomplete:
            sample.status = InspectionSample.STATUS_INCOMPLETE
        else:
            sample.status = InspectionSample.STATUS_PASS
        sample.save(update_fields=['status', 'updated_at'])
    active_holds = inspection.quality_holds.filter(active=True).count()
    unresolved_ncrs = inspection.nonconformances.exclude(status__in=[NonconformanceRecord.STATUS_CLOSED, NonconformanceRecord.STATUS_CANCELLED]).count()
    if missing:
        result = InspectionExecution.RESULT_INCOMPLETE
    elif fail_count:
        result = InspectionExecution.RESULT_FAIL
    else:
        result = InspectionExecution.RESULT_PASS
    return {
        'result': result,
        'missing_characteristics': missing,
        'pass_count': pass_count,
        'fail_count': fail_count,
        'incomplete_count': incomplete_count,
        'active_holds': active_holds,
        'unresolved_ncrs': unresolved_ncrs,
        'complete': not missing and active_holds == 0 and (result != InspectionExecution.RESULT_FAIL or unresolved_ncrs == 0),
    }


@transaction.atomic
def start_inspection(inspection: InspectionExecution, *, actor, expected_version, idempotency_key='', assign_to_self=True, notes=''):
    _require_actor(actor)
    inspection = InspectionExecution.objects.select_for_update().get(pk=inspection.pk)
    signature = _signature_payload('start', {'assign_to_self': assign_to_self, 'notes': notes})
    existing = _idempotent_event(inspection.production_order, idempotency_key, signature)
    if existing:
        return inspection, existing
    _check_inspection_expected(inspection, expected_version)
    previous = inspection.inspection_version
    inspection.status = InspectionExecution.STATUS_IN_PROGRESS
    inspection.started_at = inspection.started_at or timezone.now()
    if assign_to_self:
        inspection.assigned_inspector = actor
    inspection.inspection_version += 1
    event = _append_quality_event(actor=actor, inspection=inspection, event_type=QualityEvent.EVENT_EVALUATION, idempotency_key=idempotency_key, payload_signature=signature, previous_version=previous, new_version=inspection.inspection_version, notes=notes, metadata={'action': 'start'})
    inspection.save()
    log_event('inspection_started', target=inspection, category='business')
    return inspection, event


@transaction.atomic
def record_measurement(inspection: InspectionExecution, *, actor, expected_version, sample: InspectionSample, characteristic: InspectionCharacteristic, idempotency_key='', correction_for=None, explicit_result='', notes='', **values):
    _require_actor(actor)
    inspection = InspectionExecution.objects.select_for_update().get(pk=inspection.pk)
    signature = _signature_payload('record_measurement', {'sample': sample, 'characteristic': characteristic, 'correction_for': correction_for, 'explicit_result': explicit_result, 'values': values, 'notes': notes})
    existing = _idempotent_event(inspection.production_order, idempotency_key, signature)
    if existing:
        existing_measurement = getattr(existing, 'measurement', None)
        if existing_measurement:
            return inspection, existing_measurement
        return inspection, existing
    _check_inspection_expected(inspection, expected_version)
    if inspection.status != InspectionExecution.STATUS_IN_PROGRESS:
        raise EngineeringLifecycleError({'status': 'Measurements require an in-progress inspection.'})
    evaluation = evaluate_measurement_value(characteristic, explicit_result=explicit_result, **values)
    previous = inspection.inspection_version
    inspection.inspection_version += 1
    sequence = (InspectionMeasurement.objects.filter(inspection_execution=inspection, sample=sample, characteristic=characteristic).aggregate(Max('measurement_sequence'))['measurement_sequence__max'] or 0) + 1
    event = _append_quality_event(actor=actor, inspection=inspection, event_type=QualityEvent.EVENT_CORRECTION if correction_for else QualityEvent.EVENT_MEASUREMENT, idempotency_key=idempotency_key, payload_signature=signature, previous_version=previous, new_version=inspection.inspection_version, notes=notes, metadata={'evaluation': evaluation})
    measurement = InspectionMeasurement.objects.create(
        inspection_execution=inspection,
        sample=sample,
        characteristic=characteristic,
        measurement_sequence=sequence,
        numeric_value=values.get('numeric_value'),
        boolean_value=values.get('boolean_value'),
        attribute_value=values.get('attribute_value') or '',
        text_value=values.get('text_value') or '',
        observed_by=actor,
        observed_at=timezone.now(),
        evaluation_result=evaluation['result'],
        nonconforming=evaluation['result'] == InspectionMeasurement.RESULT_FAIL,
        notes=notes,
        supersedes=correction_for,
        quality_event=event,
    )
    aggregate = aggregate_inspection(inspection)
    inspection.result = aggregate['result']
    inspection.evaluated_at = timezone.now()
    inspection.save(update_fields=['inspection_version', 'result', 'evaluated_at', 'updated_at'])
    log_event('inspection_measurement_recorded', target=inspection, category='business')
    return inspection, measurement


@transaction.atomic
def evaluate_inspection(inspection: InspectionExecution, *, actor, expected_version, idempotency_key='', notes=''):
    _require_actor(actor)
    inspection = InspectionExecution.objects.select_for_update().get(pk=inspection.pk)
    signature = _signature_payload('evaluate', {'notes': notes})
    existing = _idempotent_event(inspection.production_order, idempotency_key, signature)
    if existing:
        return inspection, existing
    _check_inspection_expected(inspection, expected_version)
    previous = inspection.inspection_version
    aggregate = aggregate_inspection(inspection)
    inspection.result = aggregate['result']
    inspection.evaluated_at = timezone.now()
    inspection.inspection_version += 1
    event = _append_quality_event(actor=actor, inspection=inspection, event_type=QualityEvent.EVENT_EVALUATION, idempotency_key=idempotency_key, payload_signature=signature, previous_version=previous, new_version=inspection.inspection_version, notes=notes, metadata={'aggregate': aggregate})
    inspection.save(update_fields=['inspection_version', 'result', 'evaluated_at', 'updated_at'])
    return inspection, event


@transaction.atomic
def complete_inspection(inspection: InspectionExecution, *, actor, expected_version, idempotency_key='', notes=''):
    _require_actor(actor)
    inspection, event = evaluate_inspection(inspection, actor=actor, expected_version=expected_version, idempotency_key=idempotency_key or '', notes=notes)
    aggregate = aggregate_inspection(inspection)
    if aggregate['missing_characteristics']:
        raise EngineeringLifecycleError({'inspection': 'Mandatory measurements are incomplete.'})
    if aggregate['active_holds']:
        raise EngineeringLifecycleError({'quality_hold': 'Active quality hold blocks inspection completion.'})
    if inspection.result == InspectionExecution.RESULT_FAIL:
        inspection.status = InspectionExecution.STATUS_FAILED
        inspection.disposition_status = InspectionExecution.DISPOSITION_REQUIRED
    else:
        inspection.status = InspectionExecution.STATUS_COMPLETED
        inspection.disposition_status = InspectionExecution.DISPOSITION_NONE
        inspection.completed_at = timezone.now()
    inspection.save(update_fields=['status', 'disposition_status', 'completed_at', 'updated_at'])
    return inspection, event


@transaction.atomic
def place_quality_hold(*, actor, production_order: ProductionOrder, expected_version=None, inspection=None, operation_execution=None, serial=None, lot=None, scope=QualityHold.SCOPE_ORDER, reason='', idempotency_key=''):
    _require_actor(actor)
    signature = _signature_payload('place_hold', {'inspection': inspection, 'operation_execution': operation_execution, 'serial': serial, 'lot': lot, 'scope': scope, 'reason': reason})
    existing = _idempotent_event(production_order, idempotency_key, signature)
    if existing:
        hold = getattr(existing, 'placed_hold', None)
        if hold:
            return hold
    previous = inspection.inspection_version if inspection else 0
    if inspection:
        inspection = InspectionExecution.objects.select_for_update().get(pk=inspection.pk)
        _check_inspection_expected(inspection, expected_version)
        inspection.inspection_version += 1
        inspection.status = InspectionExecution.STATUS_ON_HOLD
        inspection.save(update_fields=['inspection_version', 'status', 'updated_at'])
    event = _append_quality_event(actor=actor, inspection=inspection, production_order=production_order, event_type=QualityEvent.EVENT_HOLD, idempotency_key=idempotency_key, payload_signature=signature, previous_version=previous, new_version=inspection.inspection_version if inspection else 0, notes=reason)
    hold = QualityHold.objects.create(production_order=production_order, operation_execution=operation_execution, inspection_execution=inspection, serial=serial, lot=lot, scope=scope, reason=reason, placed_by=actor, placed_at=event.event_timestamp, placed_event=event)
    return hold


@transaction.atomic
def release_quality_hold(hold: QualityHold, *, actor, reason='', idempotency_key=''):
    _require_manager(actor)
    hold = QualityHold.objects.select_for_update(of=('self',)).select_related('production_order').get(pk=hold.pk)
    if not hold.active:
        return hold
    signature = _signature_payload('release_hold', {'hold': hold, 'reason': reason})
    event = _append_quality_event(actor=actor, inspection=hold.inspection_execution, production_order=hold.production_order, event_type=QualityEvent.EVENT_RELEASE_HOLD, idempotency_key=idempotency_key, payload_signature=signature, notes=reason)
    hold.active = False
    hold.released_by = actor
    hold.released_at = event.event_timestamp
    hold.release_reason = reason
    hold.released_event = event
    hold.save(update_fields=['active', 'released_by', 'released_at', 'release_reason', 'released_event'])
    if hold.inspection_execution_id and hold.inspection_execution.status == InspectionExecution.STATUS_ON_HOLD:
        hold.inspection_execution.status = InspectionExecution.STATUS_IN_PROGRESS
        hold.inspection_execution.save(update_fields=['status', 'updated_at'])
    return hold


def generate_ncr_number(now=None) -> str:
    now = now or timezone.now()
    prefix = f'NCR-{now:%Y}-'
    latest = NonconformanceRecord.objects.select_for_update().filter(ncr_number__startswith=prefix).aggregate(Max('ncr_number'))['ncr_number__max']
    next_number = int((latest or f'{prefix}000000')[-6:]) + 1
    return f'{prefix}{next_number:06d}'


@transaction.atomic
def create_ncr(*, actor, production_order: ProductionOrder, operation_execution=None, inspection=None, measurement=None, serial=None, lot=None, defect_code='', defect_description='', severity=NonconformanceRecord.SEVERITY_MINOR, affected_quantity='1.000000', idempotency_key=''):
    _require_actor(actor)
    signature = _signature_payload('create_ncr', {'operation_execution': operation_execution, 'inspection': inspection, 'measurement': measurement, 'serial': serial, 'lot': lot, 'defect_code': defect_code, 'defect_description': defect_description, 'severity': severity, 'affected_quantity': affected_quantity})
    existing = _idempotent_event(production_order, idempotency_key, signature)
    if existing:
        ncr = getattr(existing, 'ncr_record', None)
        if ncr:
            return ncr
    event = _append_quality_event(actor=actor, inspection=inspection, production_order=production_order, event_type=QualityEvent.EVENT_NCR, idempotency_key=idempotency_key, payload_signature=signature, notes=defect_description)
    ncr = NonconformanceRecord.objects.create(
        ncr_number=generate_ncr_number(),
        production_order=production_order,
        operation_execution=operation_execution,
        inspection_execution=inspection,
        measurement=measurement,
        serial=serial,
        lot=lot,
        defect_code=defect_code,
        defect_description=defect_description,
        severity=severity,
        affected_quantity=decimal_value(affected_quantity),
        owner=actor,
        created_by=actor,
        quality_event=event,
    )
    return ncr


def allowed_ncr_actions(ncr: NonconformanceRecord) -> list[str]:
    if ncr.status == NonconformanceRecord.STATUS_OPEN:
        return ['review', 'propose_disposition', 'cancel']
    if ncr.status == NonconformanceRecord.STATUS_UNDER_REVIEW:
        return ['propose_disposition', 'cancel']
    if ncr.status == NonconformanceRecord.STATUS_DISPOSITIONED:
        return ['close']
    return []


@transaction.atomic
def transition_ncr(ncr: NonconformanceRecord, *, actor, expected_version, action, idempotency_key='', notes=''):
    _require_actor(actor)
    ncr = NonconformanceRecord.objects.select_for_update().get(pk=ncr.pk)
    _check_ncr_expected(ncr, expected_version)
    if action == 'review' and ncr.status == NonconformanceRecord.STATUS_OPEN:
        ncr.status = NonconformanceRecord.STATUS_UNDER_REVIEW
    elif action == 'cancel' and ncr.status in {NonconformanceRecord.STATUS_OPEN, NonconformanceRecord.STATUS_UNDER_REVIEW}:
        ncr.status = NonconformanceRecord.STATUS_CANCELLED
    elif action == 'close' and ncr.status == NonconformanceRecord.STATUS_DISPOSITIONED:
        if ncr.dispositions.exclude(status=QualityDisposition.STATUS_IMPLEMENTED).exists():
            raise EngineeringLifecycleError({'disposition': 'All dispositions must be implemented before NCR closure.'})
        ncr.status = NonconformanceRecord.STATUS_CLOSED
        ncr.closed_at = timezone.now()
    else:
        raise EngineeringLifecycleError({'action': 'NCR action is not allowed.'})
    ncr.ncr_version += 1
    _append_quality_event(actor=actor, production_order=ncr.production_order, inspection=ncr.inspection_execution, event_type=QualityEvent.EVENT_NCR, idempotency_key=idempotency_key, payload_signature=_signature_payload(action, {'notes': notes}), previous_version=ncr.ncr_version - 1, new_version=ncr.ncr_version, notes=notes, metadata={'ncr_id': str(ncr.pk), 'action': action})
    ncr.save()
    return ncr


@transaction.atomic
def propose_disposition(ncr: NonconformanceRecord, *, actor, expected_version, disposition_type, quantity, reason, instructions='', target_rework_edge=None, instruction_document_revision=None, idempotency_key=''):
    _require_actor(actor)
    ncr = NonconformanceRecord.objects.select_for_update().get(pk=ncr.pk)
    _check_ncr_expected(ncr, expected_version)
    disposition = QualityDisposition.objects.create(
        ncr=ncr,
        disposition_type=disposition_type,
        quantity=decimal_value(quantity),
        reason=reason,
        instructions=instructions,
        target_rework_edge=target_rework_edge,
        instruction_document_revision=instruction_document_revision,
        proposed_by=actor,
    )
    ncr.status = NonconformanceRecord.STATUS_UNDER_REVIEW
    ncr.ncr_version += 1
    _append_quality_event(actor=actor, production_order=ncr.production_order, inspection=ncr.inspection_execution, event_type=QualityEvent.EVENT_DISPOSITION, idempotency_key=idempotency_key, payload_signature=_signature_payload('propose_disposition', {'disposition': disposition}), previous_version=ncr.ncr_version - 1, new_version=ncr.ncr_version, notes=reason, metadata={'disposition_id': str(disposition.pk)})
    ncr.save(update_fields=['status', 'ncr_version'])
    return ncr, disposition


@transaction.atomic
def approve_disposition(disposition: QualityDisposition, *, actor, expected_version, idempotency_key='', notes=''):
    _require_releaser(actor)
    ncr = NonconformanceRecord.objects.select_for_update().get(pk=disposition.ncr_id)
    _check_ncr_expected(ncr, expected_version)
    if disposition.disposition_type == QualityDisposition.TYPE_USE_AS_IS and not is_engineering_releaser(actor):
        raise EngineeringPermissionError('Use-as-is disposition requires elevated authorization.')
    disposition.status = QualityDisposition.STATUS_APPROVED
    disposition.approved_by = actor
    disposition.approved_at = timezone.now()
    disposition.save(update_fields=['status', 'approved_by', 'approved_at'])
    ncr.status = NonconformanceRecord.STATUS_DISPOSITIONED
    ncr.ncr_version += 1
    _append_quality_event(actor=actor, production_order=ncr.production_order, inspection=ncr.inspection_execution, event_type=QualityEvent.EVENT_DISPOSITION, idempotency_key=idempotency_key, payload_signature=_signature_payload('approve_disposition', {'disposition': disposition, 'notes': notes}), previous_version=ncr.ncr_version - 1, new_version=ncr.ncr_version, notes=notes, metadata={'disposition_id': str(disposition.pk), 'status': disposition.status})
    ncr.save(update_fields=['status', 'ncr_version'])
    return ncr, disposition


@transaction.atomic
def implement_disposition(disposition: QualityDisposition, *, actor, expected_version, idempotency_key='', notes=''):
    _require_actor(actor)
    ncr = NonconformanceRecord.objects.select_for_update().get(pk=disposition.ncr_id)
    _check_ncr_expected(ncr, expected_version)
    if disposition.status != QualityDisposition.STATUS_APPROVED:
        raise EngineeringLifecycleError({'status': 'Only approved dispositions can be implemented.'})
    if disposition.disposition_type == QualityDisposition.TYPE_REWORK and ncr.operation_execution_id:
        from .execution_services import command_execution

        execution, _event = command_execution(ncr.operation_execution, actor=actor, expected_version=ncr.operation_execution.execution_version, command='rework', rework_edge=disposition.target_rework_edge, quantity=disposition.quantity, idempotency_key=f'quality-rework:{disposition.pk}', notes=notes)
        disposition.rework_cycle = disposition.target_rework_edge.successor.execution.cycles.order_by('-cycle_number').first()
    if disposition.disposition_type == QualityDisposition.TYPE_REINSPECT and ncr.inspection_execution_id:
        source = ncr.inspection_execution
        reinspection = InspectionExecution.objects.create(requirement=source.requirement, production_order=source.production_order, operation_execution=source.operation_execution, execution_cycle=source.execution_cycle, serial=source.serial, lot=source.lot, status=InspectionExecution.STATUS_READY)
        for sample in source.samples.order_by('sample_number'):
            InspectionSample.objects.create(inspection_execution=reinspection, sample_number=sample.sample_number, serial=sample.serial, lot=sample.lot, quantity_represented=sample.quantity_represented)
        disposition.reinspection = reinspection
    disposition.status = QualityDisposition.STATUS_IMPLEMENTED
    disposition.implemented_by = actor
    disposition.implemented_at = timezone.now()
    disposition.save(update_fields=['status', 'implemented_by', 'implemented_at', 'rework_cycle', 'reinspection'])
    ncr.ncr_version += 1
    _append_quality_event(actor=actor, production_order=ncr.production_order, inspection=ncr.inspection_execution, event_type=QualityEvent.EVENT_REWORK if disposition.disposition_type == QualityDisposition.TYPE_REWORK else QualityEvent.EVENT_DISPOSITION, idempotency_key=idempotency_key, payload_signature=_signature_payload('implement_disposition', {'disposition': disposition, 'notes': notes}), previous_version=ncr.ncr_version - 1, new_version=ncr.ncr_version, notes=notes, metadata={'disposition_id': str(disposition.pk), 'status': disposition.status})
    ncr.save(update_fields=['ncr_version'])
    return ncr, disposition


def operation_quality_blockers(execution: OperationExecution) -> list[str]:
    blockers = []
    required = execution.production_order.inspection_requirements.filter(operation=execution.manufacturing_operation, mandatory=True)
    for requirement in required:
        inspections = requirement.inspection_executions.filter(operation_execution=execution)
        if not inspections.exists():
            blockers.append(f'Mandatory inspection {requirement.inspection_plan_revision.inspection_plan.plan_code} is missing.')
            continue
        if inspections.exclude(status=InspectionExecution.STATUS_COMPLETED, result=InspectionExecution.RESULT_PASS).exists():
            blockers.append(f'Mandatory inspection {requirement.inspection_plan_revision.inspection_plan.plan_code} is not passed.')
    if execution.quality_holds.filter(active=True).exists() or execution.production_order.quality_holds.filter(active=True, operation_execution__isnull=True).exists():
        blockers.append('Active quality hold blocks completion.')
    if execution.nonconformances.exclude(status__in=[NonconformanceRecord.STATUS_CLOSED, NonconformanceRecord.STATUS_CANCELLED]).exists():
        blockers.append('Unresolved nonconformance blocks completion.')
    return blockers


def ensure_operation_quality_gate(execution: OperationExecution):
    blockers = operation_quality_blockers(execution)
    if blockers:
        raise EngineeringLifecycleError({'quality_gate': blockers})


def order_quality_summary(order: ProductionOrder) -> dict:
    inspections = order.inspection_executions.all()
    return {
        'inspection_count': inspections.count(),
        'passed_count': inspections.filter(status=InspectionExecution.STATUS_COMPLETED, result=InspectionExecution.RESULT_PASS).count(),
        'failed_count': inspections.filter(result=InspectionExecution.RESULT_FAIL).count(),
        'active_hold_count': order.quality_holds.filter(active=True).count(),
        'open_ncr_count': order.nonconformances.exclude(status__in=[NonconformanceRecord.STATUS_CLOSED, NonconformanceRecord.STATUS_CANCELLED]).count(),
        'required_count': order.inspection_requirements.filter(mandatory=True).count(),
    }
