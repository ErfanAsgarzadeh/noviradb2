from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from uuid import UUID

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.models import BOM, BOMLine, BOMRevision, ItemRevision
from enterprise_items.permissions import can_manage_engineering

from .models import (
    InventoryBalance,
    ItemPlanningPolicy,
    MRPDemandSnapshot,
    MRPPegging,
    MRPRecommendation,
    MRPRequirement,
    MRPRun,
    MRPSupplySnapshot,
    OPCDiagram,
    PlanningCalendar,
    PlanningDemand,
    ProductionOrder,
    PurchaseRequisition,
    ScheduledSupply,
    Warehouse,
)
from .production_services import generate_order_number

Q6 = Decimal('0.000001')
MRP_POLICY_VERSION = 'mrp-policy-v1'


class MRPConflict(EngineeringLifecycleError):
    def __init__(self, code: str, detail: str, **payload):
        super().__init__({'code': code, 'detail': detail, **payload})


def decimal_value(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Q6, rounding=ROUND_HALF_UP)


def _require_planner(actor):
    if not getattr(actor, 'is_authenticated', False):
        raise EngineeringPermissionError('Authentication is required for MRP planning.')


def _require_manager(actor, action='manage MRP planning'):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError(f'You do not have permission to {action}.')


def stable_checksum(payload) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


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


def _next_number(model, field: str, prefix: str) -> str:
    latest = model.objects.select_for_update().filter(**{f'{field}__startswith': prefix}).aggregate(Max(field))[f'{field}__max']
    next_number = int((latest or f'{prefix}000000')[-6:]) + 1
    return f'{prefix}{next_number:06d}'


def next_working_day(value: date, calendar: PlanningCalendar | None) -> date:
    if not calendar:
        return value
    holidays = set(calendar.holidays or [])
    weekdays = set(calendar.working_weekdays or [0, 1, 2, 3, 4])
    cursor = value
    while cursor.weekday() not in weekdays or cursor.isoformat() in holidays:
        cursor += timedelta(days=1)
    return cursor


def subtract_planning_days(value: date, days: int, calendar: PlanningCalendar | None) -> date:
    if days <= 0:
        return next_working_day(value, calendar)
    cursor = value
    remaining = days
    weekdays = set((calendar.working_weekdays if calendar else [0, 1, 2, 3, 4]) or [0, 1, 2, 3, 4])
    holidays = set((calendar.holidays if calendar else []) or [])
    while remaining:
        cursor -= timedelta(days=1)
        if cursor.weekday() in weekdays and cursor.isoformat() not in holidays:
            remaining -= 1
    return cursor


def total_lead_days(policy: ItemPlanningPolicy | None, quantity: Decimal) -> int:
    if not policy:
        return 0
    variable = Decimal('0')
    if policy.variable_lead_days_per_unit:
        variable = (policy.variable_lead_days_per_unit * quantity).to_integral_value(rounding=ROUND_CEILING)
    return int(policy.pre_processing_lead_days + policy.supply_lead_days + policy.post_processing_lead_days + policy.safety_lead_days + variable)


def lot_size(quantity: Decimal, policy: ItemPlanningPolicy | None) -> Decimal:
    qty = decimal_value(quantity)
    if not policy:
        return qty
    if policy.lot_sizing_method == ItemPlanningPolicy.LOT_FIXED and policy.fixed_lot_quantity:
        qty = decimal_value(policy.fixed_lot_quantity)
    else:
        if policy.minimum_order_quantity and qty < policy.minimum_order_quantity:
            qty = decimal_value(policy.minimum_order_quantity)
        if policy.lot_sizing_method == ItemPlanningPolicy.LOT_MULTIPLE and policy.order_multiple:
            multiple = decimal_value(policy.order_multiple)
            qty = (qty / multiple).to_integral_value(rounding=ROUND_CEILING) * multiple
    if policy.maximum_order_quantity and qty > policy.maximum_order_quantity:
        qty = decimal_value(policy.maximum_order_quantity)
    return decimal_value(qty)


@transaction.atomic
def create_demand(*, actor, **attrs) -> PlanningDemand:
    _require_planner(actor)
    demand = PlanningDemand(**attrs)
    demand.created_by = actor
    demand.demand_number = _next_number(PlanningDemand, 'demand_number', f'PD-{timezone.now():%Y}-')
    demand.save()
    return demand


@transaction.atomic
def update_demand(demand: PlanningDemand, *, actor, expected_version, **attrs) -> PlanningDemand:
    _require_planner(actor)
    demand = PlanningDemand.objects.select_for_update().get(pk=demand.pk)
    if int(expected_version) != demand.demand_version:
        raise MRPConflict('demand_version_conflict', 'Planning demand version conflict.', expected_version=expected_version, current_version=demand.demand_version)
    if demand.status != PlanningDemand.STATUS_DRAFT:
        raise EngineeringLifecycleError({'status': 'Only draft demand can be edited.'})
    for blocked in ('status', 'demand_number', 'approved_by', 'approved_at', 'cancelled_by', 'cancelled_at'):
        attrs.pop(blocked, None)
    for key, value in attrs.items():
        setattr(demand, key, value)
    demand.demand_version += 1
    demand.save()
    return demand


@transaction.atomic
def transition_demand(demand: PlanningDemand, *, actor, expected_version, action: str) -> PlanningDemand:
    _require_manager(actor, 'approve or cancel planning demand')
    demand = PlanningDemand.objects.select_for_update().get(pk=demand.pk)
    if int(expected_version) != demand.demand_version:
        raise MRPConflict('demand_version_conflict', 'Planning demand version conflict.', expected_version=expected_version, current_version=demand.demand_version)
    if action == 'approve' and demand.status == PlanningDemand.STATUS_DRAFT:
        demand.status = PlanningDemand.STATUS_APPROVED
        demand.approved_by = actor
        demand.approved_at = timezone.now()
    elif action == 'cancel' and demand.status in {PlanningDemand.STATUS_DRAFT, PlanningDemand.STATUS_APPROVED}:
        demand.status = PlanningDemand.STATUS_CANCELLED
        demand.cancelled_by = actor
        demand.cancelled_at = timezone.now()
    else:
        raise EngineeringLifecycleError({'action': 'Demand lifecycle action is not allowed.'})
    demand.demand_version += 1
    demand.save()
    return demand


def selected_bom_revision(item_revision, required_date: date, *, exceptions: list[dict]) -> BOMRevision | None:
    candidates = BOMRevision.objects.select_related('bom').filter(
        bom__parent_item_revision=item_revision,
        bom__bom_type=BOM.TYPE_MANUFACTURING,
        status=BOMRevision.STATUS_RELEASED,
        is_active=True,
    )
    candidates = [rev for rev in candidates if (not rev.effective_from or rev.effective_from <= required_date) and (not rev.effective_to or rev.effective_to >= required_date)]
    candidates = sorted(candidates, key=lambda rev: (rev.effective_from or date.min, rev.revision, str(rev.pk)), reverse=True)
    if not candidates:
        exceptions.append({'code': 'missing_bom', 'severity': 'ERROR', 'item_revision': str(item_revision.pk), 'message': 'No released manufacturing BOM revision is available.'})
        return None
    if len(candidates) > 1 and candidates[0].effective_from == candidates[1].effective_from:
        exceptions.append({'code': 'ambiguous_bom', 'severity': 'ERROR', 'item_revision': str(item_revision.pk), 'message': 'Multiple released manufacturing BOM revisions match the requirement date.'})
    return candidates[0]


def explode_requirements(seed_rows: list[dict], policies: dict, exceptions: list[dict]) -> list[dict]:
    rows = []
    stack = sorted(seed_rows, key=lambda row: (row['required_date'], row['item_code'], row['path_key']))
    sequence = 1
    while stack:
        row = stack.pop(0)
        row['sequence'] = sequence
        sequence += 1
        rows.append(row)
        policy = policies.get(str(row['item_revision_id']))
        if policy and policy.procurement_type == ItemPlanningPolicy.PROCUREMENT_BUY:
            continue
        bom = selected_bom_revision(row['item_revision'], row['required_date'], exceptions=exceptions)
        if not bom:
            continue
        if str(row['item_revision_id']) in row['visited']:
            exceptions.append({'code': 'bom_cycle', 'severity': 'ERROR', 'item_revision': str(row['item_revision_id']), 'message': 'BOM cycle detected during MRP explosion.'})
            continue
        lines = BOMLine.objects.select_related('component_item_revision__item').filter(bom_revision=bom, is_optional=False).order_by('sequence', 'component_item_revision__item__item_code', 'id')
        for line in lines:
            scrap_factor = Decimal('1') + (decimal_value(line.scrap_percent) / Decimal('100'))
            child_qty = decimal_value(row['quantity'] * line.quantity * scrap_factor)
            child_policy = policies.get(str(line.component_item_revision_id))
            child_phantom = line.is_phantom or (child_policy and child_policy.procurement_type == ItemPlanningPolicy.PROCUREMENT_PHANTOM)
            child_path = [*row['bom_path'], {'bom_revision': str(bom.pk), 'line': str(line.pk), 'component': str(line.component_item_revision_id), 'phantom': child_phantom}]
            stack.append({
                'source_type': 'DEPENDENT',
                'item_revision': line.component_item_revision,
                'item_revision_id': line.component_item_revision_id,
                'item_code': line.component_item_revision.item.item_code,
                'quantity': child_qty,
                'unit': line.unit,
                'required_date': row['required_date'],
                'warehouse': row['warehouse'],
                'warehouse_id': row['warehouse_id'],
                'parent_row': row,
                'demand_snapshot': row.get('demand_snapshot'),
                'bom_revision': bom,
                'bom_path': child_path,
                'path_key': f"{row['path_key']}/{line.sequence}:{line.component_item_revision.item.item_code}",
                'visited': {*row['visited'], str(row['item_revision_id'])},
                'phantom': child_phantom,
            })
        stack = sorted(stack, key=lambda item: (item['required_date'], item['item_code'], item['path_key']))
    return rows


def collect_policies(item_ids=None):
    queryset = ItemPlanningPolicy.objects.select_related('item_revision__item', 'default_warehouse').filter(active=True)
    if item_ids:
        queryset = queryset.filter(item_revision_id__in=item_ids)
    return {str(policy.item_revision_id): policy for policy in queryset}


def current_source_freshness() -> dict:
    return {
        'demand_count': PlanningDemand.objects.count(),
        'demand_version_sum': PlanningDemand.objects.aggregate(total=Sum('demand_version'))['total'] or 0,
        'supply_count': ScheduledSupply.objects.count(),
        'supply_version_sum': ScheduledSupply.objects.aggregate(total=Sum('supply_version'))['total'] or 0,
        'inventory_version_sum': InventoryBalance.objects.aggregate(total=Sum('balance_version'))['total'] or 0,
        'production_order_version_sum': ProductionOrder.objects.aggregate(total=Sum('order_version'))['total'] or 0,
    }


@transaction.atomic
def run_mrp(*, actor, horizon_start: date, horizon_end: date, warehouse: Warehouse | None = None, plant=None, planning_calendar: PlanningCalendar | None = None) -> MRPRun:
    _require_manager(actor, 'run MRP')
    run = MRPRun.objects.create(
        run_number=_next_number(MRPRun, 'run_number', f'MRP-{timezone.now():%Y%m%d}-'),
        status=MRPRun.STATUS_RUNNING,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        cutoff_timestamp=timezone.now(),
        planning_calendar=planning_calendar,
        warehouse=warehouse,
        plant=plant,
        policy_version=MRP_POLICY_VERSION,
        started_by=actor,
        started_at=timezone.now(),
        parameter_snapshot={'horizon_start': horizon_start.isoformat(), 'horizon_end': horizon_end.isoformat(), 'warehouse': str(warehouse.pk) if warehouse else None, 'plant': str(plant.pk) if plant else None},
        freshness_snapshot=current_source_freshness(),
    )
    exceptions: list[dict] = []
    demands = PlanningDemand.objects.select_related('item_revision__item', 'warehouse').filter(status=PlanningDemand.STATUS_APPROVED, required_date__gte=horizon_start, required_date__lte=horizon_end)
    if warehouse:
        demands = demands.filter(warehouse=warehouse)
    if plant:
        demands = demands.filter(plant=plant)
    policies = collect_policies()
    demand_payload = []
    seed_rows = []
    for sequence, demand in enumerate(demands.order_by('required_date', 'priority', 'item_revision__item__item_code', 'demand_number'), start=1):
        snapshot = MRPDemandSnapshot.objects.create(mrp_run=run, source_demand=demand, item_revision=demand.item_revision, warehouse=demand.warehouse, quantity=demand.quantity, unit=demand.unit, required_date=demand.required_date, priority=demand.priority, source_status=demand.status, source_version=demand.demand_version, source_metadata={'demand_number': demand.demand_number, 'demand_type': demand.demand_type}, sequence=sequence)
        demand_payload.append({'id': str(demand.pk), 'version': demand.demand_version, 'quantity': str(demand.quantity), 'date': demand.required_date.isoformat()})
        seed_rows.append({'source_type': 'INDEPENDENT', 'item_revision': demand.item_revision, 'item_revision_id': demand.item_revision_id, 'item_code': demand.item_revision.item.item_code, 'quantity': decimal_value(demand.quantity), 'unit': demand.unit, 'required_date': demand.required_date, 'warehouse': demand.warehouse or warehouse, 'warehouse_id': (demand.warehouse_id or (warehouse.pk if warehouse else None)), 'parent_row': None, 'demand_snapshot': snapshot, 'bom_revision': None, 'bom_path': [], 'path_key': f'D:{sequence}', 'visited': set(), 'phantom': False})
    inventory_payload = []
    inventory = InventoryBalance.objects.select_related('item_revision__item', 'warehouse').filter(stock_status=InventoryBalance.STATUS_AVAILABLE)
    if warehouse:
        inventory = inventory.filter(warehouse=warehouse)
    for sequence, balance in enumerate(inventory.order_by('item_revision__item__item_code', 'warehouse__code', 'location__code', 'id'), start=1):
        qty = decimal_value(balance.available_quantity)
        if qty <= 0:
            continue
        MRPSupplySnapshot.objects.create(mrp_run=run, source_type='INVENTORY', source_id=str(balance.pk), item_revision=balance.item_revision, warehouse=balance.warehouse, quantity=qty, unit=balance.unit, available_date=horizon_start, firm=True, source_status=balance.stock_status, source_version=balance.balance_version, source_metadata={'location': str(balance.location_id)}, sequence=sequence)
        inventory_payload.append({'id': str(balance.pk), 'version': balance.balance_version, 'qty': str(qty)})
    supplies = ScheduledSupply.objects.select_related('item_revision__item', 'warehouse').filter(status=ScheduledSupply.STATUS_APPROVED, expected_date__lte=horizon_end)
    if warehouse:
        supplies = supplies.filter(warehouse=warehouse)
    offset = len(inventory_payload)
    for idx, supply in enumerate(supplies.order_by('expected_date', 'item_revision__item__item_code', 'supply_number'), start=1):
        MRPSupplySnapshot.objects.create(mrp_run=run, source_type='SCHEDULED_SUPPLY', source_id=str(supply.pk), item_revision=supply.item_revision, warehouse=supply.warehouse, quantity=supply.quantity, unit=supply.unit, available_date=supply.expected_date, firm=supply.firm, source_status=supply.status, source_version=supply.supply_version, source_metadata={'supply_number': supply.supply_number, 'supply_type': supply.supply_type}, sequence=offset + idx)
    prod_supply = ProductionOrder.objects.select_related('item_revision__item').filter(status__in=[ProductionOrder.STATUS_PLANNED, ProductionOrder.STATUS_RELEASED], requested_completion_date__isnull=False, requested_completion_date__lte=horizon_end)
    for idx, order in enumerate(prod_supply.order_by('requested_completion_date', 'item_revision__item__item_code', 'order_number'), start=1):
        MRPSupplySnapshot.objects.create(mrp_run=run, source_type='PRODUCTION_ORDER', source_id=str(order.pk), item_revision=order.item_revision, warehouse=warehouse, quantity=order.planned_quantity, unit=order.unit, available_date=order.requested_completion_date, firm=order.status == ProductionOrder.STATUS_RELEASED, source_status=order.status, source_version=order.order_version, source_metadata={'order_number': order.order_number}, sequence=offset + len(supplies) + idx)
    rows = explode_requirements(seed_rows, policies, exceptions)
    requirements_by_key = defaultdict(Decimal)
    row_refs = defaultdict(list)
    for row in rows:
        if row.get('phantom'):
            continue
        key = (str(row['item_revision_id']), str(row['warehouse_id'] or ''), row['required_date'])
        requirements_by_key[key] += row['quantity']
        row_refs[key].append(row)
    supply_by_key = defaultdict(Decimal)
    for supply in run.supply_snapshots.all():
        key = (str(supply.item_revision_id), str(supply.warehouse_id or ''), supply.available_date)
        supply_by_key[key] += supply.quantity
    result_payload = []
    sequence = 1
    rec_sequence = 1
    projected_by_item_warehouse = defaultdict(Decimal)
    for key in sorted(set(requirements_by_key) | set(supply_by_key), key=lambda item: (item[2], item[0], item[1])):
        item_id, warehouse_id, bucket = key
        gross = decimal_value(requirements_by_key[key])
        scheduled = decimal_value(supply_by_key[key])
        policy = policies.get(item_id)
        safety = decimal_value((policy.safety_stock if policy else 0) + (policy.minimum_stock if policy else 0))
        projection_key = (item_id, warehouse_id)
        projected = decimal_value(projected_by_item_warehouse[projection_key] + scheduled - gross)
        net = decimal_value(max(Decimal('0'), safety - projected))
        receipt = lot_size(net, policy) if net > 0 else Decimal('0.000000')
        projected_by_item_warehouse[projection_key] = decimal_value(projected + receipt)
        release_date = subtract_planning_days(bucket, total_lead_days(policy, receipt), planning_calendar) if receipt > 0 else None
        item_revision = ItemRevision.objects.select_related('item').get(pk=item_id)
        requirement = MRPRequirement.objects.create(mrp_run=run, item_revision=item_revision, warehouse_id=warehouse_id or None, bucket_date=bucket, gross_requirement=gross, scheduled_receipt=scheduled, projected_available=decimal_value(projected), safety_stock=safety, net_requirement=net, planned_receipt=receipt, planned_release=receipt, planned_release_date=release_date, procurement_type=policy.procurement_type if policy else '', demand_snapshot=row_refs[key][0].get('demand_snapshot') if row_refs[key] else None, bom_path=row_refs[key][0].get('bom_path', []) if row_refs[key] else [], exception_codes=[] if policy else ['missing_policy'], sequence=sequence)
        result_payload.append({'item': item_id, 'date': bucket.isoformat(), 'gross': str(gross), 'scheduled': str(scheduled), 'net': str(net), 'planned': str(receipt)})
        if not policy:
            exceptions.append({'code': 'missing_policy', 'severity': 'ERROR', 'item_revision': item_id, 'message': 'No active planning policy exists.'})
        if receipt > 0 and policy:
            rec_type = MRPRecommendation.TYPE_PRODUCTION if policy.procurement_type == ItemPlanningPolicy.PROCUREMENT_MAKE else MRPRecommendation.TYPE_PURCHASE_REQUISITION if policy.procurement_type == ItemPlanningPolicy.PROCUREMENT_BUY else MRPRecommendation.TYPE_TRANSFER
            rec = MRPRecommendation.objects.create(mrp_run=run, recommendation_number=f'{run.run_number}-REC-{rec_sequence:04d}', recommendation_type=rec_type, item_revision=item_revision, warehouse_id=warehouse_id or None, quantity=receipt, unit=requirement.item_revision.item.base_unit or 'EA', required_date=bucket, planned_receipt_date=bucket, planned_release_date=release_date or bucket, procurement_type=policy.procurement_type, source_requirement=requirement, shortage_quantity=net, priority=PlanningDemand.PRIORITY_NORMAL, explanation='Net requirement after inventory, scheduled supply, and safety stock netting.', exception_codes=['lead_time_past_due'] if release_date and release_date < horizon_start else [], input_checksum='', status=MRPRecommendation.STATUS_PLANNED)
            for peg_sequence, source in enumerate(row_refs[key], start=1):
                MRPPegging.objects.create(mrp_run=run, recommendation=rec, requirement=requirement, demand_snapshot=source.get('demand_snapshot'), parent_requirement=None, bom_path=source.get('bom_path', []), quantity=source['quantity'], sequence=peg_sequence)
            rec_sequence += 1
        sequence += 1
    input_payload = {'demands': demand_payload, 'inventory': inventory_payload, 'parameters': run.parameter_snapshot, 'freshness': run.freshness_snapshot}
    run.input_checksum = stable_checksum(input_payload)
    run.result_checksum = stable_checksum({'requirements': result_payload, 'exceptions': exceptions})
    run.error_summary = json.dumps(exceptions, sort_keys=True, default=str)
    run.status = MRPRun.STATUS_COMPLETED
    run.completed_at = timezone.now()
    run.run_version += 1
    run.save(update_fields=['input_checksum', 'result_checksum', 'error_summary', 'status', 'completed_at', 'run_version'])
    MRPRecommendation.objects.filter(mrp_run=run).update(input_checksum=run.input_checksum)
    return run


def recommendation_freshness(recommendation: MRPRecommendation) -> dict:
    current = current_source_freshness()
    stale = current != recommendation.mrp_run.freshness_snapshot
    return {'fresh': not stale, 'snapshot': recommendation.mrp_run.freshness_snapshot, 'current': current}


@transaction.atomic
def transition_recommendation(recommendation: MRPRecommendation, *, actor, expected_version, action: str) -> MRPRecommendation:
    _require_manager(actor, 'review MRP recommendations')
    rec = MRPRecommendation.objects.select_for_update(of=('self',)).select_related('mrp_run').get(pk=recommendation.pk)
    if int(expected_version) != rec.recommendation_version:
        raise MRPConflict('recommendation_version_conflict', 'MRP recommendation version conflict.', expected_version=expected_version, current_version=rec.recommendation_version)
    if action == 'review' and rec.status == MRPRecommendation.STATUS_PLANNED:
        rec.status = MRPRecommendation.STATUS_REVIEWED
        rec.reviewed_by = actor
        rec.reviewed_at = timezone.now()
    elif action == 'approve' and rec.status in {MRPRecommendation.STATUS_PLANNED, MRPRecommendation.STATUS_REVIEWED}:
        if not recommendation_freshness(rec)['fresh']:
            rec.status = MRPRecommendation.STATUS_STALE
            rec.recommendation_version += 1
            rec.save(update_fields=['status', 'recommendation_version'])
            raise EngineeringLifecycleError({'freshness': 'Recommendation is stale and cannot be approved.'})
        rec.status = MRPRecommendation.STATUS_APPROVED
        rec.approved_by = actor
        rec.approved_at = timezone.now()
    elif action == 'reject' and rec.status in {MRPRecommendation.STATUS_PLANNED, MRPRecommendation.STATUS_REVIEWED, MRPRecommendation.STATUS_APPROVED}:
        rec.status = MRPRecommendation.STATUS_REJECTED
    else:
        raise EngineeringLifecycleError({'action': 'Recommendation lifecycle action is not allowed.'})
    rec.recommendation_version += 1
    rec.save()
    return rec


def _idempotent_conversion(rec: MRPRecommendation, key: str, signature: dict):
    if not key:
        return None
    existing = rec.converted_production_order or rec.converted_purchase_requisition
    if existing and rec.exception_codes and rec.exception_codes[-1] == f'idempotency:{key}:{stable_checksum(signature)}':
        return existing
    if existing:
        raise EngineeringLifecycleError({'idempotency_key': 'Recommendation has already been converted.'})
    return None


@transaction.atomic
def convert_recommendation(recommendation: MRPRecommendation, *, actor, expected_version, idempotency_key='', source_opc_diagram=None, source_manufacturing_bom_revision=None) -> MRPRecommendation:
    _require_manager(actor, 'convert MRP recommendations')
    rec = MRPRecommendation.objects.select_for_update(of=('self',)).select_related('mrp_run', 'item_revision').get(pk=recommendation.pk)
    if int(expected_version) != rec.recommendation_version:
        raise MRPConflict('recommendation_version_conflict', 'MRP recommendation version conflict.', expected_version=expected_version, current_version=rec.recommendation_version)
    if rec.status != MRPRecommendation.STATUS_APPROVED:
        raise EngineeringLifecycleError({'status': 'Only approved recommendations can be converted.'})
    if not recommendation_freshness(rec)['fresh']:
        raise EngineeringLifecycleError({'freshness': 'Stale recommendations cannot be converted.'})
    signature = {'recommendation': str(rec.pk), 'type': rec.recommendation_type, 'quantity': str(rec.quantity), 'opc': source_opc_diagram, 'mbom': source_manufacturing_bom_revision}
    if _idempotent_conversion(rec, idempotency_key, signature):
        return rec
    if rec.recommendation_type == MRPRecommendation.TYPE_PRODUCTION:
        if not source_opc_diagram or not source_manufacturing_bom_revision:
            opc = OPCDiagram.objects.filter(item_revision=rec.item_revision, status=OPCDiagram.STATUS_RELEASED).order_by('revision', 'id').first()
            mbom = BOMRevision.objects.filter(bom__parent_item_revision=rec.item_revision, bom__bom_type=BOM.TYPE_MANUFACTURING, status=BOMRevision.STATUS_RELEASED).order_by('-effective_from', 'revision', 'id').first()
        else:
            opc = source_opc_diagram
            mbom = source_manufacturing_bom_revision
        if not opc or not mbom:
            raise EngineeringLifecycleError({'source': 'Production conversion requires exact released OPC and MBOM sources.'})
        order = ProductionOrder.objects.create(order_number=generate_order_number(), item_revision=rec.item_revision, planned_quantity=rec.quantity, unit=rec.unit, status=ProductionOrder.STATUS_DRAFT, priority=ProductionOrder.PRIORITY_NORMAL, requested_completion_date=rec.planned_receipt_date, source_opc_diagram=opc, source_graph_version=opc.graph_version, source_validation_evidence=opc.validation_evidence.filter(release_ready=True).first(), source_manufacturing_bom_revision=mbom, description=f'MRP recommendation {rec.recommendation_number}', created_by=actor)
        rec.converted_production_order = order
    elif rec.recommendation_type == MRPRecommendation.TYPE_PURCHASE_REQUISITION:
        req = PurchaseRequisition.objects.create(requisition_number=_next_number(PurchaseRequisition, 'requisition_number', f'PR-{timezone.now():%Y}-'), item_revision=rec.item_revision, quantity=rec.quantity, unit=rec.unit, required_date=rec.planned_receipt_date, warehouse=rec.warehouse, source_recommendation=rec, created_by=actor)
        rec.converted_purchase_requisition = req
    else:
        raise EngineeringLifecycleError({'recommendation_type': 'This recommendation type cannot be converted in Phase 11.'})
    rec.status = MRPRecommendation.STATUS_CONVERTED
    rec.converted_by = actor
    rec.converted_at = timezone.now()
    rec.recommendation_version += 1
    rec.exception_codes = [*rec.exception_codes, f'idempotency:{idempotency_key}:{stable_checksum(signature)}'] if idempotency_key else rec.exception_codes
    rec.save()
    return rec
