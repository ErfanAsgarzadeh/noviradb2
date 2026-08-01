from __future__ import annotations

from datetime import date, datetime, time, timedelta
from threading import Barrier, Thread

import pytest
from django.db import close_old_connections, connection
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from opc.execution_services import command_execution, ensure_executions_for_order
from opc.models import (
    CalendarException,
    MachineCapacity,
    OperationExecution,
    ResourceCalendar,
    ScheduledOperationAssignment,
    SchedulingException,
    SchedulingPolicy,
    SchedulingRun,
    Shift,
    WorkCenterCapacity,
)
from opc.production_services import generate_order_baseline, release_production_order
from opc.scheduling_services import apply_scheduling_run, calculate_operation_demand, run_finite_schedule, transition_scheduling_run
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase7_production_orders import create_order, released_source


pytestmark = pytest.mark.django_db


def aware(value: datetime):
    return timezone.make_aware(value) if value.tzinfo is None else value


def calendar(code='P12-CAL'):
    return ResourceCalendar.objects.create(code=code, name=code, calendar_type=ResourceCalendar.TYPE_GENERAL, timezone='UTC')


def add_day_shift(cal, code='DAY', weekday=0, start=time(8, 0), end=time(16, 0)):
    return Shift.objects.create(resource_calendar=cal, code=code, name=code, weekday=weekday, local_start_time=start, local_end_time=end)


def schedulable_order(code='P-P12-SCH', quantity='2.000000'):
    admin, diagram, _nodes, _edges, refs = released_source(code)
    order = generate_order_baseline(create_order(admin, diagram, refs, quantity=quantity), actor=admin, expected_version=0)
    return admin, order, refs


def capacity_for(refs, *, suffix='A'):
    wc_cal = calendar(f'P12-WC-{suffix}')
    machine_cal = calendar(f'P12-M-{suffix}')
    for weekday in range(5):
        add_day_shift(wc_cal, code=f'W{weekday}', weekday=weekday)
        add_day_shift(machine_cal, code=f'M{weekday}', weekday=weekday)
    WorkCenterCapacity.objects.create(work_center=refs['work_center'], resource_calendar=wc_cal, parallel_capacity_units='1.000', default_efficiency_factor='1.0000')
    MachineCapacity.objects.create(machine=refs['machine'], resource_calendar=machine_cal, exclusive_capacity=True, efficiency_factor='1.0000')
    return wc_cal, machine_cal


def policy(code='P12-FWD', direction=SchedulingPolicy.DIRECTION_FORWARD, allow_overload=False):
    return SchedulingPolicy.objects.create(code=code, name=code, direction=direction, allow_overload=allow_overload)


def horizon():
    return aware(datetime(2026, 9, 14, 8, 0)), aware(datetime(2026, 9, 18, 18, 0))


def test_resource_calendar_shift_exception_and_overlap_validation():
    cal = calendar('P12-CAL-VALID')
    add_day_shift(cal, weekday=0, start=time(22, 0), end=time(6, 0))
    with pytest.raises(Exception):
        Shift.objects.create(resource_calendar=cal, code='OVERLAP', weekday=0, local_start_time=time(23, 0), local_end_time=time(2, 0))
    exc = CalendarException.objects.create(resource_calendar=cal, start_datetime=aware(datetime(2026, 9, 14, 22, 0)), end_datetime=aware(datetime(2026, 9, 15, 1, 0)), exception_type=CalendarException.TYPE_PLANNED_DOWNTIME, reason='Maintenance')
    assert exc.exception_type == CalendarException.TYPE_PLANNED_DOWNTIME


def test_duration_calculation_setup_run_efficiency_queue_move_external():
    admin, order, refs = schedulable_order('P-P12-DUR', quantity='3.000000')
    op = order.operations.get(work_center=refs['work_center'])
    op.setup_time_hours = '1.000'
    op.run_time_per_unit_hours = '0.500'
    op.queue_time_hours = '0.250'
    op.move_time_hours = '0.250'
    op.save()
    demand = calculate_operation_demand(op, efficiency=SchedulingPolicy.objects.create(code='P12-DUMMY', name='Dummy').priority_weight)
    assert demand['setup'] == 60
    assert demand['run'] == 90
    assert demand['queue'] == 15
    assert demand['move'] == 15
    assert demand['total'] == 180

    external = order.operations.exclude(pk=op.pk).first()
    external.execution_type = 'EXTERNAL'
    external.external_lead_time_days = '1.000'
    external.save()
    assert calculate_operation_demand(external)['total'] >= 1440


def test_forward_schedule_respects_precedence_machine_eligibility_and_no_double_booking():
    admin, order, refs = schedulable_order('P-P12-FWD', quantity='2.000000')
    op = order.operations.get(work_center=refs['work_center'])
    op.run_time_per_unit_hours = '4.000'
    op.save()
    capacity_for(refs, suffix='FWD')
    start, end = horizon()

    run = run_finite_schedule(actor=admin, scheduling_policy=policy('P12-FWD-POL'), horizon_start=start, horizon_end=end, scenario_name='Forward')

    assert run.status == SchedulingRun.STATUS_COMPLETED
    assignments = list(run.assignments.order_by('planned_start'))
    assert assignments
    for pred in order.precedences.filter(
        optional=False,
        rework=False,
        predecessor__node_type='OPERATION',
        successor__node_type='OPERATION',
    ):
        pred_assignment = run.assignments.get(manufacturing_operation=pred.predecessor)
        succ_assignment = run.assignments.get(manufacturing_operation=pred.successor)
        assert succ_assignment.planned_start >= pred_assignment.planned_end
    machine_assignments = [item for item in assignments if item.machine_id == refs['machine'].pk]
    for left, right in zip(machine_assignments, machine_assignments[1:]):
        assert left.planned_end <= right.planned_start
    assert run.capacity_buckets.filter(bottleneck=True).exists()


def test_backward_schedule_works_and_is_deterministic():
    admin, order, refs = schedulable_order('P-P12-BWD', quantity='2.000000')
    order.requested_completion_date = date(2026, 9, 18)
    order.save()
    capacity_for(refs, suffix='BWD')
    start, end = horizon()
    pol = policy('P12-BWD-POL', direction=SchedulingPolicy.DIRECTION_BACKWARD)

    first = run_finite_schedule(actor=admin, scheduling_policy=pol, horizon_start=start, horizon_end=end, scenario_name='Backward 1')
    second = run_finite_schedule(actor=admin, scheduling_policy=pol, horizon_start=start, horizon_end=end, scenario_name='Backward 2')

    assert first.result_checksum == second.result_checksum
    assert first.assignments.order_by('planned_end').last().planned_end <= end


def test_approval_application_updates_planned_fields_only_and_blocks_stale():
    admin, order, refs = schedulable_order('P-P12-APP', quantity='2.000000')
    capacity_for(refs, suffix='APP')
    start, end = horizon()
    run = run_finite_schedule(actor=admin, scheduling_policy=policy('P12-APP-POL'), horizon_start=start, horizon_end=end)
    approved = transition_scheduling_run(run, actor=admin, expected_version=run.run_version, action='approve')
    applied = apply_scheduling_run(approved, actor=admin, expected_version=approved.run_version, idempotency_key='apply-once')

    order.refresh_from_db()
    op = order.operations.get(work_center=refs['work_center'])
    assert applied.status == SchedulingRun.STATUS_APPLIED
    assert order.planned_start is not None and order.planned_end is not None
    assert op.planned_start is not None and op.planned_end is not None
    assert op.scheduled_machine == refs['machine']
    assert op.selected_machine == refs['machine']
    assert order.order_version == 2

    stale_run = run_finite_schedule(actor=admin, scheduling_policy=policy('P12-STALE-POL'), horizon_start=start, horizon_end=end)
    stale_approved = transition_scheduling_run(stale_run, actor=admin, expected_version=stale_run.run_version, action='approve')
    stale_order = order
    stale_order.description = 'changed after run'
    stale_order.order_version += 1
    stale_order.save(update_fields=['description', 'order_version', 'updated_at'])
    with pytest.raises(Exception):
        apply_scheduling_run(stale_approved, actor=admin, expected_version=stale_approved.run_version, idempotency_key='stale-apply')
    assert SchedulingException.objects.filter(scheduling_run=stale_approved, code='stale_source').exists()


def test_application_protects_started_execution():
    admin, order, refs = schedulable_order('P-P12-PROT', quantity='2.000000')
    capacity_for(refs, suffix='PROT')
    start, end = horizon()
    run = run_finite_schedule(actor=admin, scheduling_policy=policy('P12-PROT-POL'), horizon_start=start, horizon_end=end)
    approved = transition_scheduling_run(run, actor=admin, expected_version=run.run_version, action='approve')
    released = release_production_order(order, actor=admin, expected_version=1)
    ensure_executions_for_order(released, actor=admin)
    execution = released.operation_executions.order_by('manufacturing_operation__sequence').first()
    if execution.status == OperationExecution.STATUS_NOT_READY:
        execution.status = OperationExecution.STATUS_READY
        execution.ready = True
        execution.save(update_fields=['status', 'ready', 'updated_at'])
    command_execution(execution, actor=admin, expected_version=execution.execution_version, command='dispatch', idempotency_key='p12-dispatch')
    execution.refresh_from_db()
    command_execution(execution, actor=admin, expected_version=execution.execution_version, command='start', idempotency_key='p12-start')

    with pytest.raises(Exception):
        apply_scheduling_run(approved, actor=admin, expected_version=approved.run_version, idempotency_key='protect-apply')


def test_scheduling_api_create_approve_apply_and_conflict():
    admin, _order, refs = schedulable_order('P-P12-API', quantity='2.000000')
    capacity_for(refs, suffix='API')
    pol = policy('P12-API-POL')
    client = api(admin)
    start, end = horizon()
    created = client.post(reverse('opc-scheduling-run-list'), {'scheduling_policy': str(pol.pk), 'scenario_name': 'API', 'horizon_start': start.isoformat(), 'horizon_end': end.isoformat()}, format='json')
    assert created.status_code == status.HTTP_201_CREATED, created.data
    assert created.data['assignments']
    stale = client.post(reverse('opc-scheduling-run-approve', kwargs={'pk': created.data['id']}), {'expected_version': 99}, format='json')
    assert stale.status_code == status.HTTP_409_CONFLICT
    approved = client.post(reverse('opc-scheduling-run-approve', kwargs={'pk': created.data['id']}), {'expected_version': created.data['run_version']}, format='json')
    assert approved.status_code == status.HTTP_200_OK, approved.data
    applied = client.post(reverse('opc-scheduling-run-apply', kwargs={'pk': created.data['id']}), {'expected_version': approved.data['run_version'], 'idempotency_key': 'api-apply'}, format='json')
    assert applied.status_code == status.HTTP_200_OK, applied.data
    assert applied.data['status'] == SchedulingRun.STATUS_APPLIED


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_schedule_application_is_single_writer():
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-lock concurrency test.')
    admin, _order, refs = schedulable_order('P-P12-CONC', quantity='2.000000')
    capacity_for(refs, suffix='CONC')
    start, end = horizon()
    run = run_finite_schedule(actor=admin, scheduling_policy=policy('P12-CONC-POL'), horizon_start=start, horizon_end=end)
    approved = transition_scheduling_run(run, actor=admin, expected_version=run.run_version, action='approve')
    barrier = Barrier(2)
    results: list[str] = []

    def worker(key):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            apply_scheduling_run(SchedulingRun.objects.get(pk=approved.pk), actor=admin.__class__.objects.get(pk=admin.pk), expected_version=approved.run_version, idempotency_key=key)
            results.append('ok')
        except Exception:
            results.append('blocked')
        finally:
            close_old_connections()

    threads = [Thread(target=worker, args=(f'conc-{idx}',)) for idx in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    approved.refresh_from_db()
    assert sorted(results) == ['blocked', 'ok']
    assert approved.status == SchedulingRun.STATUS_APPLIED
    assert ScheduledOperationAssignment.objects.filter(scheduling_run=approved, status=ScheduledOperationAssignment.STATUS_APPLIED).exists()
