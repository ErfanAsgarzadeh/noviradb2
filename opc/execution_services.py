from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from auditlog.services import log_event
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.permissions import can_manage_engineering

from .models import (
    ManufacturingExecutionEvent,
    ManufacturingGenealogyLink,
    ManufacturingOperationPrecedence,
    OperationExecution,
    OperationExecutionCycle,
    ProductionDocumentAcknowledgement,
    ProductionDocumentRequirement,
    ProductionLot,
    ProductionMaterialConsumption,
    ProductionMaterialRequirement,
    ProductionOrder,
    ProductionSerial,
    ProductionToolingRequirement,
    ProductionToolingUsage,
)

Q6 = Decimal('0.000001')


class ExecutionConflict(EngineeringLifecycleError):
    def __init__(self, execution: OperationExecution, expected_version: int):
        super().__init__({
            'code': 'execution_version_conflict',
            'detail': 'Operation execution version conflict.',
            'expected_version': expected_version,
            'current_version': execution.execution_version,
            'operation_execution_id': str(execution.pk),
        })


def _require_operator(actor):
    if not getattr(actor, 'is_authenticated', False):
        raise EngineeringPermissionError('Authentication is required for shop-floor execution.')


def _require_manager(actor):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError('You do not have permission to manage shop-floor execution.')


def decimal_value(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Q6, rounding=ROUND_HALF_UP)


def allowed_execution_actions(execution: OperationExecution) -> list[str]:
    actions = []
    if execution.status in {OperationExecution.STATUS_READY, OperationExecution.STATUS_NOT_READY}:
        actions.extend(['dispatch', 'assign_operator', 'assign_machine', 'block'])
    if execution.status == OperationExecution.STATUS_DISPATCHED:
        actions.extend(['assign_operator', 'assign_machine', 'start', 'block', 'cancel'])
    if execution.status == OperationExecution.STATUS_RUNNING:
        actions.extend(['pause', 'record_output', 'consume_material', 'record_tooling', 'ack_document', 'complete', 'rework', 'block'])
    if execution.status == OperationExecution.STATUS_PAUSED:
        actions.extend(['resume', 'record_output', 'complete', 'block'])
    if execution.status == OperationExecution.STATUS_BLOCKED:
        actions.append('unblock')
    return actions


def ensure_executions_for_order(order: ProductionOrder, *, actor=None):
    if order.status != ProductionOrder.STATUS_RELEASED:
        raise EngineeringLifecycleError({'production_order': 'Execution requires a released production order.'})
    created = []
    for operation in order.operations.order_by('operation_number', 'sequence', 'id'):
        execution, was_created = OperationExecution.objects.get_or_create(
            manufacturing_operation=operation,
            defaults={
                'production_order': order,
                'planned_quantity': operation.planned_quantity,
                'status': OperationExecution.STATUS_NOT_READY,
            },
        )
        if was_created:
            OperationExecutionCycle.objects.create(operation_execution=execution, cycle_number=1, quantity=operation.planned_quantity, created_by=actor)
            created.append(execution)
    refresh_order_readiness(order)
    return created


def predecessors_complete(execution: OperationExecution) -> tuple[bool, list[str]]:
    blockers = []
    precedences = ManufacturingOperationPrecedence.objects.filter(
        production_order=execution.production_order,
        successor=execution.manufacturing_operation,
    ).select_related('predecessor__execution')
    for edge in precedences:
        if edge.edge_type in {'OPTIONAL', 'REWORK'}:
            continue
        predecessor_execution = getattr(edge.predecessor, 'execution', None)
        if not predecessor_execution or predecessor_execution.status != OperationExecution.STATUS_COMPLETED:
            blockers.append(f'Predecessor operation {edge.predecessor.operation_number or edge.predecessor.sequence} is not complete.')
    return not blockers, blockers


def refresh_readiness(execution: OperationExecution):
    if execution.status in {OperationExecution.STATUS_COMPLETED, OperationExecution.STATUS_CANCELLED, OperationExecution.STATUS_BLOCKED, OperationExecution.STATUS_RUNNING, OperationExecution.STATUS_PAUSED}:
        execution.ready = False
        execution.save(update_fields=['ready', 'updated_at'])
        return execution
    ready, blockers = predecessors_complete(execution)
    execution.ready = ready and execution.production_order.status == ProductionOrder.STATUS_RELEASED
    execution.block_reason = '' if execution.ready else '; '.join(blockers)
    if execution.ready and execution.status == OperationExecution.STATUS_NOT_READY:
        execution.status = OperationExecution.STATUS_READY
        execution.save(update_fields=['ready', 'block_reason', 'status', 'updated_at'])
    else:
        execution.save(update_fields=['ready', 'block_reason', 'updated_at'])
    return execution


def refresh_order_readiness(order: ProductionOrder):
    for execution in order.operation_executions.select_related('manufacturing_operation').order_by('manufacturing_operation__sequence'):
        refresh_readiness(execution)


def progress_rollup(order: ProductionOrder) -> dict:
    executions = list(order.operation_executions.all())
    total = len(executions)
    completed = sum(1 for item in executions if item.status == OperationExecution.STATUS_COMPLETED)
    active = sum(1 for item in executions if item.status in {OperationExecution.STATUS_RUNNING, OperationExecution.STATUS_PAUSED, OperationExecution.STATUS_DISPATCHED})
    blocked = sum(1 for item in executions if item.status == OperationExecution.STATUS_BLOCKED)
    quantities = order.operation_executions.aggregate(
        produced=Sum('produced_quantity'),
        accepted=Sum('accepted_quantity'),
        rejected=Sum('rejected_quantity'),
        scrapped=Sum('scrapped_quantity'),
        rework=Sum('rework_quantity'),
    )
    return {
        'operation_count': total,
        'completed_count': completed,
        'percent_complete': round((completed / total) * 100, 2) if total else 0,
        'active_count': active,
        'blocked_count': blocked,
        'produced_quantity': str(quantities['produced'] or Decimal('0')),
        'accepted_quantity': str(quantities['accepted'] or Decimal('0')),
        'rejected_quantity': str(quantities['rejected'] or Decimal('0')),
        'scrapped_quantity': str(quantities['scrapped'] or Decimal('0')),
        'rework_quantity': str(quantities['rework'] or Decimal('0')),
    }


def _check_expected(execution: OperationExecution, expected_version):
    if expected_version is None:
        raise ValidationError({'expected_version': 'This field is required.'})
    expected = int(expected_version)
    if execution.execution_version != expected:
        raise ExecutionConflict(execution, expected)


def _idempotent_event(execution: OperationExecution, key: str, payload_signature: dict):
    if not key:
        return None
    existing = ManufacturingExecutionEvent.objects.filter(operation_execution=execution, idempotency_key=key).first()
    if not existing:
        return None
    if existing.metadata.get('payload_signature') != payload_signature:
        raise EngineeringLifecycleError({'idempotency_key': 'Idempotency key was reused with a different payload.'})
    return existing


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


def _signature_payload(command: str, payload: dict) -> dict:
    return {'command': command, **{key: _signature_value(value) for key, value in payload.items()}}


def _append_event(execution: OperationExecution, *, actor, event_type, idempotency_key='', payload_signature=None, **attrs):
    sequence = (ManufacturingExecutionEvent.objects.filter(operation_execution=execution).aggregate(Max('event_sequence'))['event_sequence__max'] or 0) + 1
    previous = execution.execution_version
    execution.execution_version += 1
    event = ManufacturingExecutionEvent.objects.create(
        production_order=execution.production_order,
        operation_execution=execution,
        cycle=execution.cycles.order_by('-cycle_number').first(),
        event_sequence=sequence,
        event_type=event_type,
        actor=actor,
        assigned_operator=execution.assigned_operator,
        machine=execution.assigned_machine,
        event_timestamp=timezone.now(),
        idempotency_key=idempotency_key or f'auto:{sequence}',
        previous_execution_version=previous,
        new_execution_version=execution.execution_version,
        metadata={'payload_signature': payload_signature or {}, **attrs.pop('metadata', {})},
        **attrs,
    )
    return event


@transaction.atomic
def command_execution(execution: OperationExecution, *, actor, expected_version, command, idempotency_key='', **payload):
    _require_operator(actor)
    execution = OperationExecution.objects.select_for_update(of=('self',)).select_related('production_order', 'manufacturing_operation').get(pk=execution.pk)
    signature = _signature_payload(command, payload)
    existing = _idempotent_event(execution, idempotency_key, signature)
    if existing:
        return execution, existing
    _check_expected(execution, expected_version)
    now = timezone.now()
    event_type = command.upper()
    event_kwargs = {'idempotency_key': idempotency_key, 'payload_signature': signature, 'notes': payload.get('notes', '')}

    if command == 'dispatch':
        refresh_readiness(execution)
        if not execution.ready:
            raise EngineeringLifecycleError({'readiness': execution.block_reason or 'Operation is not ready.'})
        execution.status = OperationExecution.STATUS_DISPATCHED
    elif command == 'assign_operator':
        operator = payload.get('operator') or actor
        execution.assigned_operator = operator
        event_type = ManufacturingExecutionEvent.EVENT_ASSIGN_OPERATOR
    elif command == 'assign_machine':
        machine = payload['machine']
        if not execution.manufacturing_operation.eligible_machines.filter(machine_asset=machine).exists():
            raise EngineeringLifecycleError({'machine': 'Machine is not eligible for this operation.'})
        execution.assigned_machine = machine
        event_type = ManufacturingExecutionEvent.EVENT_ASSIGN_MACHINE
    elif command == 'start':
        if execution.status not in {OperationExecution.STATUS_DISPATCHED, OperationExecution.STATUS_READY}:
            raise EngineeringLifecycleError({'status': 'Only ready or dispatched operations can start.'})
        refresh_readiness(execution)
        if not execution.ready and execution.status != OperationExecution.STATUS_DISPATCHED:
            raise EngineeringLifecycleError({'readiness': execution.block_reason or 'Operation is not ready.'})
        machine = payload.get('machine') or execution.assigned_machine or execution.manufacturing_operation.scheduled_machine or execution.manufacturing_operation.selected_machine
        from .maintenance_services import machine_execution_blockers
        blockers = machine_execution_blockers(machine)
        if blockers:
            raise EngineeringLifecycleError({'maintenance': blockers})
        execution.status = OperationExecution.STATUS_RUNNING
        execution.active_interval = 'RUN'
        execution.active_interval_started_at = now
        execution.first_started_at = execution.first_started_at or now
        event_type = ManufacturingExecutionEvent.EVENT_START
    elif command == 'pause':
        if execution.status != OperationExecution.STATUS_RUNNING:
            raise EngineeringLifecycleError({'status': 'Only running operations can pause.'})
        if execution.active_interval_started_at:
            execution.actual_run_seconds += int((now - execution.active_interval_started_at).total_seconds())
        execution.status = OperationExecution.STATUS_PAUSED
        execution.paused_at = now
        execution.active_interval = ''
        execution.active_interval_started_at = None
        event_type = ManufacturingExecutionEvent.EVENT_PAUSE
    elif command == 'resume':
        if execution.status != OperationExecution.STATUS_PAUSED:
            raise EngineeringLifecycleError({'status': 'Only paused operations can resume.'})
        execution.status = OperationExecution.STATUS_RUNNING
        execution.active_interval = 'RUN'
        execution.active_interval_started_at = now
        event_type = ManufacturingExecutionEvent.EVENT_RESUME
    elif command == 'record_output':
        produced = decimal_value(payload.get('produced_quantity'))
        accepted = decimal_value(payload.get('accepted_quantity'))
        rejected = decimal_value(payload.get('rejected_quantity'))
        scrapped = decimal_value(payload.get('scrapped_quantity'))
        rework = decimal_value(payload.get('rework_quantity'))
        if min(produced, accepted, rejected, scrapped, rework) < 0:
            raise EngineeringLifecycleError({'quantity': 'Execution quantities cannot be negative.'})
        if accepted + rejected + scrapped > produced:
            raise EngineeringLifecycleError({'quantity': 'Accepted, rejected and scrapped quantity cannot exceed produced quantity.'})
        if rework > rejected:
            raise EngineeringLifecycleError({'rework_quantity': 'Rework quantity cannot exceed rejected quantity.'})
        execution.produced_quantity += produced
        execution.accepted_quantity += accepted
        execution.rejected_quantity += rejected
        execution.scrapped_quantity += scrapped
        execution.rework_quantity += rework
        event_kwargs.update(produced_quantity=produced, accepted_quantity=accepted, rejected_quantity=rejected, scrapped_quantity=scrapped, rework_quantity=rework)
        event_type = ManufacturingExecutionEvent.EVENT_RECORD_OUTPUT
    elif command == 'complete':
        if execution.produced_quantity <= 0:
            raise EngineeringLifecycleError({'quantity': 'Record output before completing the operation.'})
        from .quality_services import ensure_operation_quality_gate

        ensure_operation_quality_gate(execution)
        mandatory_docs = execution.production_order.document_requirements.filter(operation=execution.manufacturing_operation, mandatory=True)
        acknowledged = execution.document_acknowledgements.values_list('requirement_id', flat=True)
        if mandatory_docs.exclude(pk__in=acknowledged).exists():
            raise EngineeringLifecycleError({'documents': 'Mandatory documents must be acknowledged before completion.'})
        mandatory_tools = execution.production_order.tooling_requirements.filter(operation=execution.manufacturing_operation, mandatory=True)
        used_tools = execution.tooling_usages.values_list('requirement_id', flat=True)
        if mandatory_tools.exclude(pk__in=used_tools).exists():
            raise EngineeringLifecycleError({'tooling': 'Mandatory tooling usage must be recorded before completion.'})
        if execution.active_interval_started_at:
            execution.actual_run_seconds += int((now - execution.active_interval_started_at).total_seconds())
        execution.status = OperationExecution.STATUS_COMPLETED
        execution.completed_at = now
        execution.active_interval = ''
        execution.active_interval_started_at = None
        cycle = execution.cycles.order_by('-cycle_number').first()
        if cycle:
            cycle.status = OperationExecutionCycle.STATUS_COMPLETED
            cycle.completed_at = now
            cycle.save(update_fields=['status', 'completed_at', 'updated_at'])
        event_type = ManufacturingExecutionEvent.EVENT_COMPLETE
    elif command == 'block':
        execution.status = OperationExecution.STATUS_BLOCKED
        execution.block_reason = payload.get('reason') or payload.get('notes') or 'Blocked'
        event_type = ManufacturingExecutionEvent.EVENT_BLOCK
    elif command == 'unblock':
        execution.status = OperationExecution.STATUS_NOT_READY
        execution.block_reason = ''
        event_type = ManufacturingExecutionEvent.EVENT_UNBLOCK
    elif command == 'cancel':
        execution.status = OperationExecution.STATUS_CANCELLED
        event_type = ManufacturingExecutionEvent.EVENT_CANCEL
    elif command == 'rework':
        edge = payload['rework_edge']
        quantity = decimal_value(payload.get('quantity'))
        if edge.production_order_id != execution.production_order_id or not edge.rework:
            raise EngineeringLifecycleError({'rework_edge': 'Rework must use a released REWORK edge for this order.'})
        if quantity <= 0 or quantity > execution.rejected_quantity:
            raise EngineeringLifecycleError({'quantity': 'Rework quantity must be positive and cannot exceed rejected quantity.'})
        target_execution = edge.successor.execution
        next_cycle = (target_execution.cycles.aggregate(Max('cycle_number'))['cycle_number__max'] or 1) + 1
        OperationExecutionCycle.objects.create(operation_execution=target_execution, cycle_number=next_cycle, reason=payload.get('reason', ''), source_rework_edge=edge, quantity=quantity, created_by=actor)
        target_execution.status = OperationExecution.STATUS_READY
        target_execution.current_cycle_number = next_cycle
        target_execution.ready = True
        target_execution.save(update_fields=['status', 'current_cycle_number', 'ready', 'updated_at'])
        event_kwargs.update(rework_quantity=quantity, metadata={'target_execution': str(target_execution.pk), 'cycle_number': next_cycle})
        event_type = ManufacturingExecutionEvent.EVENT_REWORK
    elif command == 'document_ack':
        if execution.status not in {OperationExecution.STATUS_RUNNING, OperationExecution.STATUS_PAUSED}:
            raise EngineeringLifecycleError({'status': 'Documents can only be acknowledged while an operation is running or paused.'})
        event_type = ManufacturingExecutionEvent.EVENT_DOCUMENT_ACK
    elif command == 'tooling_usage':
        if execution.status not in {OperationExecution.STATUS_RUNNING, OperationExecution.STATUS_PAUSED}:
            raise EngineeringLifecycleError({'status': 'Tooling can only be recorded while an operation is running or paused.'})
        event_type = ManufacturingExecutionEvent.EVENT_TOOLING_USAGE
    elif command == 'consume_material':
        if execution.status not in {OperationExecution.STATUS_RUNNING, OperationExecution.STATUS_PAUSED}:
            raise EngineeringLifecycleError({'status': 'Material can only be consumed while an operation is running or paused.'})
        event_type = ManufacturingExecutionEvent.EVENT_CONSUME_MATERIAL
    elif command == 'reverse_material':
        if execution.status not in {OperationExecution.STATUS_RUNNING, OperationExecution.STATUS_PAUSED}:
            raise EngineeringLifecycleError({'status': 'Material can only be reversed while an operation is running or paused.'})
        event_type = ManufacturingExecutionEvent.EVENT_REVERSE_MATERIAL
    elif command == 'genealogy_link':
        if execution.status not in {OperationExecution.STATUS_RUNNING, OperationExecution.STATUS_PAUSED}:
            raise EngineeringLifecycleError({'status': 'Genealogy can only be linked while an operation is running or paused.'})
        event_type = ManufacturingExecutionEvent.EVENT_GENEALOGY_LINK
    else:
        raise EngineeringLifecycleError({'command': 'Unsupported execution command.'})

    event = _append_event(execution, actor=actor, event_type=event_type, **event_kwargs)
    execution.save()
    refresh_order_readiness(execution.production_order)
    log_event(f'execution_{command}', target=execution, category='business')
    return execution, event


@transaction.atomic
def acknowledge_document(execution: OperationExecution, *, actor, expected_version, requirement: ProductionDocumentRequirement, idempotency_key='', notes=''):
    if requirement.production_order_id != execution.production_order_id or requirement.operation_id != execution.manufacturing_operation_id:
        raise EngineeringLifecycleError({'requirement': 'Document requirement belongs to a different operation.'})
    execution, event = command_execution(execution, actor=actor, expected_version=expected_version, command='document_ack', idempotency_key=idempotency_key, requirement=requirement, notes=notes)
    ack, _ = ProductionDocumentAcknowledgement.objects.get_or_create(
        operation_execution=execution,
        requirement=requirement,
        actor=actor,
        defaults={
            'production_order': execution.production_order,
            'controlled_document_revision': requirement.controlled_document_revision,
            'event': event,
            'acknowledged_at': event.event_timestamp,
            'notes': notes,
        },
    )
    return execution, ack


@transaction.atomic
def record_tooling_usage(execution: OperationExecution, *, actor, expected_version, requirement: ProductionToolingRequirement, quantity, idempotency_key='', notes=''):
    if requirement.production_order_id != execution.production_order_id or requirement.operation_id != execution.manufacturing_operation_id:
        raise EngineeringLifecycleError({'requirement': 'Tooling requirement belongs to a different operation.'})
    execution, event = command_execution(execution, actor=actor, expected_version=expected_version, command='tooling_usage', idempotency_key=idempotency_key, requirement=requirement, quantity=quantity, notes=notes)
    existing = ProductionToolingUsage.objects.filter(event=event).first()
    if existing:
        return execution, existing
    usage = ProductionToolingUsage.objects.create(
        production_order=execution.production_order,
        operation_execution=execution,
        requirement=requirement,
        tooling_definition=requirement.tooling_definition,
        quantity=Decimal(str(quantity)),
        machine=execution.assigned_machine,
        event=event,
        actor=actor,
        used_at=event.event_timestamp,
        notes=notes,
    )
    return execution, usage


@transaction.atomic
def consume_material(execution: OperationExecution, *, actor, expected_version, requirement: ProductionMaterialRequirement, quantity, idempotency_key='', lot=None, serial=None, reverse=False, notes=''):
    if requirement.production_order_id != execution.production_order_id or requirement.operation_id != execution.manufacturing_operation_id:
        raise EngineeringLifecycleError({'requirement': 'Material requirement belongs to a different operation.'})
    qty = decimal_value(quantity)
    if qty <= 0:
        raise EngineeringLifecycleError({'quantity': 'Consumption quantity must be positive.'})
    existing_net = requirement.consumptions.aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    if reverse and qty > existing_net:
        raise EngineeringLifecycleError({'quantity': 'Cannot reverse more material than net consumed.'})
    signed_qty = -qty if reverse else qty
    execution, event = command_execution(
        execution,
        actor=actor,
        expected_version=expected_version,
        command='reverse_material' if reverse else 'consume_material',
        idempotency_key=idempotency_key,
        requirement=requirement,
        quantity=qty,
        lot=lot,
        serial=serial,
        notes=notes,
    )
    existing = ProductionMaterialConsumption.objects.filter(event=event).first()
    if existing:
        return execution, existing
    consumption = ProductionMaterialConsumption.objects.create(
        production_order=execution.production_order,
        operation_execution=execution,
        cycle=execution.cycles.order_by('-cycle_number').first(),
        requirement=requirement,
        component_item_revision=requirement.component_item_revision,
        quantity=signed_qty,
        unit=requirement.unit,
        consumption_type=ProductionMaterialConsumption.TYPE_REVERSAL if reverse else ProductionMaterialConsumption.TYPE_CONSUME,
        lot=lot,
        serial=serial,
        event=event,
        actor=actor,
        consumed_at=event.event_timestamp,
        notes=notes,
    )
    return execution, consumption


def _identity_key(lot=None, serial=None):
    return ('S', str(serial.pk)) if serial else ('L', str(lot.pk))


def _descendants(parent_key):
    links = ManufacturingGenealogyLink.objects.select_related('component_lot', 'component_serial')
    for link in links:
        if _identity_key(lot=link.parent_lot, serial=link.parent_serial) == parent_key:
            child = _identity_key(lot=link.component_lot, serial=link.component_serial)
            yield child
            yield from _descendants(child)


@transaction.atomic
def link_genealogy(execution: OperationExecution, *, actor, expected_version, consumption: ProductionMaterialConsumption, parent_lot=None, parent_serial=None, component_lot=None, component_serial=None, idempotency_key='', notes=''):
    if consumption.production_order_id != execution.production_order_id:
        raise EngineeringLifecycleError({'consumption': 'Consumption belongs to a different production order.'})
    parent = _identity_key(lot=parent_lot, serial=parent_serial)
    component = _identity_key(lot=component_lot, serial=component_serial)
    if parent == component or parent in set(_descendants(component)):
        raise EngineeringLifecycleError({'genealogy': 'Genealogy cycle detected.'})
    execution, event = command_execution(
        execution,
        actor=actor,
        expected_version=expected_version,
        command='genealogy_link',
        idempotency_key=idempotency_key,
        consumption=consumption,
        parent_lot=parent_lot,
        parent_serial=parent_serial,
        component_lot=component_lot,
        component_serial=component_serial,
        notes=notes,
    )
    existing = ManufacturingGenealogyLink.objects.filter(event=event).first()
    if existing:
        return execution, existing
    link = ManufacturingGenealogyLink.objects.create(
        production_order=execution.production_order,
        parent_lot=parent_lot,
        parent_serial=parent_serial,
        component_lot=component_lot,
        component_serial=component_serial,
        consumption=consumption,
        event=event,
        actor=actor,
        linked_at=event.event_timestamp,
        notes=notes,
    )
    return execution, link
