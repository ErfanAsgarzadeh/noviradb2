from __future__ import annotations

import hashlib
import json
from datetime import datetime, time
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Avg, Max, Sum
from django.utils import timezone

from auditlog.services import log_event
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.permissions import can_manage_engineering
from .inventory_services import adjust_inventory, receive_inventory
from .models import (
    AssetMeter,
    AssetMeterReading,
    CalendarException,
    InventoryBalance,
    InventoryTransaction,
    MachineAsset,
    MachineCapacity,
    MachineDowntimeEvent,
    MachineMaintenanceProfile,
    MaintenanceChecklistResult,
    MaintenanceEvent,
    MaintenanceRequest,
    MaintenanceSparePartIssue,
    MaintenanceSparePartRequirement,
    MaintenanceTaskChecklistItem,
    MaintenanceWorkOrder,
    PreventiveMaintenancePlan,
)


Q6 = Decimal('0.000001')


class MaintenanceConflict(EngineeringLifecycleError):
    def __init__(self, code, message, **details):
        super().__init__({'code': code, 'detail': message, **details})
        self.code = code
        self.details = details


def _require_manager(actor, action='manage maintenance'):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError(f'You do not have permission to {action}.')


def decimal_value(value) -> Decimal:
    return Decimal(str(value or '0')).quantize(Q6, rounding=ROUND_HALF_UP)


def _signature(scope, payload):
    cleaned = {}
    for key, value in payload.items():
        if key in {'actor', 'self'}:
            continue
        cleaned[key] = str(getattr(value, 'pk', value))
    return hashlib.sha256(json.dumps({'scope': scope, 'payload': cleaned}, sort_keys=True).encode()).hexdigest()


def _next_number(model, field, prefix):
    latest = model.objects.select_for_update().filter(**{f'{field}__startswith': prefix}).aggregate(Max(field))[f'{field}__max']
    seq = int(str(latest).split('-')[-1]) + 1 if latest else 1
    return f'{prefix}{seq:06d}'


def _check_version(obj, field, expected):
    if expected is None:
        return
    current = getattr(obj, field)
    if int(expected) != current:
        raise MaintenanceConflict(f'{field}_conflict', 'Maintenance version conflict.', expected_version=expected, current_version=current, object_id=str(obj.pk))


def _machine_calendar(machine: MachineAsset):
    cap = MachineCapacity.objects.select_related('resource_calendar').filter(machine=machine, active=True).first()
    return cap.resource_calendar if cap else None


def _asset_label(machine=None, asset=None):
    if machine:
        return machine.asset_code
    if asset:
        return asset.asset_code
    return ''


def ensure_machine_profile(machine: MachineAsset, *, criticality=MachineMaintenanceProfile._meta.get_field('criticality').default, operational_status=MachineMaintenanceProfile._meta.get_field('operational_status').default):
    profile, _ = MachineMaintenanceProfile.objects.get_or_create(machine=machine, defaults={'criticality': criticality, 'operational_status': operational_status})
    return profile


@transaction.atomic
def record_meter_reading(*, actor, meter: AssetMeter, reading_value, reading_timestamp=None, idempotency_key='', source='MANUAL', notes=''):
    _require_manager(actor, 'record meter readings')
    meter = AssetMeter.objects.select_for_update().get(pk=meter.pk)
    value = decimal_value(reading_value)
    if idempotency_key:
        existing = AssetMeterReading.objects.filter(meter=meter, idempotency_key=idempotency_key).first()
        if existing:
            if existing.reading_value != value:
                raise EngineeringLifecycleError({'idempotency_key': 'Idempotency key was reused with a different meter reading.'})
            return existing
    latest = meter.readings.order_by('-reading_timestamp', '-created_at').first()
    if latest and value < latest.reading_value and not meter.rollover_value:
        raise EngineeringLifecycleError({'reading_value': 'Meter readings cannot regress unless rollover is configured.'})
    reading = AssetMeterReading.objects.create(meter=meter, reading_value=value, reading_timestamp=reading_timestamp or timezone.now(), recorded_by=actor, source=source, idempotency_key=idempotency_key, notes=notes)
    due_plans = PreventiveMaintenancePlan.objects.filter(meter=meter, status=PreventiveMaintenancePlan.STATUS_ACTIVE)
    for plan in due_plans:
        if plan.trigger_type == PreventiveMaintenancePlan.TRIGGER_METER and plan.meter_interval:
            base = plan.next_due_meter_value or value
            if value >= base:
                plan.next_due_meter_value = decimal_value(value + plan.meter_interval)
                plan.plan_version += 1
                plan.save(update_fields=['next_due_meter_value', 'plan_version', 'updated_at'])
        elif plan.trigger_type == PreventiveMaintenancePlan.TRIGGER_THRESHOLD and plan.threshold_value and value >= plan.threshold_value:
            plan.plan_version += 1
            plan.save(update_fields=['plan_version', 'updated_at'])
    return reading


@transaction.atomic
def create_maintenance_request(*, actor, title, machine=None, maintainable_asset=None, description='', priority=MaintenanceRequest.PRIORITY_NORMAL, failure_code=None, production_order=None, operation_execution=None):
    _require_manager(actor, 'create maintenance requests')
    request = MaintenanceRequest.objects.create(
        request_number=_next_number(MaintenanceRequest, 'request_number', f'MR-{timezone.now():%Y%m%d}-'),
        machine=machine,
        maintainable_asset=maintainable_asset,
        title=title,
        description=description,
        priority=priority,
        failure_code=failure_code,
        production_order=production_order,
        operation_execution=operation_execution,
        requested_by=actor,
    )
    log_event('maintenance_request_created', target=request, category='business')
    return request


def _seed_checklist(work_order):
    if not work_order.task_template_id:
        return
    existing = set(work_order.checklist_results.values_list('sequence', flat=True))
    for item in MaintenanceTaskChecklistItem.objects.filter(template=work_order.task_template).order_by('sequence'):
        if item.sequence not in existing:
            MaintenanceChecklistResult.objects.create(work_order=work_order, template_item=item, sequence=item.sequence, label=item.label, mandatory=item.mandatory)


def _append_event(work_order, *, actor, event_type, from_status='', to_status='', idempotency_key='', notes='', metadata=None):
    if idempotency_key:
        existing = MaintenanceEvent.objects.filter(work_order=work_order, event_type=event_type, idempotency_key=idempotency_key).first()
        if existing:
            return existing
    return MaintenanceEvent.objects.create(work_order=work_order, event_type=event_type, event_timestamp=timezone.now(), actor=actor, from_status=from_status, to_status=to_status, idempotency_key=idempotency_key, notes=notes, metadata=metadata or {})


@transaction.atomic
def create_work_order(*, actor, title, machine=None, maintainable_asset=None, request=None, preventive_plan=None, task_template=None, work_order_type=MaintenanceWorkOrder.TYPE_CORRECTIVE, priority=MaintenanceRequest.PRIORITY_NORMAL, planned_start=None, planned_end=None, estimated_duration_hours=None, downtime_required=True, description='', idempotency_key=''):
    _require_manager(actor, 'create maintenance work orders')
    if request:
        request = MaintenanceRequest.objects.select_for_update().get(pk=request.pk)
        if request.status in {MaintenanceRequest.STATUS_CONVERTED, MaintenanceRequest.STATUS_CANCELLED, MaintenanceRequest.STATUS_REJECTED}:
            raise EngineeringLifecycleError({'request': 'Request cannot be converted.'})
        machine = machine or request.machine
        maintainable_asset = maintainable_asset or request.maintainable_asset
        priority = priority or request.priority
    work_order = MaintenanceWorkOrder.objects.create(
        work_order_number=_next_number(MaintenanceWorkOrder, 'work_order_number', f'MWO-{timezone.now():%Y%m%d}-'),
        work_order_type=work_order_type,
        title=title,
        description=description,
        machine=machine,
        maintainable_asset=maintainable_asset,
        request=request,
        preventive_plan=preventive_plan,
        task_template=task_template,
        priority=priority,
        planned_start=planned_start,
        planned_end=planned_end,
        estimated_duration_hours=estimated_duration_hours if estimated_duration_hours is not None else (task_template.estimated_duration_hours if task_template else 0),
        downtime_required=downtime_required,
        created_by=actor,
    )
    _seed_checklist(work_order)
    if request:
        request.status = MaintenanceRequest.STATUS_CONVERTED
        request.converted_work_order = work_order
        request.request_version += 1
        request.save(update_fields=['status', 'converted_work_order', 'request_version', 'updated_at'])
    _append_event(work_order, actor=actor, event_type=MaintenanceEvent.TYPE_CREATED, to_status=work_order.status, idempotency_key=idempotency_key, metadata={'signature': _signature('create_work_order', locals())})
    return work_order


def _sync_planned_downtime(work_order: MaintenanceWorkOrder):
    if not (work_order.machine_id and work_order.downtime_required and work_order.planned_start and work_order.planned_end):
        return None
    calendar = _machine_calendar(work_order.machine)
    if not calendar:
        return None
    exception = work_order.downtime_exception
    if exception:
        exception.start_datetime = work_order.planned_start
        exception.end_datetime = work_order.planned_end
        exception.reason = f'Maintenance {work_order.work_order_number}: {work_order.title}'
        exception.active = True
        exception.save(update_fields=['start_datetime', 'end_datetime', 'reason', 'active', 'updated_at'])
    else:
        exception = CalendarException.objects.create(resource_calendar=calendar, start_datetime=work_order.planned_start, end_datetime=work_order.planned_end, exception_type=CalendarException.TYPE_PLANNED_DOWNTIME, reason=f'Maintenance {work_order.work_order_number}: {work_order.title}', source_reference=work_order.work_order_number)
        work_order.downtime_exception = exception
        work_order.save(update_fields=['downtime_exception', 'updated_at'])
    return exception


@transaction.atomic
def transition_work_order(work_order: MaintenanceWorkOrder, *, actor, expected_version, action, idempotency_key='', notes=''):
    _require_manager(actor, 'transition maintenance work orders')
    locked = MaintenanceWorkOrder.objects.select_for_update(of=('self',)).select_related('machine', 'task_template').get(pk=work_order.pk)
    _check_version(locked, 'work_order_version', expected_version)
    old = locked.status
    now = timezone.now()
    event_type = None
    if action == 'plan' and locked.status == MaintenanceWorkOrder.STATUS_DRAFT:
        locked.status = MaintenanceWorkOrder.STATUS_PLANNED
        event_type = MaintenanceEvent.TYPE_PLANNED
        _sync_planned_downtime(locked)
    elif action == 'release' and locked.status in {MaintenanceWorkOrder.STATUS_DRAFT, MaintenanceWorkOrder.STATUS_PLANNED}:
        locked.status = MaintenanceWorkOrder.STATUS_RELEASED
        event_type = MaintenanceEvent.TYPE_RELEASED
        _sync_planned_downtime(locked)
    elif action == 'start' and locked.status == MaintenanceWorkOrder.STATUS_RELEASED:
        locked.status = MaintenanceWorkOrder.STATUS_IN_PROGRESS
        locked.actual_start = locked.actual_start or now
        event_type = MaintenanceEvent.TYPE_STARTED
        if locked.machine_id:
            profile = ensure_machine_profile(locked.machine)
            profile.operational_status = 'MAINTENANCE'
            profile.profile_version += 1
            profile.save(update_fields=['operational_status', 'profile_version', 'updated_at'])
            MachineDowntimeEvent.objects.create(machine=locked.machine, work_order=locked, downtime_type=MachineDowntimeEvent.TYPE_PLANNED if locked.work_order_type == MaintenanceWorkOrder.TYPE_PREVENTIVE else MachineDowntimeEvent.TYPE_UNPLANNED, start_datetime=now, planned=locked.work_order_type == MaintenanceWorkOrder.TYPE_PREVENTIVE, failure_code=locked.failure_code, cause_code=locked.cause_code, calendar_exception=locked.downtime_exception)
    elif action == 'pause' and locked.status == MaintenanceWorkOrder.STATUS_IN_PROGRESS:
        locked.status = MaintenanceWorkOrder.STATUS_PAUSED
        event_type = MaintenanceEvent.TYPE_PAUSED
    elif action == 'resume' and locked.status == MaintenanceWorkOrder.STATUS_PAUSED:
        locked.status = MaintenanceWorkOrder.STATUS_IN_PROGRESS
        event_type = MaintenanceEvent.TYPE_RESUMED
    elif action == 'complete' and locked.status in {MaintenanceWorkOrder.STATUS_IN_PROGRESS, MaintenanceWorkOrder.STATUS_PAUSED}:
        missing = locked.checklist_results.filter(mandatory=True, status=MaintenanceChecklistResult.STATUS_PENDING)
        if missing.exists():
            raise EngineeringLifecycleError({'checklist': 'Mandatory checklist items must be completed before work-order completion.'})
        locked.status = MaintenanceWorkOrder.STATUS_COMPLETED
        locked.actual_end = now
        locked.completed_by = actor
        event_type = MaintenanceEvent.TYPE_COMPLETED
        if locked.machine_id:
            MachineDowntimeEvent.objects.filter(machine=locked.machine, work_order=locked, end_datetime__isnull=True).update(end_datetime=now)
            profile = ensure_machine_profile(locked.machine)
            profile.operational_status = 'AVAILABLE'
            profile.profile_version += 1
            profile.save(update_fields=['operational_status', 'profile_version', 'updated_at'])
    elif action == 'close' and locked.status == MaintenanceWorkOrder.STATUS_COMPLETED:
        locked.status = MaintenanceWorkOrder.STATUS_CLOSED
        event_type = MaintenanceEvent.TYPE_CLOSED
    elif action == 'cancel' and locked.status in {MaintenanceWorkOrder.STATUS_DRAFT, MaintenanceWorkOrder.STATUS_PLANNED, MaintenanceWorkOrder.STATUS_RELEASED}:
        locked.status = MaintenanceWorkOrder.STATUS_CANCELLED
        event_type = MaintenanceEvent.TYPE_CANCELLED
        if locked.downtime_exception_id:
            CalendarException.objects.filter(pk=locked.downtime_exception_id).update(active=False)
    else:
        raise EngineeringLifecycleError({'action': 'Maintenance work-order lifecycle action is not allowed.'})
    locked.work_order_version += 1
    locked.save()
    _append_event(locked, actor=actor, event_type=event_type, from_status=old, to_status=locked.status, idempotency_key=idempotency_key, notes=notes)
    log_event('maintenance_work_order_transitioned', target=locked, category='business', extra={'action': action})
    return locked


@transaction.atomic
def record_checklist_result(result: MaintenanceChecklistResult, *, actor, status, expected_work_order_version=None, notes=''):
    _require_manager(actor, 'record maintenance checklist results')
    result = MaintenanceChecklistResult.objects.select_for_update(of=('self',)).select_related('work_order').get(pk=result.pk)
    work_order = MaintenanceWorkOrder.objects.select_for_update().get(pk=result.work_order_id)
    _check_version(work_order, 'work_order_version', expected_work_order_version)
    if work_order.status not in {MaintenanceWorkOrder.STATUS_IN_PROGRESS, MaintenanceWorkOrder.STATUS_PAUSED}:
        raise EngineeringLifecycleError({'status': 'Checklist can only be recorded during active maintenance.'})
    result.status = status
    result.notes = notes
    result.recorded_by = actor
    result.recorded_at = timezone.now()
    result.save(update_fields=['status', 'notes', 'recorded_by', 'recorded_at'])
    work_order.work_order_version += 1
    work_order.save(update_fields=['work_order_version', 'updated_at'])
    _append_event(work_order, actor=actor, event_type=MaintenanceEvent.TYPE_CHECKLIST, metadata={'result': str(result.pk), 'status': status})
    return result


@transaction.atomic
def issue_spare_part(*, actor, requirement: MaintenanceSparePartRequirement, balance: InventoryBalance, quantity, expected_balance_version=None, expected_requirement_version=None, idempotency_key='', notes=''):
    _require_manager(actor, 'issue maintenance spare parts')
    requirement = MaintenanceSparePartRequirement.objects.select_for_update(of=('self',)).select_related('work_order', 'item_revision').get(pk=requirement.pk)
    _check_version(requirement, 'requirement_version', expected_requirement_version)
    if requirement.work_order.status not in {MaintenanceWorkOrder.STATUS_RELEASED, MaintenanceWorkOrder.STATUS_IN_PROGRESS, MaintenanceWorkOrder.STATUS_PAUSED}:
        raise EngineeringLifecycleError({'status': 'Spare parts can be issued only to released or active work orders.'})
    qty = decimal_value(quantity)
    if qty <= 0 or requirement.issued_quantity + qty > requirement.quantity:
        raise EngineeringLifecycleError({'quantity': 'Issue quantity must be positive and cannot exceed the requirement.'})
    tx = adjust_inventory(actor=actor, balance=balance, quantity=qty, adjustment_type=InventoryTransaction.TYPE_ADJUSTMENT_OUT, expected_version=expected_balance_version, idempotency_key=idempotency_key, reason_code='MAINTENANCE_ISSUE', notes=f'{requirement.work_order.work_order_number}: {notes}')
    issue = MaintenanceSparePartIssue.objects.create(work_order=requirement.work_order, requirement=requirement, issue_transaction=tx, quantity=qty, issued_by=actor)
    requirement.issued_quantity = decimal_value(requirement.issued_quantity + qty)
    requirement.status = MaintenanceSparePartRequirement.STATUS_ISSUED if requirement.issued_quantity >= requirement.quantity else MaintenanceSparePartRequirement.STATUS_RESERVED
    requirement.requirement_version += 1
    requirement.save(update_fields=['issued_quantity', 'status', 'requirement_version'])
    _append_event(requirement.work_order, actor=actor, event_type=MaintenanceEvent.TYPE_PART_ISSUED, idempotency_key=idempotency_key, metadata={'issue': str(issue.pk), 'transaction': str(tx.pk)})
    return issue


@transaction.atomic
def return_spare_part(*, actor, issue: MaintenanceSparePartIssue, quantity, destination_warehouse, destination_location, stock_status='AVAILABLE', idempotency_key='', notes=''):
    _require_manager(actor, 'return unused maintenance spare parts')
    issue = MaintenanceSparePartIssue.objects.select_for_update(of=('self',)).select_related('requirement', 'work_order', 'issue_transaction').get(pk=issue.pk)
    qty = decimal_value(quantity)
    if qty <= 0 or issue.returned_quantity + qty > issue.quantity:
        raise EngineeringLifecycleError({'quantity': 'Return quantity must be positive and cannot exceed issued quantity.'})
    tx, _balance = receive_inventory(actor=actor, item_revision=issue.requirement.item_revision, quantity=qty, unit=issue.requirement.unit, warehouse=destination_warehouse, location=destination_location, stock_status=stock_status, idempotency_key=idempotency_key, reason_code='MAINTENANCE_RETURN', notes=f'{issue.work_order.work_order_number}: {notes}')
    issue.returned_quantity = decimal_value(issue.returned_quantity + qty)
    issue.return_transaction = tx
    issue.save(update_fields=['returned_quantity', 'return_transaction'])
    req = issue.requirement
    req.returned_quantity = decimal_value(req.returned_quantity + qty)
    req.status = MaintenanceSparePartRequirement.STATUS_RETURNED
    req.requirement_version += 1
    req.save(update_fields=['returned_quantity', 'status', 'requirement_version'])
    _append_event(issue.work_order, actor=actor, event_type=MaintenanceEvent.TYPE_PART_RETURNED, idempotency_key=idempotency_key, metadata={'issue': str(issue.pk), 'transaction': str(tx.pk)})
    return issue


@transaction.atomic
def generate_preventive_work_orders(*, actor, horizon_date=None):
    _require_manager(actor, 'generate preventive maintenance work orders')
    horizon_date = horizon_date or timezone.localdate()
    created = []
    plans = PreventiveMaintenancePlan.objects.select_for_update(of=('self',)).select_related('machine', 'maintainable_asset', 'task_template', 'meter').filter(status=PreventiveMaintenancePlan.STATUS_ACTIVE)
    for plan in plans:
        due = False
        if plan.trigger_type == PreventiveMaintenancePlan.TRIGGER_CALENDAR and plan.next_due_date and plan.next_due_date <= horizon_date + timedelta(days=plan.generate_horizon_days):
            due = True
        elif plan.trigger_type in {PreventiveMaintenancePlan.TRIGGER_METER, PreventiveMaintenancePlan.TRIGGER_THRESHOLD} and plan.meter_id:
            latest = plan.meter.readings.order_by('-reading_timestamp').first()
            target = plan.next_due_meter_value if plan.trigger_type == PreventiveMaintenancePlan.TRIGGER_METER else plan.threshold_value
            due = bool(latest and target is not None and latest.reading_value >= target)
        if not due:
            continue
        if MaintenanceWorkOrder.objects.filter(preventive_plan=plan, status__in=[MaintenanceWorkOrder.STATUS_DRAFT, MaintenanceWorkOrder.STATUS_PLANNED, MaintenanceWorkOrder.STATUS_RELEASED, MaintenanceWorkOrder.STATUS_IN_PROGRESS]).exists():
            continue
        start = timezone.make_aware(datetime.combine(plan.next_due_date or horizon_date, time.min)) if plan.next_due_date else None
        end = start + timedelta(hours=float(plan.task_template.estimated_duration_hours or 1)) if start else None
        wo = create_work_order(actor=actor, title=plan.name, machine=plan.machine, maintainable_asset=plan.maintainable_asset, preventive_plan=plan, task_template=plan.task_template, work_order_type=MaintenanceWorkOrder.TYPE_PREVENTIVE, priority=MaintenanceRequest.PRIORITY_NORMAL, planned_start=start, planned_end=end, estimated_duration_hours=plan.task_template.estimated_duration_hours, description=plan.task_template.description)
        created.append(wo)
        if plan.trigger_type == PreventiveMaintenancePlan.TRIGGER_CALENDAR and plan.interval_days and plan.next_due_date:
            plan.next_due_date = plan.next_due_date + timedelta(days=plan.interval_days)
        plan.plan_version += 1
        plan.save(update_fields=['next_due_date', 'plan_version', 'updated_at'])
    return created


def machine_execution_blockers(machine: MachineAsset | None):
    if not machine:
        return []
    blockers = []
    profile = getattr(machine, 'maintenance_profile', None)
    if profile and profile.operational_status in {'PLANNED_DOWN', 'BREAKDOWN', 'MAINTENANCE', 'RETIRED'}:
        blockers.append(f'Machine {machine.asset_code} is {profile.operational_status}.')
    now = timezone.now()
    active_downtime = MachineDowntimeEvent.objects.filter(machine=machine, start_datetime__lte=now).filter(end_datetime__isnull=True).exists()
    if active_downtime:
        blockers.append(f'Machine {machine.asset_code} has active downtime.')
    return blockers


def maintenance_dashboard():
    open_statuses = [MaintenanceWorkOrder.STATUS_DRAFT, MaintenanceWorkOrder.STATUS_PLANNED, MaintenanceWorkOrder.STATUS_RELEASED, MaintenanceWorkOrder.STATUS_IN_PROGRESS, MaintenanceWorkOrder.STATUS_PAUSED]
    today = timezone.localdate()
    downtime = MachineDowntimeEvent.objects.all()
    planned = downtime.filter(planned=True).count()
    unplanned = downtime.filter(planned=False).count()
    completed = MaintenanceWorkOrder.objects.filter(status__in=[MaintenanceWorkOrder.STATUS_COMPLETED, MaintenanceWorkOrder.STATUS_CLOSED])
    avg_minutes = completed.exclude(actual_start__isnull=True).exclude(actual_end__isnull=True).annotate().aggregate(avg=Avg('estimated_duration_hours'))['avg'] or Decimal('0')
    return {
        'open_requests': MaintenanceRequest.objects.filter(status__in=[MaintenanceRequest.STATUS_OPEN, MaintenanceRequest.STATUS_TRIAGED]).count(),
        'open_work_orders': MaintenanceWorkOrder.objects.filter(status__in=open_statuses).count(),
        'overdue_pm_plans': PreventiveMaintenancePlan.objects.filter(status=PreventiveMaintenancePlan.STATUS_ACTIVE, next_due_date__lt=today).count(),
        'planned_downtime_events': planned,
        'unplanned_downtime_events': unplanned,
        'mttr_hours_estimate': str(decimal_value(avg_minutes)),
        'maintenance_compliance_percent': '100.000000' if not open_statuses else str(decimal_value((completed.count() / max(1, MaintenanceWorkOrder.objects.count())) * 100)),
    }


def reliability_metrics(machine: MachineAsset, *, start=None, end=None):
    qs = MachineDowntimeEvent.objects.filter(machine=machine)
    if start:
        qs = qs.filter(start_datetime__gte=start)
    if end:
        qs = qs.filter(start_datetime__lte=end)
    events = list(qs.order_by('start_datetime'))
    repair_minutes = []
    failure_starts = []
    planned_minutes = Decimal('0')
    unplanned_minutes = Decimal('0')
    for event in events:
        if event.end_datetime:
            minutes = Decimal(str((event.end_datetime - event.start_datetime).total_seconds() / 60)).quantize(Q6)
            repair_minutes.append(minutes)
            if event.planned:
                planned_minutes += minutes
            else:
                unplanned_minutes += minutes
        if not event.planned:
            failure_starts.append(event.start_datetime)
    gaps = []
    for left, right in zip(failure_starts, failure_starts[1:]):
        gaps.append(Decimal(str((right - left).total_seconds() / 3600)).quantize(Q6))
    mtbf = sum(gaps, Decimal('0')) / Decimal(len(gaps)) if gaps else Decimal('0')
    mttr = (sum(repair_minutes, Decimal('0')) / Decimal(len(repair_minutes)) / Decimal('60')) if repair_minutes else Decimal('0')
    return {
        'machine': str(machine.pk),
        'machine_code': machine.asset_code,
        'failure_count': len(failure_starts),
        'mtbf_hours': str(mtbf.quantize(Q6)),
        'mttr_hours': str(mttr.quantize(Q6)),
        'planned_downtime_minutes': str(planned_minutes.quantize(Q6)),
        'unplanned_downtime_minutes': str(unplanned_minutes.quantize(Q6)),
    }
