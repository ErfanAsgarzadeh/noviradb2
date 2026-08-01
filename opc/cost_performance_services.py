from __future__ import annotations

from decimal import Decimal

from django.db.models import Max
from django.utils import timezone

from .models import (
    CostRate,
    CostSnapshot,
    MachineDowntimeEvent,
    MaintenanceWorkOrder,
    OperationalKPISnapshot,
    ProductionOrder,
    PurchaseReceipt,
)


def decimal_value(value):
    return Decimal(str(value or '0')).quantize(Decimal('0.000001'))


def _next_number(model, field, prefix):
    latest = model.objects.aggregate(Max(field))[f'{field}__max']
    if latest and str(latest).startswith(prefix):
        try:
            number = int(str(latest).split('-')[-1]) + 1
        except ValueError:
            number = 1
    else:
        number = 1
    return f'{prefix}{number:06d}'


def _active_rates(rate_type, *, work_center=None, machine=None, at_date=None):
    at_date = at_date or timezone.localdate()
    rates = CostRate.objects.filter(rate_type=rate_type, active=True, effective_from__lte=at_date).filter(effective_to__isnull=True) | CostRate.objects.filter(rate_type=rate_type, active=True, effective_from__lte=at_date, effective_to__gte=at_date)
    if work_center:
        rates = rates.filter(work_center=work_center)
    if machine:
        rates = rates.filter(machine=machine)
    return rates.order_by('-effective_from')


def create_receipt_cost_snapshot(*, receipt: PurchaseReceipt, actor=None, policy_version='cost-rollup-v1', currency='USD'):
    material = Decimal('0')
    evidence_lines = []
    for line in receipt.lines.select_related('purchase_order_line__item_revision__item'):
        po_line = line.purchase_order_line
        line_cost = decimal_value(line.quantity * po_line.unit_price)
        material += line_cost
        evidence_lines.append({
            'receipt_line': str(line.pk),
            'po_line': str(po_line.pk),
            'item': po_line.item_revision.item.item_code,
            'quantity': str(line.quantity),
            'unit_price': str(po_line.unit_price),
            'line_cost': str(line_cost),
        })
    total = decimal_value(material)
    return CostSnapshot.objects.create(
        snapshot_number=_next_number(CostSnapshot, 'snapshot_number', f'CST-{timezone.now():%Y}-'),
        snapshot_type=CostSnapshot.TYPE_RECEIPT,
        policy_version=policy_version,
        currency=currency,
        purchase_receipt=receipt,
        material_cost=total,
        total_cost=total,
        evidence={'receipt': receipt.receipt_number, 'lines': evidence_lines},
        created_by=actor if actor and getattr(actor, 'is_authenticated', False) else None,
    )


def create_production_cost_snapshot(*, production_order: ProductionOrder, actor=None, policy_version='cost-rollup-v1', currency='USD'):
    material_cost = Decimal('0')
    labor_cost = Decimal('0')
    machine_cost = Decimal('0')
    evidence = {'operations': [], 'inventory_transactions': []}
    for tx in production_order.inventory_transactions.all():
        material_cost += Decimal('0')
        evidence['inventory_transactions'].append({'transaction': tx.transaction_number, 'quantity': str(tx.quantity), 'type': tx.transaction_type})
    for execution in production_order.operation_executions.select_related('manufacturing_operation__work_center', 'assigned_machine'):
        work_center = execution.manufacturing_operation.work_center
        labor_rate = _active_rates(CostRate.TYPE_LABOR, work_center=work_center).first()
        machine_rate = _active_rates(CostRate.TYPE_MACHINE, machine=execution.assigned_machine).first() if execution.assigned_machine_id else None
        labor = decimal_value(Decimal(execution.actual_run_seconds + execution.actual_setup_seconds) / Decimal('3600') * (labor_rate.hourly_rate if labor_rate else Decimal('0')))
        machine = decimal_value(Decimal(execution.actual_run_seconds) / Decimal('3600') * (machine_rate.hourly_rate if machine_rate else Decimal('0')))
        labor_cost += labor
        machine_cost += machine
        evidence['operations'].append({'execution': str(execution.pk), 'work_center': work_center.code, 'labor_cost': str(labor), 'machine_cost': str(machine)})
    total = decimal_value(material_cost + labor_cost + machine_cost)
    return CostSnapshot.objects.create(
        snapshot_number=_next_number(CostSnapshot, 'snapshot_number', f'CST-{timezone.now():%Y}-'),
        snapshot_type=CostSnapshot.TYPE_PRODUCTION_ORDER,
        policy_version=policy_version,
        currency=currency,
        production_order=production_order,
        material_cost=decimal_value(material_cost),
        labor_cost=decimal_value(labor_cost),
        machine_cost=decimal_value(machine_cost),
        total_cost=total,
        evidence=evidence,
        created_by=actor if actor and getattr(actor, 'is_authenticated', False) else None,
    )


def create_maintenance_cost_snapshot(*, work_order: MaintenanceWorkOrder, actor=None, policy_version='cost-rollup-v1', currency='USD'):
    labor_rate = CostRate.objects.filter(rate_type=CostRate.TYPE_LABOR, active=True).order_by('-effective_from').first()
    labor_cost = decimal_value(work_order.estimated_duration_hours * (labor_rate.hourly_rate if labor_rate else Decimal('0')))
    total = labor_cost
    return CostSnapshot.objects.create(
        snapshot_number=_next_number(CostSnapshot, 'snapshot_number', f'CST-{timezone.now():%Y}-'),
        snapshot_type=CostSnapshot.TYPE_MAINTENANCE_WORK_ORDER,
        policy_version=policy_version,
        currency=currency,
        maintenance_work_order=work_order,
        labor_cost=labor_cost,
        total_cost=total,
        evidence={'work_order': work_order.work_order_number, 'estimated_duration_hours': str(work_order.estimated_duration_hours)},
        created_by=actor if actor and getattr(actor, 'is_authenticated', False) else None,
    )


def create_project_cost_snapshot(*, project, actor=None, policy_version='cost-rollup-v1', currency='USD'):
    po_cost = Decimal('0')
    receipt_refs = []
    for receipt in project.purchase_orders.all().prefetch_related('receipts__lines__purchase_order_line'):
        for posted in receipt.receipts.all():
            for line in posted.lines.all():
                value = decimal_value(line.quantity * line.purchase_order_line.unit_price)
                po_cost += value
                receipt_refs.append({'receipt': posted.receipt_number, 'line': str(line.pk), 'value': str(value)})
    return CostSnapshot.objects.create(
        snapshot_number=_next_number(CostSnapshot, 'snapshot_number', f'CST-{timezone.now():%Y}-'),
        snapshot_type=CostSnapshot.TYPE_PROJECT,
        policy_version=policy_version,
        currency=currency,
        project=project,
        material_cost=decimal_value(po_cost),
        total_cost=decimal_value(po_cost),
        evidence={'project': str(project.pk), 'purchase_receipts': receipt_refs},
        created_by=actor if actor and getattr(actor, 'is_authenticated', False) else None,
    )


def create_operational_kpi_snapshot(*, period_start, period_end, actor=None, plant=None, work_center=None, machine=None, production_order=None):
    executions = production_order.operation_executions.all() if production_order else None
    if executions is None:
        from .models import OperationExecution
        executions = OperationExecution.objects.filter(updated_at__gte=period_start, updated_at__lte=period_end)
        if work_center:
            executions = executions.filter(manufacturing_operation__work_center=work_center)
        if machine:
            executions = executions.filter(assigned_machine=machine)
        if plant:
            executions = executions.filter(manufacturing_operation__work_center__plant=plant)
    produced = Decimal('0')
    accepted = Decimal('0')
    scrapped = Decimal('0')
    rework = Decimal('0')
    setup_seconds = 0
    run_seconds = 0
    planned = Decimal('0')
    for execution in executions:
        produced += execution.produced_quantity
        accepted += execution.accepted_quantity
        scrapped += execution.scrapped_quantity
        rework += execution.rework_quantity
        setup_seconds += execution.actual_setup_seconds
        run_seconds += execution.actual_run_seconds
        planned += execution.planned_quantity
    period_seconds = Decimal((period_end - period_start).total_seconds())
    downtime_seconds = Decimal('0')
    if machine:
        for event in MachineDowntimeEvent.objects.filter(machine=machine, start_time__lt=period_end).filter(end_time__gt=period_start):
            event_start = max(event.start_time, period_start)
            event_end = min(event.end_time, period_end) if event.end_time else period_end
            downtime_seconds += Decimal(max((event_end - event_start).total_seconds(), 0))
    available_seconds = max(period_seconds - downtime_seconds, Decimal('0'))
    availability = Decimal('100') if period_seconds == 0 else Decimal('100') * available_seconds / period_seconds
    performance = Decimal('100') if planned == 0 else Decimal('100') * produced / planned
    quality = Decimal('100') if produced == 0 else Decimal('100') * accepted / produced
    oee = availability * performance * quality / Decimal('10000')
    yield_percent = Decimal('100') if produced == 0 else Decimal('100') * accepted / produced
    scrap_percent = Decimal('0') if produced == 0 else Decimal('100') * scrapped / produced
    rework_percent = Decimal('0') if produced == 0 else Decimal('100') * rework / produced
    utilization = Decimal('0') if period_seconds == 0 else Decimal('100') * Decimal(run_seconds + setup_seconds) / period_seconds
    cycle_time = Decimal('0') if produced == 0 else Decimal(run_seconds) / produced
    return OperationalKPISnapshot.objects.create(
        snapshot_number=_next_number(OperationalKPISnapshot, 'snapshot_number', f'KPI-{timezone.now():%Y}-'),
        period_start=period_start,
        period_end=period_end,
        plant=plant,
        work_center=work_center,
        machine=machine,
        production_order=production_order,
        oee_percent=oee.quantize(Decimal('0.001')),
        availability_percent=availability.quantize(Decimal('0.001')),
        performance_percent=performance.quantize(Decimal('0.001')),
        quality_percent=quality.quantize(Decimal('0.001')),
        throughput_quantity=decimal_value(produced),
        cycle_time_seconds=cycle_time.quantize(Decimal('0.000001')),
        setup_seconds=setup_seconds,
        wait_seconds=int(downtime_seconds),
        yield_percent=yield_percent.quantize(Decimal('0.001')),
        scrap_percent=scrap_percent.quantize(Decimal('0.001')),
        rework_percent=rework_percent.quantize(Decimal('0.001')),
        schedule_adherence_percent=Decimal('100.000'),
        utilization_percent=utilization.quantize(Decimal('0.001')),
        evidence={'produced': str(decimal_value(produced)), 'accepted': str(decimal_value(accepted)), 'scrapped': str(decimal_value(scrapped)), 'run_seconds': run_seconds},
        created_by=actor if actor and getattr(actor, 'is_authenticated', False) else None,
    )
