from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework import status

from opc.execution_services import acknowledge_document, command_execution, ensure_executions_for_order, record_tooling_usage
from opc.models import (
    InspectionApplicability,
    InspectionCharacteristic,
    InspectionExecution,
    InspectionMeasurement,
    InspectionPlan,
    InspectionPlanRevision,
    NonconformanceRecord,
    OperationExecution,
    ProductionInspectionRequirement,
    QualityDisposition,
    QualityEvent,
    QualityHold,
)
from opc.production_services import generate_order_baseline, release_production_order
from opc.quality_services import (
    approve_disposition,
    complete_inspection,
    create_ncr,
    ensure_inspections_for_order,
    evaluate_measurement_value,
    implement_disposition,
    place_quality_hold,
    propose_disposition,
    record_measurement,
    release_quality_hold,
    release_inspection_plan_revision,
    start_inspection,
)
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase7_production_orders import create_order, released_source


pytestmark = pytest.mark.django_db


def make_released_plan(admin, refs, *, code='IP-P9', node=None):
    plan = InspectionPlan.objects.create(plan_code=code, name=f'{code} Dimensional', inspection_type=InspectionPlan.TYPE_IN_PROCESS)
    revision = InspectionPlanRevision.objects.create(inspection_plan=plan, revision='A', instruction_document_revision=refs['document_revision'], created_by=admin)
    numeric = InspectionCharacteristic.objects.create(
        inspection_plan_revision=revision,
        characteristic_number='DIM-001',
        name='Length',
        characteristic_type=InspectionCharacteristic.TYPE_NUMERIC,
        unit='MM',
        lower_spec_limit='9.500000',
        upper_spec_limit='10.500000',
        sample_size=1,
        sequence=1,
    )
    boolean = InspectionCharacteristic.objects.create(
        inspection_plan_revision=revision,
        characteristic_number='VIS-001',
        name='No visible burr',
        characteristic_type=InspectionCharacteristic.TYPE_BOOLEAN,
        expected_boolean=True,
        sequence=2,
    )
    released = release_inspection_plan_revision(revision, actor=admin)
    applicability = InspectionApplicability.objects.create(
        inspection_plan_revision=released,
        item_revision=refs['component'].item.revisions.first() if False else None,
        opc_node=node,
        process_definition=refs['process'],
        applicability_type=InspectionApplicability.TYPE_OPERATION,
        mandatory=True,
        sequence=1,
    )
    return released, numeric, boolean, applicability


def make_quality_order(code='P-P9'):
    admin, diagram, nodes, _edges, refs = released_source(code)
    plan_revision, numeric, boolean, applicability = make_released_plan(admin, refs, code=f'IP-{code}', node=nodes[1])
    order = generate_order_baseline(create_order(admin, diagram, refs), actor=admin, expected_version=0)
    order = release_production_order(order, actor=admin, expected_version=1)
    ensure_executions_for_order(order, actor=admin)
    ensure_inspections_for_order(order, actor=admin)
    return admin, order, refs, plan_revision, numeric, boolean, applicability


def start_cut(order, admin):
    material_execution, cut_execution, _output = order.operation_executions.select_related('manufacturing_operation').order_by('manufacturing_operation__sequence')
    material_execution, _event = command_execution(material_execution, actor=admin, expected_version=0, command='dispatch')
    material_execution, _event = command_execution(material_execution, actor=admin, expected_version=material_execution.execution_version, command='start')
    material_execution, _event = command_execution(material_execution, actor=admin, expected_version=material_execution.execution_version, command='record_output', produced_quantity='5.000000', accepted_quantity='5.000000')
    material_execution, _event = command_execution(material_execution, actor=admin, expected_version=material_execution.execution_version, command='complete')
    cut_execution.refresh_from_db()
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='dispatch')
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='start')
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='record_output', produced_quantity='5.000000', accepted_quantity='5.000000')
    return cut_execution


def pass_cut_quality(order, admin, numeric, boolean):
    inspection = order.inspection_executions.select_related('requirement').get(requirement__operation__label='Cut')
    inspection, _event = start_inspection(inspection, actor=admin, expected_version=0, idempotency_key='start-q')
    sample = inspection.samples.get(sample_number=1)
    inspection, _m1 = record_measurement(inspection, actor=admin, expected_version=inspection.inspection_version, sample=sample, characteristic=numeric, numeric_value='10.000000', idempotency_key='m-num')
    inspection, _m2 = record_measurement(inspection, actor=admin, expected_version=inspection.inspection_version, sample=sample, characteristic=boolean, boolean_value=True, idempotency_key='m-bool')
    inspection, _event = complete_inspection(inspection, actor=admin, expected_version=inspection.inspection_version, idempotency_key='complete-q')
    assert inspection.result == InspectionExecution.RESULT_PASS
    assert inspection.status == InspectionExecution.STATUS_COMPLETED
    return inspection


def test_inspection_definition_release_and_deterministic_measurement_evaluation():
    admin, _diagram, nodes, _edges, refs = released_source('P-P9-DEF')
    revision, numeric, boolean, _app = make_released_plan(admin, refs, code='IP-P9-DEF', node=nodes[1])

    assert revision.status == InspectionPlanRevision.STATUS_RELEASED
    assert evaluate_measurement_value(numeric, numeric_value='9.500000')['result'] == InspectionMeasurement.RESULT_PASS
    assert evaluate_measurement_value(numeric, numeric_value='10.600000')['code'] == 'above_upper_limit'
    assert evaluate_measurement_value(boolean, boolean_value=False)['result'] == InspectionMeasurement.RESULT_FAIL

    numeric.name = 'Changed after release'
    with pytest.raises(Exception):
        numeric.save()


def test_generation_snapshots_inspection_requirement_and_operation_completion_gate():
    admin, order, refs, _revision, numeric, boolean, _app = make_quality_order('P-P9-GATE')
    requirement = ProductionInspectionRequirement.objects.get(production_order=order)
    assert requirement.inspection_plan_revision.status == InspectionPlanRevision.STATUS_RELEASED
    assert requirement.sampling_policy_snapshot['characteristics'][0]['number'] == 'DIM-001'

    cut_execution = start_cut(order, admin)
    with pytest.raises(Exception):
        command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='complete')

    pass_cut_quality(order, admin, numeric, boolean)
    cut_execution.refresh_from_db()
    cut_execution, _usage = record_tooling_usage(cut_execution, actor=admin, expected_version=cut_execution.execution_version, requirement=order.tooling_requirements.get(), quantity='2.000')
    cut_execution, _ack = acknowledge_document(cut_execution, actor=admin, expected_version=cut_execution.execution_version, requirement=order.document_requirements.get())
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='complete')
    assert cut_execution.status == OperationExecution.STATUS_COMPLETED


def test_quality_holds_ncr_disposition_rework_and_reinspection():
    admin, order, _refs, _revision, numeric, _boolean, _app = make_quality_order('P-P9-NCR')
    cut_execution = start_cut(order, admin)
    inspection = order.inspection_executions.get(requirement__operation=cut_execution.manufacturing_operation)
    inspection, _event = start_inspection(inspection, actor=admin, expected_version=0)
    sample = inspection.samples.get(sample_number=1)
    inspection, bad = record_measurement(inspection, actor=admin, expected_version=inspection.inspection_version, sample=sample, characteristic=numeric, numeric_value='11.000000', idempotency_key='bad-measure')
    assert bad.nonconforming is True

    hold = place_quality_hold(actor=admin, production_order=order, inspection=inspection, operation_execution=cut_execution, scope=QualityHold.SCOPE_INSPECTION, reason='Dimensional failure', expected_version=inspection.inspection_version)
    assert hold.active is True
    release_quality_hold(hold, actor=admin, reason='Moved to NCR')

    ncr = create_ncr(actor=admin, production_order=order, operation_execution=cut_execution, inspection=inspection, measurement=bad, defect_code='DIM', defect_description='Length high', affected_quantity='1.000000')
    assert ncr.ncr_number.startswith('NCR-')
    assert ncr.status == NonconformanceRecord.STATUS_OPEN

    edge = order.precedences.get(predecessor=cut_execution.manufacturing_operation)
    edge.edge_type = 'REWORK'
    edge.rework = True
    edge.save(update_fields=['edge_type', 'rework'])
    cut_execution.rejected_quantity = Decimal('1.000000')
    cut_execution.produced_quantity = Decimal('5.000000')
    cut_execution.accepted_quantity = Decimal('4.000000')
    cut_execution.save(update_fields=['produced_quantity', 'accepted_quantity', 'rejected_quantity', 'updated_at'])
    ncr, disposition = propose_disposition(ncr, actor=admin, expected_version=0, disposition_type=QualityDisposition.TYPE_REWORK, quantity='1.000000', reason='Rework dimension', target_rework_edge=edge)
    ncr, disposition = approve_disposition(disposition, actor=admin, expected_version=ncr.ncr_version)
    ncr, disposition = implement_disposition(disposition, actor=admin, expected_version=ncr.ncr_version)
    assert disposition.status == QualityDisposition.STATUS_IMPLEMENTED
    assert disposition.rework_cycle_id
    assert QualityEvent.objects.filter(production_order=order, event_type=QualityEvent.EVENT_REWORK).exists()


def test_inspection_api_conflict_and_measurement_recording():
    admin, order, _refs, _revision, numeric, _boolean, _app = make_quality_order('P-P9-API')
    inspection = order.inspection_executions.get(requirement__operation__label='Cut')
    client = api(admin)

    stale = client.post(reverse('opc-inspection-execution-start', kwargs={'pk': inspection.pk}), {'expected_version': 99}, format='json')
    assert stale.status_code == status.HTTP_409_CONFLICT

    started = client.post(reverse('opc-inspection-execution-start', kwargs={'pk': inspection.pk}), {'expected_version': 0, 'idempotency_key': 'api-start'}, format='json')
    assert started.status_code == status.HTTP_200_OK, started.data
    sample_id = started.data['samples'][0]['id']
    measured = client.post(reverse('opc-inspection-execution-record-measurement', kwargs={'pk': inspection.pk}), {
        'expected_version': started.data['inspection_version'],
        'idempotency_key': 'api-measure',
        'sample': sample_id,
        'characteristic': str(numeric.pk),
        'numeric_value': '10.000000',
    }, format='json')
    assert measured.status_code == status.HTTP_200_OK, measured.data
    assert measured.data['measurements'][0]['evaluation_result'] == InspectionMeasurement.RESULT_PASS
