from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from auditlog.services import log_event
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.permissions import can_manage_engineering

from .models import (
    CalendarException,
    CapacityBucket,
    MachineAsset,
    MachineCapacity,
    ManufacturingOperation,
    ManufacturingOperationPrecedence,
    OperationExecution,
    ProductionOrder,
    ResourceCalendar,
    ScheduledOperationAssignment,
    SchedulingCalendarSnapshot,
    SchedulingException,
    SchedulingOperationSnapshot,
    SchedulingPolicy,
    SchedulingRun,
    Shift,
    WorkCenter,
    WorkCenterCapacity,
)

Q3 = Decimal('0.001')
POLICY_VERSION = 'finite-scheduling-v1'


class SchedulingConflict(EngineeringLifecycleError):
    def __init__(self, code: str, detail: str, **payload):
        super().__init__({'code': code, 'detail': detail, **payload})


def _require_manager(actor, action='manage finite scheduling'):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError(f'You do not have permission to {action}.')


def decimal_value(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Q3, rounding=ROUND_HALF_UP)


def stable_checksum(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def _signature(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, 'pk'):
        return str(value.pk)
    if isinstance(value, dict):
        return {key: _signature(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_signature(item) for item in value]
    return value


def _next_number(model, field: str, prefix: str) -> str:
    latest = model.objects.select_for_update().filter(**{f'{field}__startswith': prefix}).aggregate(Max(field))[f'{field}__max']
    next_number = int((latest or f'{prefix}000000')[-6:]) + 1
    return f'{prefix}{next_number:06d}'


def current_source_freshness() -> dict:
    return {
        'order_count': ProductionOrder.objects.count(),
        'order_version_sum': ProductionOrder.objects.aggregate(total=Sum('order_version'))['total'] or 0,
        'execution_count': OperationExecution.objects.count(),
        'execution_version_sum': OperationExecution.objects.aggregate(total=Sum('execution_version'))['total'] or 0,
        'machine_count': MachineAsset.objects.count(),
        'active_machine_count': MachineAsset.objects.filter(active=True).count(),
        'calendar_count': ResourceCalendar.objects.count(),
        'shift_count': Shift.objects.count(),
        'exception_count': CalendarException.objects.count(),
        'work_center_capacity_count': WorkCenterCapacity.objects.count(),
        'machine_capacity_count': MachineCapacity.objects.count(),
        'policy_count': SchedulingPolicy.objects.count(),
    }


def scheduling_freshness(run: SchedulingRun) -> dict:
    current = current_source_freshness()
    return {'fresh': current == run.freshness_snapshot, 'snapshot': run.freshness_snapshot, 'current': current}


def _minutes(hours) -> int:
    return int((decimal_value(hours) * Decimal('60')).to_integral_value(rounding=ROUND_HALF_UP))


def calculate_operation_demand(operation: ManufacturingOperation, *, efficiency: Decimal = Decimal('1'), policy: SchedulingPolicy | None = None) -> dict:
    if efficiency <= 0:
        raise EngineeringLifecycleError({'efficiency': 'Efficiency factor must be positive.'})
    setup = _minutes(operation.setup_time_hours)
    queue = _minutes(operation.queue_time_hours if operation.queue_time_hours else (policy.default_queue_time_hours if policy else 0))
    move = _minutes(operation.move_time_hours if operation.move_time_hours else (policy.default_move_time_hours if policy else 0))
    buffer_minutes = _minutes(operation.buffer_time_hours)
    if operation.execution_type == 'EXTERNAL':
        external = int((decimal_value(operation.external_lead_time_days) * Decimal('24') * Decimal('60')).to_integral_value(rounding=ROUND_HALF_UP))
        processing = max(external, 1)
        return {'setup': 0, 'run': processing, 'queue': queue, 'move': move, 'buffer': buffer_minutes, 'total': processing + queue + move + buffer_minutes}
    run = _minutes(operation.run_time_per_unit_hours * operation.planned_quantity)
    processing = int(((Decimal(setup + run) / efficiency).to_integral_value(rounding=ROUND_HALF_UP)))
    return {'setup': setup, 'run': max(processing - setup, 0), 'queue': queue, 'move': move, 'buffer': buffer_minutes, 'total': max(processing + queue + move + buffer_minutes, 1)}


def _aware(day, local_time: time, tz):
    naive = datetime.combine(day, local_time)
    return timezone.make_aware(naive, tz)


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def validate_shift_overlap(shift: Shift):
    tz = timezone.get_fixed_timezone(0)
    base_day = datetime(2026, 1, 5).date()
    day = base_day + timedelta(days=shift.weekday)
    start = _aware(day, shift.local_start_time, tz)
    end_day = day + timedelta(days=1) if shift.local_end_time <= shift.local_start_time else day
    end = _aware(end_day, shift.local_end_time, tz)
    for other in Shift.objects.filter(resource_calendar=shift.resource_calendar, weekday=shift.weekday, active=True).exclude(pk=shift.pk):
        other_start = _aware(day, other.local_start_time, tz)
        other_end_day = day + timedelta(days=1) if other.local_end_time <= other.local_start_time else day
        other_end = _aware(other_end_day, other.local_end_time, tz)
        if _overlaps(start, end, other_start, other_end):
            raise EngineeringLifecycleError({'shift': 'Overlapping active shifts are not allowed.'})


def _capacity_intervals(calendar: ResourceCalendar, start, end, base_units: Decimal, *, resource_type: str, resource_id: str, run: SchedulingRun | None = None) -> list[dict]:
    intervals = []
    shifts = list(calendar.shifts.filter(active=True).order_by('weekday', 'sequence', 'local_start_time'))
    exceptions = list(calendar.exceptions.filter(active=True, end_datetime__gt=start, start_datetime__lt=end).order_by('start_datetime'))
    tz = timezone.get_current_timezone()
    cursor = start.date() - timedelta(days=1)
    while cursor <= end.date():
        for shift in shifts:
            if shift.weekday != cursor.weekday():
                continue
            if shift.effective_start and cursor < shift.effective_start:
                continue
            if shift.effective_end and cursor > shift.effective_end:
                continue
            s = _aware(cursor, shift.local_start_time, tz)
            e_day = cursor + timedelta(days=1) if shift.local_end_time <= shift.local_start_time else cursor
            e = _aware(e_day, shift.local_end_time, tz)
            if e <= start or s >= end:
                continue
            interval = {'start': max(s, start), 'end': min(e, end), 'capacity': decimal_value(base_units * shift.capacity_factor), 'calendar': calendar, 'shift': shift, 'exception': None, 'status': 'AVAILABLE'}
            blocked = False
            for exc in exceptions:
                if not _overlaps(interval['start'], interval['end'], exc.start_datetime, exc.end_datetime):
                    continue
                if exc.exception_type in {CalendarException.TYPE_HOLIDAY, CalendarException.TYPE_PLANNED_DOWNTIME, CalendarException.TYPE_BLOCKED}:
                    blocked = True
                    if run:
                        SchedulingCalendarSnapshot.objects.create(scheduling_run=run, resource_type=resource_type, resource_id=resource_id, interval_start=max(interval['start'], exc.start_datetime), interval_end=min(interval['end'], exc.end_datetime), capacity_units=0, source_calendar=calendar, source_shift=shift, source_exception=exc, availability_status='BLOCKED')
                elif exc.exception_type in {CalendarException.TYPE_OVERTIME, CalendarException.TYPE_CAPACITY_INCREASE}:
                    interval['capacity'] = decimal_value(interval['capacity'] + base_units * exc.capacity_factor)
                    interval['exception'] = exc
                elif exc.exception_type == CalendarException.TYPE_CAPACITY_REDUCTION:
                    interval['capacity'] = decimal_value(interval['capacity'] * exc.capacity_factor)
                    interval['exception'] = exc
            if not blocked and interval['capacity'] > 0:
                intervals.append(interval)
                if run:
                    SchedulingCalendarSnapshot.objects.create(scheduling_run=run, resource_type=resource_type, resource_id=resource_id, interval_start=interval['start'], interval_end=interval['end'], capacity_units=interval['capacity'], source_calendar=calendar, source_shift=shift, source_exception=interval['exception'], availability_status='AVAILABLE')
        cursor += timedelta(days=1)
    return sorted(intervals, key=lambda item: (item['start'], item['end']))


@dataclass
class ResourceState:
    key: str
    intervals: list[dict]
    reservations: list[tuple]
    exclusive: bool


def _fits(interval_start, interval_end, reservations, exclusive=True):
    if not exclusive:
        return True
    return all(not _overlaps(interval_start, interval_end, start, end) for start, end, _op in reservations)


def _find_forward(state: ResourceState, earliest, duration_minutes: int):
    duration = timedelta(minutes=duration_minutes)
    for interval in state.intervals:
        cursor = max(interval['start'], earliest)
        if cursor + duration > interval['end']:
            continue
        cursor = _avoid_forward(cursor, duration, state.reservations, interval['end']) if state.exclusive else cursor
        if cursor and cursor + duration <= interval['end']:
            return cursor, cursor + duration
    return None


def _avoid_forward(cursor, duration, reservations, interval_end):
    changed = True
    while changed:
        changed = False
        for start, end, _op in sorted(reservations):
            if _overlaps(cursor, cursor + duration, start, end):
                cursor = end
                changed = True
        if cursor + duration > interval_end:
            return None
    return cursor


def _find_backward(state: ResourceState, latest, duration_minutes: int):
    duration = timedelta(minutes=duration_minutes)
    for interval in reversed(state.intervals):
        end = min(interval['end'], latest)
        start = end - duration
        if start < interval['start']:
            continue
        if state.exclusive:
            changed = True
            while changed:
                changed = False
                for res_start, res_end, _op in sorted(state.reservations, reverse=True):
                    if _overlaps(start, end, res_start, res_end):
                        end = res_start
                        start = end - duration
                        changed = True
                if start < interval['start']:
                    break
        if start >= interval['start'] and _fits(start, end, state.reservations, state.exclusive):
            return start, end
    return None


def _priority_value(priority: str) -> int:
    return {'URGENT': 0, 'HIGH': 1, 'NORMAL': 2, 'LOW': 3}.get(priority, 2)


def _eligible_machines(operation: ManufacturingOperation):
    eligible = list(operation.eligible_machines.select_related('machine_asset').filter(machine_asset__active=True).order_by('-preferred', 'sequence', 'machine_code_snapshot'))
    if operation.selected_machine_id and operation.selected_machine.active:
        selected = [item for item in eligible if item.machine_asset_id == operation.selected_machine_id]
        if selected:
            return selected
    return eligible


def _operation_queryset(horizon_start, horizon_end, plant=None, work_center=None):
    orders = ProductionOrder.objects.select_related('item_revision__item').filter(status__in=[ProductionOrder.STATUS_DRAFT, ProductionOrder.STATUS_PLANNED, ProductionOrder.STATUS_RELEASED])
    if plant:
        orders = orders.filter(operations__plant=plant).distinct()
    if work_center:
        orders = orders.filter(operations__work_center=work_center).distinct()
    return ManufacturingOperation.objects.select_related('production_order', 'work_center', 'selected_machine', 'execution').prefetch_related('eligible_machines__machine_asset', 'predecessor_links__predecessor').filter(production_order__in=orders, status=ManufacturingOperation.STATUS_PLANNED, node_type='OPERATION')


def _snapshot_operation(run, operation, duration, sequence):
    execution = getattr(operation, 'execution', None)
    fixed = bool(execution and execution.status in {OperationExecution.STATUS_RUNNING, OperationExecution.STATUS_COMPLETED, OperationExecution.STATUS_PAUSED, OperationExecution.STATUS_DISPATCHED})
    eligible_ids = [str(item.machine_asset_id) for item in _eligible_machines(operation)]
    return SchedulingOperationSnapshot.objects.create(
        scheduling_run=run,
        production_order=operation.production_order,
        manufacturing_operation=operation,
        operation_number=operation.operation_number,
        priority=operation.production_order.priority,
        required_completion=timezone.make_aware(datetime.combine(operation.production_order.requested_completion_date, time(23, 59))) if operation.production_order.requested_completion_date else None,
        current_planned_start=operation.planned_start,
        current_planned_end=operation.planned_end,
        fixed_start=fixed,
        fixed_end=fixed,
        fixed_reason='Execution already active or complete.' if fixed else '',
        source_order_version=operation.production_order.order_version,
        source_execution_version=execution.execution_version if execution else None,
        work_center=operation.work_center,
        selected_machine=operation.selected_machine,
        eligible_machine_ids=eligible_ids,
        calculated_duration_minutes=duration['total'],
        setup_duration_minutes=duration['setup'],
        run_duration_minutes=duration['run'],
        queue_duration_minutes=duration['queue'],
        move_duration_minutes=duration['move'],
        buffer_duration_minutes=duration['buffer'],
        execution_type=operation.execution_type,
        status_snapshot=execution.status if execution else operation.status,
        source_metadata={'order_number': operation.production_order.order_number, 'operation_label': operation.label, 'item_code': operation.production_order.item_revision.item.item_code},
        sequence=sequence,
    )


def _source_payload(snapshots, precedences, parameters, freshness):
    return {
        'snapshots': [
            {
                'operation': str(item.manufacturing_operation_id),
                'order': str(item.production_order_id),
                'order_version': item.source_order_version,
                'execution_version': item.source_execution_version,
                'duration': item.calculated_duration_minutes,
                'work_center': str(item.work_center_id or ''),
                'machines': item.eligible_machine_ids,
                'fixed': item.fixed_start or item.fixed_end,
            }
            for item in snapshots
        ],
        'precedence': precedences,
        'parameters': parameters,
        'freshness': freshness,
    }


@transaction.atomic
def run_finite_schedule(*, actor, scheduling_policy: SchedulingPolicy, horizon_start, horizon_end, scenario_name='Scenario', plant=None, work_center=None, direction=None, allow_overload=None) -> SchedulingRun:
    _require_manager(actor, 'run finite scheduling')
    direction = direction or scheduling_policy.direction
    run = SchedulingRun.objects.create(
        run_number=_next_number(SchedulingRun, 'run_number', f'SCH-{timezone.now():%Y%m%d}-'),
        scenario_name=scenario_name or 'Scenario',
        status=SchedulingRun.STATUS_RUNNING,
        scheduling_policy=scheduling_policy,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        cutoff_timestamp=timezone.now(),
        plant=plant,
        work_center=work_center,
        direction=direction,
        policy_version=scheduling_policy.policy_version,
        started_by=actor,
        started_at=timezone.now(),
        parameter_snapshot={'direction': direction, 'allow_overload': scheduling_policy.allow_overload if allow_overload is None else allow_overload, 'plant': str(plant.pk) if plant else None, 'work_center': str(work_center.pk) if work_center else None},
        freshness_snapshot=current_source_freshness(),
    )
    exceptions = []
    operations = list(_operation_queryset(horizon_start, horizon_end, plant, work_center).order_by('production_order__priority', 'production_order__requested_completion_date', 'production_order__order_number', 'operation_number', 'sequence'))
    wc_caps = {cap.work_center_id: cap for cap in WorkCenterCapacity.objects.select_related('resource_calendar', 'work_center').filter(active=True)}
    machine_caps = {cap.machine_id: cap for cap in MachineCapacity.objects.select_related('resource_calendar', 'machine').filter(active=True)}
    resource_states: dict[str, ResourceState] = {}
    snapshots = []
    for idx, operation in enumerate(operations, start=1):
        wc_cap = wc_caps.get(operation.work_center_id)
        machine = operation.selected_machine or (_eligible_machines(operation)[0].machine_asset if _eligible_machines(operation) else None)
        machine_cap = machine_caps.get(machine.pk) if machine else None
        efficiency = machine_cap.efficiency_factor if machine_cap else (wc_cap.default_efficiency_factor if wc_cap else Decimal('1'))
        duration = calculate_operation_demand(operation, efficiency=efficiency, policy=scheduling_policy)
        snapshots.append(_snapshot_operation(run, operation, duration, idx))
        if operation.execution_type != 'EXTERNAL':
            if not operation.work_center_id or not wc_cap:
                exceptions.append({'code': 'missing_work_center_capacity', 'severity': 'ERROR', 'operation': str(operation.pk), 'message': 'No active work-center capacity policy.'})
                SchedulingException.objects.create(scheduling_run=run, severity=SchedulingException.SEVERITY_ERROR, code='missing_work_center_capacity', production_order=operation.production_order, manufacturing_operation=operation, message='No active work-center capacity policy.', blocking=True, sequence=len(exceptions))
            else:
                key = f'WC:{operation.work_center_id}'
                if key not in resource_states:
                    intervals = _capacity_intervals(wc_cap.resource_calendar, horizon_start, horizon_end, wc_cap.parallel_capacity_units, resource_type='WORK_CENTER', resource_id=str(operation.work_center_id), run=run)
                    resource_states[key] = ResourceState(key=key, intervals=intervals, reservations=[], exclusive=wc_cap.parallel_capacity_units <= 1)
            if machine:
                if not machine_cap:
                    exceptions.append({'code': 'missing_machine_capacity', 'severity': 'WARNING', 'operation': str(operation.pk), 'message': 'No active machine capacity policy; work-center fallback will be used.'})
                    SchedulingException.objects.create(scheduling_run=run, severity=SchedulingException.SEVERITY_WARNING, code='missing_machine_capacity', production_order=operation.production_order, manufacturing_operation=operation, resource_type='MACHINE', resource_id=str(machine.pk), message='No active machine capacity policy; work-center fallback will be used.', blocking=False, sequence=len(exceptions))
                else:
                    key = f'M:{machine.pk}'
                    if key not in resource_states:
                        intervals = _capacity_intervals(machine_cap.resource_calendar, horizon_start, horizon_end, Decimal('1'), resource_type='MACHINE', resource_id=str(machine.pk), run=run)
                        resource_states[key] = ResourceState(key=key, intervals=intervals, reservations=[], exclusive=machine_cap.exclusive_capacity)
    scheduled_operation_ids = {str(operation.pk) for operation in operations}
    precedence_rows = list(ManufacturingOperationPrecedence.objects.filter(production_order__in=[op.production_order for op in operations]).values('predecessor_id', 'successor_id', 'edge_type', 'optional', 'rework'))
    predecessors = defaultdict(set)
    successors = defaultdict(set)
    for row in precedence_rows:
        if row['edge_type'] in {'OPTIONAL', 'REWORK'} or row['optional'] or row['rework']:
            continue
        predecessor_id = str(row['predecessor_id'])
        successor_id = str(row['successor_id'])
        if predecessor_id not in scheduled_operation_ids or successor_id not in scheduled_operation_ids:
            continue
        predecessors[successor_id].add(predecessor_id)
        successors[predecessor_id].add(successor_id)
    snapshot_by_operation = {str(item.manufacturing_operation_id): item for item in snapshots}
    order_key = lambda snap: (_priority_value(snap.priority), snap.required_completion or timezone.make_aware(datetime.max.replace(year=2099)), snap.source_metadata.get('order_number', ''), snap.operation_number or 999999, str(snap.manufacturing_operation_id))
    scheduled: dict[str, ScheduledOperationAssignment] = {}
    ready = deque(sorted([snap for snap in snapshots if not predecessors[str(snap.manufacturing_operation_id)]], key=order_key))
    reverse_ready = deque(sorted([snap for snap in snapshots if not successors[str(snap.manufacturing_operation_id)]], key=order_key))
    pending = set(snapshot_by_operation)
    sequence_by_resource = defaultdict(int)

    def schedule_one(snapshot, earliest=None, latest=None):
        operation = snapshot.manufacturing_operation
        if snapshot.fixed_start and operation.planned_start and operation.planned_end:
            start, end = operation.planned_start, operation.planned_end
        elif operation.execution_type == 'EXTERNAL':
            start = earliest or horizon_start
            end = start + timedelta(minutes=snapshot.calculated_duration_minutes)
        else:
            machine = operation.selected_machine or (MachineAsset.objects.filter(pk__in=snapshot.eligible_machine_ids).order_by('asset_code').first() if snapshot.eligible_machine_ids else None)
            resource_key = f'M:{machine.pk}' if machine and f'M:{machine.pk}' in resource_states else f'WC:{operation.work_center_id}'
            state = resource_states.get(resource_key)
            if not state:
                return None, resource_key, machine
            result = _find_backward(state, latest or horizon_end, snapshot.calculated_duration_minutes) if direction == SchedulingPolicy.DIRECTION_BACKWARD else _find_forward(state, earliest or horizon_start, snapshot.calculated_duration_minutes)
            if not result:
                return None, resource_key, machine
            start, end = result
            state.reservations.append((start, end, str(operation.pk)))
        machine = operation.selected_machine or (MachineAsset.objects.filter(pk__in=snapshot.eligible_machine_ids).order_by('asset_code').first() if snapshot.eligible_machine_ids else None)
        resource_key = f'M:{machine.pk}' if machine and f'M:{machine.pk}' in resource_states else f'WC:{operation.work_center_id}'
        sequence_by_resource[resource_key] += 1
        due = snapshot.required_completion
        lateness = int((end - due).total_seconds() // 60) if due and end > due else 0
        slack = int((due - end).total_seconds() // 60) if due and due > end else 0
        assignment = ScheduledOperationAssignment.objects.create(
            scheduling_run=run,
            operation_snapshot=snapshot,
            production_order=operation.production_order,
            manufacturing_operation=operation,
            planned_start=start,
            planned_end=end,
            setup_start=start if snapshot.setup_duration_minutes else None,
            setup_end=start + timedelta(minutes=snapshot.setup_duration_minutes) if snapshot.setup_duration_minutes else None,
            run_start=start + timedelta(minutes=snapshot.setup_duration_minutes),
            run_end=end,
            work_center=operation.work_center,
            machine=machine if resource_key.startswith('M:') else None,
            sequence_on_resource=sequence_by_resource[resource_key],
            dispatch_priority=sequence_by_resource[resource_key],
            lateness_minutes=lateness,
            slack_minutes=slack,
            explanation=f'{direction.lower()} finite placement using {resource_key}.',
        )
        return assignment, resource_key, machine

    if direction == SchedulingPolicy.DIRECTION_BACKWARD:
        while reverse_ready:
            snap = reverse_ready.popleft()
            if str(snap.manufacturing_operation_id) not in pending:
                continue
            latest = min([scheduled[item].planned_start for item in successors[str(snap.manufacturing_operation_id)] if item in scheduled] or [snap.required_completion or horizon_end])
            assignment, _resource_key, _machine = schedule_one(snap, latest=latest)
            if not assignment:
                exceptions.append({'code': 'capacity_shortage', 'severity': 'ERROR', 'operation': str(snap.manufacturing_operation_id), 'message': 'No finite capacity slot was available.'})
                SchedulingException.objects.create(scheduling_run=run, severity=SchedulingException.SEVERITY_ERROR, code='capacity_shortage', production_order=snap.production_order, manufacturing_operation=snap.manufacturing_operation, message='No finite capacity slot was available.', blocking=not scheduling_policy.allow_overload, sequence=len(exceptions))
            else:
                scheduled[str(snap.manufacturing_operation_id)] = assignment
            pending.discard(str(snap.manufacturing_operation_id))
            for pred in sorted(predecessors[str(snap.manufacturing_operation_id)]):
                if pred in pending and successors[pred].issubset(set(scheduled)):
                    reverse_ready.append(snapshot_by_operation[pred])
    else:
        while ready:
            snap = ready.popleft()
            if str(snap.manufacturing_operation_id) not in pending:
                continue
            earliest = max([scheduled[item].planned_end for item in predecessors[str(snap.manufacturing_operation_id)] if item in scheduled] or [horizon_start])
            assignment, _resource_key, _machine = schedule_one(snap, earliest=earliest)
            if not assignment:
                exceptions.append({'code': 'capacity_shortage', 'severity': 'ERROR', 'operation': str(snap.manufacturing_operation_id), 'message': 'No finite capacity slot was available.'})
                SchedulingException.objects.create(scheduling_run=run, severity=SchedulingException.SEVERITY_ERROR, code='capacity_shortage', production_order=snap.production_order, manufacturing_operation=snap.manufacturing_operation, message='No finite capacity slot was available.', blocking=not scheduling_policy.allow_overload, sequence=len(exceptions))
            else:
                scheduled[str(snap.manufacturing_operation_id)] = assignment
            pending.discard(str(snap.manufacturing_operation_id))
            for succ in sorted(successors[str(snap.manufacturing_operation_id)]):
                if succ in pending and predecessors[succ].issubset(set(scheduled)):
                    ready.append(snapshot_by_operation[succ])
    for unresolved in sorted(pending):
        snap = snapshot_by_operation[unresolved]
        SchedulingException.objects.create(scheduling_run=run, severity=SchedulingException.SEVERITY_ERROR, code='precedence_cycle_or_disconnected', production_order=snap.production_order, manufacturing_operation=snap.manufacturing_operation, message='Operation could not be reached through precedence traversal.', blocking=True, sequence=len(exceptions) + 1)
    _build_capacity_buckets(run)
    assignments = list(run.assignments.all())
    metrics = {
        'assignment_count': len(assignments),
        'unscheduled_operations': run.exceptions.filter(code='capacity_shortage').count(),
        'late_operations': sum(1 for item in assignments if item.lateness_minutes > 0),
        'total_tardiness_minutes': sum(max(0, item.lateness_minutes) for item in assignments),
        'makespan_minutes': int(((max([item.planned_end for item in assignments]) - min([item.planned_start for item in assignments])).total_seconds() // 60)) if assignments else 0,
        'bottlenecks': run.capacity_buckets.filter(bottleneck=True).count(),
        'overload_minutes': run.capacity_buckets.aggregate(total=Sum('overload_minutes'))['total'] or 0,
    }
    input_payload = _source_payload(snapshots, [{key: _signature(value) for key, value in row.items()} for row in precedence_rows], run.parameter_snapshot, run.freshness_snapshot)
    result_payload = [{'operation': str(item.manufacturing_operation_id), 'start': item.planned_start.isoformat(), 'end': item.planned_end.isoformat(), 'machine': str(item.machine_id or ''), 'wc': str(item.work_center_id or '')} for item in assignments]
    run.input_checksum = stable_checksum(input_payload)
    run.result_checksum = stable_checksum({'assignments': result_payload, 'exceptions': exceptions, 'metrics': metrics})
    run.error_summary = json.dumps(exceptions, sort_keys=True, default=str)
    run.metrics = metrics
    run.status = SchedulingRun.STATUS_COMPLETED
    run.completed_at = timezone.now()
    run.run_version += 1
    run.save(update_fields=['input_checksum', 'result_checksum', 'error_summary', 'metrics', 'status', 'completed_at', 'run_version'])
    return run


def _build_capacity_buckets(run: SchedulingRun):
    available = defaultdict(int)
    for interval in run.calendar_snapshots.filter(availability_status='AVAILABLE'):
        day_start = interval.interval_start.replace(hour=0, minute=0, second=0, microsecond=0)
        key = (interval.resource_type, interval.resource_id, day_start)
        minutes = int((interval.interval_end - interval.interval_start).total_seconds() // 60)
        available[key] += int(minutes * float(interval.capacity_units))
    proposed = defaultdict(int)
    affected = defaultdict(set)
    for assignment in run.assignments.select_related('production_order'):
        resources = []
        if assignment.machine_id:
            resources.append(('MACHINE', str(assignment.machine_id)))
        if assignment.work_center_id:
            resources.append(('WORK_CENTER', str(assignment.work_center_id)))
        for resource_type, resource_id in resources:
            key = (resource_type, resource_id, assignment.planned_start.replace(hour=0, minute=0, second=0, microsecond=0))
            proposed[key] += int((assignment.planned_end - assignment.planned_start).total_seconds() // 60)
            affected[key].add(assignment.production_order.order_number)
    for sequence, key in enumerate(sorted(set(available) | set(proposed)), start=1):
        resource_type, resource_id, bucket_start = key
        av = available[key]
        load = proposed[key]
        overload = max(0, load - av)
        util = Decimal('0') if av == 0 else decimal_value(Decimal(load) / Decimal(av))
        CapacityBucket.objects.create(scheduling_run=run, resource_type=resource_type, resource_id=resource_id, bucket_start=bucket_start, bucket_end=bucket_start + timedelta(days=1), available_minutes=av, proposed_load_minutes=load, utilization=util, overload_minutes=overload, idle_minutes=max(0, av - load), bottleneck=util >= Decimal('0.850') or overload > 0, affected_orders=sorted(affected[key]))


@transaction.atomic
def transition_scheduling_run(run: SchedulingRun, *, actor, expected_version, action: str, override_reason='') -> SchedulingRun:
    _require_manager(actor, 'approve scheduling runs')
    locked = SchedulingRun.objects.select_for_update().get(pk=run.pk)
    if int(expected_version) != locked.run_version:
        raise SchedulingConflict('scheduling_run_version_conflict', 'Scheduling run version conflict.', expected_version=expected_version, current_version=locked.run_version)
    if action == 'approve' and locked.status == SchedulingRun.STATUS_COMPLETED:
        blocking = locked.exceptions.filter(blocking=True).exists()
        if blocking and not override_reason:
            raise EngineeringLifecycleError({'override_reason': 'Blocking schedule exceptions require an override reason.'})
        locked.status = SchedulingRun.STATUS_APPROVED
        locked.approved_by = actor
        locked.approved_at = timezone.now()
    elif action == 'reject' and locked.status in {SchedulingRun.STATUS_COMPLETED, SchedulingRun.STATUS_APPROVED}:
        locked.status = SchedulingRun.STATUS_REJECTED
    else:
        raise EngineeringLifecycleError({'action': 'Scheduling lifecycle action is not allowed.'})
    locked.run_version += 1
    locked.save()
    log_event('scheduling_run_transitioned', target=locked, category='business', extra={'action': action})
    return locked


def _idempotent_apply(run: SchedulingRun, key: str):
    if key and run.status == SchedulingRun.STATUS_APPLIED and run.application_idempotency_key == key:
        return run
    if run.status == SchedulingRun.STATUS_APPLIED:
        raise EngineeringLifecycleError({'status': 'Scheduling run has already been applied.'})
    return None


def apply_scheduling_run(run: SchedulingRun, *, actor, expected_version, idempotency_key='') -> SchedulingRun:
    _require_manager(actor, 'apply scheduling runs')
    stale_run_id = None
    stale_sequence = 1
    with transaction.atomic():
        locked = SchedulingRun.objects.select_for_update().get(pk=run.pk)
        if _idempotent_apply(locked, idempotency_key):
            return locked
        if int(expected_version) != locked.run_version:
            raise SchedulingConflict('scheduling_run_version_conflict', 'Scheduling run version conflict.', expected_version=expected_version, current_version=locked.run_version)
        if locked.status != SchedulingRun.STATUS_APPROVED:
            raise EngineeringLifecycleError({'status': 'Only approved scheduling runs can be applied.'})
        if not scheduling_freshness(locked)['fresh']:
            stale_run_id = locked.pk
            stale_sequence = locked.exceptions.count() + 1
        else:
            assignments = list(locked.assignments.select_related('manufacturing_operation', 'production_order', 'operation_snapshot', 'machine').order_by('planned_start', 'sequence_on_resource'))
            orders = {item.production_order_id: item.production_order for item in assignments}
            for assignment in assignments:
                operation = ManufacturingOperation.objects.select_for_update().get(pk=assignment.manufacturing_operation_id)
                execution = getattr(operation, 'execution', None)
                if execution and execution.status in {OperationExecution.STATUS_RUNNING, OperationExecution.STATUS_COMPLETED, OperationExecution.STATUS_PAUSED}:
                    raise EngineeringLifecycleError({'operation': f'Operation {operation.pk} is active or complete and cannot be rescheduled.'})
                if operation.production_order.order_version != assignment.operation_snapshot.source_order_version:
                    raise SchedulingConflict('source_order_version_conflict', 'Production order changed after scheduling run.', production_order=str(operation.production_order_id))
                operation.planned_start = assignment.planned_start
                operation.planned_end = assignment.planned_end
                operation.scheduled_machine = assignment.machine
                operation.scheduling_dispatch_priority = assignment.dispatch_priority
                operation.applied_scheduling_run = locked
                operation.save(update_fields=['planned_start', 'planned_end', 'scheduled_machine', 'scheduling_dispatch_priority', 'applied_scheduling_run', 'updated_at'])
                if execution and execution.status in {OperationExecution.STATUS_READY, OperationExecution.STATUS_NOT_READY, OperationExecution.STATUS_DISPATCHED}:
                    execution.assigned_machine = assignment.machine or execution.assigned_machine
                    execution.dispatch_priority = assignment.dispatch_priority
                    execution.save(update_fields=['assigned_machine', 'dispatch_priority', 'updated_at'])
                assignment.status = ScheduledOperationAssignment.STATUS_APPLIED
                assignment.save(update_fields=['status'])
            for order in orders.values():
                ops = list(order.operations.all())
                starts = [op.planned_start for op in ops if op.planned_start]
                ends = [op.planned_end for op in ops if op.planned_end]
                order.planned_start = min(starts) if starts else order.planned_start
                order.planned_end = max(ends) if ends else order.planned_end
                order.order_version += 1
                order.save(update_fields=['planned_start', 'planned_end', 'order_version', 'updated_at'])
            locked.status = SchedulingRun.STATUS_APPLIED
            locked.applied_by = actor
            locked.applied_at = timezone.now()
            locked.application_idempotency_key = idempotency_key or ''
            locked.run_version += 1
            locked.save()
            log_event('scheduling_run_applied', target=locked, category='business')
            return locked
    if stale_run_id:
        SchedulingException.objects.create(scheduling_run_id=stale_run_id, severity=SchedulingException.SEVERITY_ERROR, code='stale_source', message='Scheduling source data changed after the run.', blocking=True, sequence=stale_sequence)
    raise EngineeringLifecycleError({'freshness': 'Stale scheduling runs cannot be applied.'})
