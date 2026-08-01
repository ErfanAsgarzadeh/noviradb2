from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework import status

from opc.execution_services import (
    acknowledge_document,
    command_execution,
    consume_material,
    ensure_executions_for_order,
    link_genealogy,
    record_tooling_usage,
)
from opc.models import (
    ManufacturingExecutionEvent,
    ManufacturingOperationPrecedence,
    OperationExecution,
    OperationExecutionCycle,
    ProductionLot,
    ProductionOrder,
    ProductionSerial,
)
from opc.production_services import generate_order_baseline, release_production_order
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase7_production_orders import create_order, released_source


pytestmark = pytest.mark.django_db


def released_order(code='P-P8') -> ProductionOrder:
    admin, diagram, _nodes, _edges, refs = released_source(code)
    order = generate_order_baseline(create_order(admin, diagram, refs), actor=admin, expected_version=0)
    return release_production_order(order, actor=admin, expected_version=1)


def initialized_order(code='P-P8'):
    admin, diagram, _nodes, _edges, refs = released_source(code)
    order = generate_order_baseline(create_order(admin, diagram, refs), actor=admin, expected_version=0)
    order = release_production_order(order, actor=admin, expected_version=1)
    ensure_executions_for_order(order, actor=admin)
    return admin, order, refs


def complete_execution(execution: OperationExecution, actor, quantity='5.000000') -> OperationExecution:
    execution, _event = command_execution(execution, actor=actor, expected_version=execution.execution_version, command='dispatch')
    execution, _event = command_execution(execution, actor=actor, expected_version=execution.execution_version, command='start')
    execution, _event = command_execution(
        execution,
        actor=actor,
        expected_version=execution.execution_version,
        command='record_output',
        produced_quantity=quantity,
        accepted_quantity=quantity,
    )
    execution, _event = command_execution(execution, actor=actor, expected_version=execution.execution_version, command='complete')
    execution.refresh_from_db()
    return execution


def test_execution_initialization_readiness_and_predecessor_enforcement():
    admin, order, _refs = initialized_order('P-P8-INIT')

    executions = list(order.operation_executions.select_related('manufacturing_operation').order_by('manufacturing_operation__sequence'))

    assert len(executions) == order.operations.count()
    assert executions[0].status == OperationExecution.STATUS_READY
    assert executions[1].status == OperationExecution.STATUS_NOT_READY
    assert executions[1].block_reason

    with pytest.raises(Exception):
        command_execution(executions[1], actor=admin, expected_version=0, command='dispatch')

    complete_execution(executions[0], admin)
    executions[1].refresh_from_db()
    assert executions[1].status == OperationExecution.STATUS_READY


def test_execution_lifecycle_events_idempotency_material_documents_tooling_and_completion():
    admin, order, refs = initialized_order('P-P8-LIFE')
    material_execution, cut_execution, output_execution = order.operation_executions.select_related('manufacturing_operation').order_by('manufacturing_operation__sequence')
    complete_execution(material_execution, admin)
    cut_execution.refresh_from_db()

    cut_execution, first_dispatch = command_execution(
        cut_execution,
        actor=admin,
        expected_version=cut_execution.execution_version,
        command='dispatch',
        idempotency_key='dispatch-cut',
    )
    retry_execution, retry_event = command_execution(
        cut_execution,
        actor=admin,
        expected_version=0,
        command='dispatch',
        idempotency_key='dispatch-cut',
    )
    assert retry_event.pk == first_dispatch.pk
    assert retry_execution.execution_version == cut_execution.execution_version

    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='assign_machine', machine=refs['machine'])
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='start')
    cut_execution, consumption = consume_material(
        cut_execution,
        actor=admin,
        expected_version=cut_execution.execution_version,
        requirement=order.material_requirements.get(),
        quantity='2.000000',
        idempotency_key='consume-cut',
    )
    cut_execution, reversal = consume_material(
        cut_execution,
        actor=admin,
        expected_version=cut_execution.execution_version,
        requirement=order.material_requirements.get(),
        quantity='1.000000',
        reverse=True,
        idempotency_key='reverse-cut',
    )
    assert consumption.quantity == Decimal('2.000000')
    assert reversal.quantity == Decimal('-1.000000')

    cut_execution, _event = command_execution(
        cut_execution,
        actor=admin,
        expected_version=cut_execution.execution_version,
        command='record_output',
        produced_quantity='5.000000',
        accepted_quantity='4.000000',
        rejected_quantity='1.000000',
        rework_quantity='1.000000',
    )
    cut_execution, _usage = record_tooling_usage(
        cut_execution,
        actor=admin,
        expected_version=cut_execution.execution_version,
        requirement=order.tooling_requirements.get(),
        quantity='2.000',
        idempotency_key='tool-cut',
    )
    cut_execution, _ack = acknowledge_document(
        cut_execution,
        actor=admin,
        expected_version=cut_execution.execution_version,
        requirement=order.document_requirements.get(),
        idempotency_key='doc-cut',
    )
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='complete')

    assert cut_execution.status == OperationExecution.STATUS_COMPLETED
    assert list(cut_execution.events.values_list('event_sequence', flat=True)) == list(range(1, cut_execution.events.count() + 1))
    assert ManufacturingExecutionEvent.objects.filter(operation_execution=cut_execution, event_type=ManufacturingExecutionEvent.EVENT_COMPLETE).exists()


def test_serial_lot_genealogy_cycle_detection_and_rework_cycle_creation():
    admin, order, refs = initialized_order('P-P8-TRACE')
    material_execution, cut_execution, output_execution = order.operation_executions.select_related('manufacturing_operation').order_by('manufacturing_operation__sequence')
    complete_execution(material_execution, admin)
    cut_execution.refresh_from_db()
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='dispatch')
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='start')
    cut_execution, consumption = consume_material(cut_execution, actor=admin, expected_version=cut_execution.execution_version, requirement=order.material_requirements.get(), quantity='1.000000')

    parent_lot = ProductionLot.objects.create(lot_number='FG-LOT-1', item_revision=order.item_revision, production_order=order, created_by=admin)
    component_lot = ProductionLot.objects.create(lot_number='COMP-LOT-1', item_revision=refs['component'], production_order=order, created_by=admin)
    parent_serial = ProductionSerial.objects.create(serial_number='FG-SN-1', item_revision=order.item_revision, production_order=order, lot=parent_lot, created_by=admin)
    component_serial = ProductionSerial.objects.create(serial_number='COMP-SN-1', item_revision=refs['component'], production_order=order, lot=component_lot, created_by=admin)

    cut_execution, link = link_genealogy(
        cut_execution,
        actor=admin,
        expected_version=cut_execution.execution_version,
        consumption=consumption,
        parent_serial=parent_serial,
        component_serial=component_serial,
    )
    assert link.parent_serial == parent_serial
    with pytest.raises(Exception):
        link_genealogy(cut_execution, actor=admin, expected_version=cut_execution.execution_version, consumption=consumption, parent_serial=component_serial, component_serial=parent_serial)

    precedence = order.precedences.get(predecessor=cut_execution.manufacturing_operation, successor=output_execution.manufacturing_operation)
    precedence.edge_type = 'REWORK'
    precedence.rework = True
    precedence.save(update_fields=['edge_type', 'rework'])
    cut_execution, _event = command_execution(
        cut_execution,
        actor=admin,
        expected_version=cut_execution.execution_version,
        command='record_output',
        produced_quantity='1.000000',
        rejected_quantity='1.000000',
        rework_quantity='1.000000',
    )
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='rework', rework_edge=precedence, quantity='1.000000')

    assert OperationExecutionCycle.objects.filter(operation_execution=output_execution, cycle_number=2, source_rework_edge=precedence).exists()


def test_execution_api_conflict_and_actions():
    admin, order, _refs = initialized_order('P-P8-API')
    execution = order.operation_executions.order_by('manufacturing_operation__sequence').first()
    client = api(admin)

    stale = client.post(reverse('opc-operation-execution-dispatch', kwargs={'pk': execution.pk}), {'expected_version': 99}, format='json')
    assert stale.status_code == status.HTTP_409_CONFLICT

    dispatch = client.post(reverse('opc-operation-execution-dispatch', kwargs={'pk': execution.pk}), {'expected_version': 0, 'idempotency_key': 'api-dispatch'}, format='json')
    assert dispatch.status_code == status.HTTP_200_OK, dispatch.data
    assert dispatch.data['status'] == OperationExecution.STATUS_DISPATCHED
    assert 'start' in dispatch.data['allowed_actions']

    progress = client.get(reverse('opc-production-order-execution-progress', kwargs={'pk': order.pk}))
    assert progress.status_code == status.HTTP_200_OK, progress.data
    assert progress.data['operation_count'] == order.operation_executions.count()
