from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from auditlog.services import log_event
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.models import BOMRevision
from enterprise_items.permissions import can_manage_engineering, is_engineering_releaser

from .models import (
    ControlledDocumentRevision,
    ManufacturingOperation,
    ManufacturingOperationEligibleMachine,
    ManufacturingOperationPrecedence,
    OPCDiagram,
    OPCValidationEvidence,
    ProductionDocumentRequirement,
    ProductionMaterialRequirement,
    ProductionOrder,
    ProductionOrderValidationEvidence,
    ProductionToolingRequirement,
)

GENERATOR_POLICY_VERSION = 'production-order-v1'
VALIDATION_POLICY_VERSION = 'production-order-release-v1'
QUANTITY_PLACES = Decimal('0.000001')


class ProductionOrderConflict(EngineeringLifecycleError):
    def __init__(self, order: ProductionOrder, expected_version: int):
        super().__init__({
            'code': 'production_order_version_conflict',
            'detail': 'Production order version conflict.',
            'expected_version': expected_version,
            'current_version': order.order_version,
            'production_order_id': str(order.pk),
        })


def _require_manager(actor):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError('You do not have permission to manage production orders.')


def _require_releaser(actor):
    if not is_engineering_releaser(actor):
        raise EngineeringPermissionError('You do not have permission to release production orders.')


def _q(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(QUANTITY_PLACES, rounding=ROUND_HALF_UP)


def stable_checksum(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def source_checksum_payload(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if key != 'generated_at'}


def check_expected_version(order: ProductionOrder, expected_version):
    if expected_version is None:
        raise ValidationError({'expected_version': 'This field is required.'})
    try:
        expected = int(expected_version)
    except (TypeError, ValueError) as exc:
        raise ValidationError({'expected_version': 'Expected version must be an integer.'}) from exc
    if order.order_version != expected:
        raise ProductionOrderConflict(order, expected)


def allowed_order_actions(order: ProductionOrder) -> list[str]:
    actions: list[str] = []
    if order.status in {ProductionOrder.STATUS_DRAFT, ProductionOrder.STATUS_PLANNED}:
        actions.extend(['generate', 'validate', 'release', 'cancel'])
    elif order.status == ProductionOrder.STATUS_RELEASED:
        actions.append('cancel')
    return actions


def generate_order_number(now=None) -> str:
    now = now or timezone.now()
    prefix = f'PO-{now:%Y}-'
    latest = ProductionOrder.objects.select_for_update().filter(order_number__startswith=prefix).aggregate(Max('order_number'))['order_number__max']
    next_number = int((latest or f'{prefix}000000')[-6:]) + 1
    return f'{prefix}{next_number:06d}'


def latest_release_evidence(diagram: OPCDiagram):
    return OPCValidationEvidence.objects.filter(
        diagram=diagram,
        graph_version=diagram.graph_version,
        mode=OPCValidationEvidence.MODE_RELEASE,
        release_ready=True,
        valid=True,
    ).order_by('-released_at', '-validated_at', '-created_at').first()


def build_source_snapshot(order: ProductionOrder, *, document_revision_ids: list[str]) -> dict:
    item = order.item_revision.item
    mbom = order.source_manufacturing_bom_revision
    return {
        'item_revision': {
            'id': str(order.item_revision_id),
            'item_code': item.item_code,
            'revision': order.item_revision.revision,
            'title': order.item_revision.title,
        },
        'opc': {
            'id': str(order.source_opc_diagram_id),
            'title': order.source_opc_diagram.title,
            'revision': order.source_opc_diagram.revision,
            'graph_version': order.source_graph_version,
            'validation_evidence': str(order.source_validation_evidence_id or ''),
        },
        'mbom': {
            'id': str(order.source_manufacturing_bom_revision_id),
            'bom_code': mbom.bom.bom_code,
            'revision': mbom.revision,
        },
        'documents': sorted(document_revision_ids),
        'generated_at': timezone.now().isoformat(),
        'generator_policy_version': GENERATOR_POLICY_VERSION,
    }


def validate_source_eligibility(order: ProductionOrder):
    if order.source_opc_diagram.status != OPCDiagram.STATUS_RELEASED:
        raise EngineeringLifecycleError({'source_opc_diagram': 'Source OPC must be released.'})
    if order.source_opc_diagram.item_revision_id != order.item_revision_id:
        raise EngineeringLifecycleError({'source_opc_diagram': 'Source OPC does not match target ItemRevision.'})
    if order.source_graph_version and order.source_graph_version != order.source_opc_diagram.graph_version:
        raise EngineeringLifecycleError({'source_graph_version': 'Source OPC graph version is stale.'})
    mbom = order.source_manufacturing_bom_revision
    if mbom.status != BOMRevision.STATUS_RELEASED or mbom.bom.bom_type != 'MANUFACTURING':
        raise EngineeringLifecycleError({'source_manufacturing_bom_revision': 'Source MBOM must be a released manufacturing BOM.'})
    if mbom.bom.parent_item_revision_id != order.item_revision_id:
        raise EngineeringLifecycleError({'source_manufacturing_bom_revision': 'Source MBOM does not match target ItemRevision.'})
    if order.source_opc_diagram.manufacturing_bom_revision_id != order.source_manufacturing_bom_revision_id:
        raise EngineeringLifecycleError({'source_manufacturing_bom_revision': 'Source MBOM must match the released OPC baseline.'})
    evidence = order.source_validation_evidence or latest_release_evidence(order.source_opc_diagram)
    if not evidence:
        raise EngineeringLifecycleError({'source_validation_evidence': 'Release validation evidence is required.'})
    if evidence.graph_version != order.source_opc_diagram.graph_version or not evidence.release_ready:
        raise EngineeringLifecycleError({'source_validation_evidence': 'Release evidence must match source OPC graph version.'})
    order.source_validation_evidence = evidence
    order.source_graph_version = order.source_opc_diagram.graph_version


@transaction.atomic
def create_production_order(*, actor, **attrs) -> ProductionOrder:
    _require_manager(actor)
    order = ProductionOrder(**attrs)
    order.created_by = actor
    order.order_number = generate_order_number()
    if not order.source_manufacturing_bom_revision_id and order.source_opc_diagram_id:
        order.source_manufacturing_bom_revision = order.source_opc_diagram.manufacturing_bom_revision
    validate_source_eligibility(order)
    order.save()
    log_event('production_order_created', target=order, category='business')
    return order


@transaction.atomic
def update_production_order(order: ProductionOrder, *, actor, expected_version, **attrs) -> ProductionOrder:
    _require_manager(actor)
    order = ProductionOrder.objects.select_for_update().get(pk=order.pk)
    check_expected_version(order, expected_version)
    if order.status in ProductionOrder.LOCKED_STATUSES:
        raise EngineeringLifecycleError({'status': 'Released or cancelled production orders are read-only.'})
    for blocked in ('status', 'order_number', 'released_by', 'released_at', 'cancelled_by', 'cancelled_at'):
        attrs.pop(blocked, None)
    for field, value in attrs.items():
        setattr(order, field, value)
    validate_source_eligibility(order)
    order.order_version += 1
    order.save()
    log_event('production_order_updated', target=order, category='business')
    return order


def _clear_generated(order: ProductionOrder):
    order.precedences.all().delete()
    order.material_requirements.all().delete()
    order.tooling_requirements.all().delete()
    order.document_requirements.all().delete()
    order.inspection_requirements.all().delete()
    order.operations.all().delete()


@transaction.atomic
def generate_order_baseline(order: ProductionOrder, *, actor, expected_version) -> ProductionOrder:
    _require_manager(actor)
    order = ProductionOrder.objects.select_for_update(of=('self',)).select_related(
        'item_revision__item',
        'source_opc_diagram',
        'source_manufacturing_bom_revision__bom',
        'source_validation_evidence',
    ).get(pk=order.pk)
    check_expected_version(order, expected_version)
    if order.status in ProductionOrder.LOCKED_STATUSES:
        raise EngineeringLifecycleError({'status': 'Released or cancelled production orders cannot be regenerated.'})
    validate_source_eligibility(order)
    diagram = OPCDiagram.objects.prefetch_related(
        'nodes__tooling_requirements__tooling_definition',
        'nodes__material_allocations__component_item_revision__item',
        'nodes__material_allocations__bom_line',
        'nodes__document_requirements__document_revision__document',
        'edges',
    ).get(pk=order.source_opc_diagram_id)
    _clear_generated(order)
    operations_by_node = {}
    nodes = sorted(diagram.nodes.all(), key=lambda node: (node.operation_number or 999999, node.sequence, str(node.pk)))
    for node in nodes:
        operation = ManufacturingOperation.objects.create(
            production_order=order,
            source_opc_node=node,
            source_operation_number=node.operation_number,
            operation_number=node.operation_number,
            sequence=node.sequence,
            node_type=node.node_type,
            label=node.label,
            description=node.description,
            execution_type=node.execution_type,
            process_definition=node.process_definition,
            process_code_snapshot=node.process_definition.process_code if node.process_definition_id else node.process_code,
            plant=node.plant,
            plant_code_snapshot=node.plant.code if node.plant_id else '',
            work_center=node.work_center,
            work_center_code_snapshot=node.work_center.code if node.work_center_id else '',
            selected_machine=node.machine_asset,
            machine_code_snapshot=node.machine_asset.asset_code if node.machine_asset_id else '',
            setup_time_hours=node.setup_time_hours,
            run_time_per_unit_hours=node.run_time_per_unit_hours,
            queue_time_hours=node.queue_time_hours,
            move_time_hours=node.move_time_hours,
            inspection_time_hours=node.inspection_time_hours,
            external_lead_time_days=node.external_lead_time_days,
            buffer_time_hours=node.buffer_time_hours,
            planned_quantity=order.planned_quantity,
            planned_start=order.planned_start,
            planned_end=order.planned_end,
            status=ManufacturingOperation.STATUS_PLANNED,
        )
        operations_by_node[node.pk] = operation
        if node.machine_asset_id:
            ManufacturingOperationEligibleMachine.objects.create(
                operation=operation,
                machine_asset=node.machine_asset,
                machine_code_snapshot=node.machine_asset.asset_code,
                machine_name_snapshot=node.machine_asset.name,
                preferred=True,
                sequence=1,
            )
        for allocation in sorted(node.material_allocations.all(), key=lambda item: (item.sequence, str(item.pk))):
            net = _q(allocation.quantity) * _q(order.planned_quantity)
            total = (net * (Decimal('1') + (_q(allocation.scrap_percent) / Decimal('100')))).quantize(QUANTITY_PLACES, rounding=ROUND_HALF_UP)
            ProductionMaterialRequirement.objects.create(
                production_order=order,
                operation=operation,
                source_allocation=allocation,
                source_mbom_line=allocation.bom_line,
                component_item_revision=allocation.component_item_revision,
                component_code_snapshot=allocation.component_item_revision.item.item_code,
                component_revision_snapshot=allocation.component_item_revision.revision,
                quantity_per_unit=_q(allocation.quantity),
                planned_order_quantity=_q(order.planned_quantity),
                total_net_quantity=net,
                scrap_percent=_q(allocation.scrap_percent),
                total_planned_quantity=total,
                unit=allocation.unit,
                allocation_type=allocation.allocation_type,
                sequence=allocation.sequence,
                notes=allocation.notes,
            )
        for requirement in sorted(node.tooling_requirements.all(), key=lambda item: (item.sequence, str(item.pk))):
            ProductionToolingRequirement.objects.create(
                production_order=order,
                operation=operation,
                source_tooling_requirement=requirement,
                tooling_definition=requirement.tooling_definition,
                tooling_code_snapshot=requirement.tooling_definition.tooling_code,
                tooling_name_snapshot=requirement.tooling_definition.name,
                quantity=requirement.quantity,
                mandatory=requirement.mandatory,
                sequence=requirement.sequence,
                notes=requirement.notes,
            )
        for requirement in sorted(node.document_requirements.all(), key=lambda item: (item.sequence, str(item.pk))):
            revision = requirement.document_revision
            ProductionDocumentRequirement.objects.create(
                production_order=order,
                operation=operation,
                source_document_requirement=requirement,
                controlled_document_revision=revision,
                document_number_snapshot=revision.document.document_number,
                document_revision_snapshot=revision.revision,
                title_snapshot=revision.document.title,
                purpose=requirement.purpose,
                mandatory=requirement.mandatory,
                sequence=requirement.sequence,
                notes=requirement.notes,
            )
    for edge in sorted(diagram.edges.all(), key=lambda edge: (edge.sequence, str(edge.pk))):
        predecessor = operations_by_node.get(edge.source_id)
        successor = operations_by_node.get(edge.target_id)
        if predecessor and successor:
            ManufacturingOperationPrecedence.objects.create(
                production_order=order,
                predecessor=predecessor,
                successor=successor,
                source_opc_edge=edge,
                edge_type=edge.edge_type,
                label_snapshot=edge.label,
                sequence=edge.sequence,
                optional=edge.edge_type == 'OPTIONAL',
                rework=edge.edge_type == 'REWORK',
            )
    from .quality_services import create_inspection_requirements_for_order

    create_inspection_requirements_for_order(order)
    document_ids = [str(row.controlled_document_revision_id) for row in order.document_requirements.order_by('controlled_document_revision_id')]
    document_ids += [str(row.instruction_document_revision_id) for row in order.inspection_requirements.exclude(instruction_document_revision_id__isnull=True).order_by('instruction_document_revision_id')]
    order.source_snapshot = build_source_snapshot(order, document_revision_ids=document_ids)
    order.source_snapshot_checksum = stable_checksum(source_checksum_payload(order.source_snapshot))
    order.generator_policy_version = GENERATOR_POLICY_VERSION
    order.generated_at = timezone.now()
    order.status = ProductionOrder.STATUS_PLANNED
    order.order_version += 1
    order.save()
    log_event('production_order_generated', target=order, category='business', extra={'checksum': order.source_snapshot_checksum})
    return order


def validate_production_order(order: ProductionOrder, *, actor=None, persist=False, release=False) -> dict:
    issues = []

    def add(code, message, scope='ORDER', target_id='', blocking=True, severity='ERROR'):
        issues.append({
            'code': code,
            'severity': severity,
            'scope': scope,
            'target_id': str(target_id or order.pk),
            'message': message,
            'blocking': blocking,
        })

    if order.planned_quantity <= 0:
        add('planned_quantity_positive', 'Planned quantity must be positive.')
    if order.planned_start and order.planned_end and order.planned_end < order.planned_start:
        add('planned_dates_order', 'Planned end cannot precede planned start.')
    try:
        validate_source_eligibility(order)
    except EngineeringLifecycleError as exc:
        add('source_eligibility', str(exc))
    if not order.operations.exists():
        add('operations_required', 'Generate the order baseline before release.')
    if not order.material_requirements.exists():
        add('material_requirements_required', 'Generated material requirements are required.')
    for req in order.document_requirements.select_related('controlled_document_revision'):
        if req.controlled_document_revision.status != ControlledDocumentRevision.STATUS_RELEASED:
            add('document_revision_released', 'Document requirement must reference a released revision.', scope='DOCUMENT', target_id=req.pk)
    blocking = sum(1 for issue in issues if issue['blocking'])
    counts = {
        'error_count': sum(1 for issue in issues if issue['severity'] == 'ERROR'),
        'blocking_error_count': blocking,
        'warning_count': sum(1 for issue in issues if issue['severity'] == 'WARNING'),
        'info_count': sum(1 for issue in issues if issue['severity'] == 'INFO'),
    }
    result = {
        'production_order_id': str(order.pk),
        'order_version': order.order_version,
        'validated_at': timezone.now().isoformat(),
        'policy_version': VALIDATION_POLICY_VERSION,
        'valid': blocking == 0,
        'release_ready': blocking == 0,
        'counts': counts,
        'issues': issues,
    }
    if persist:
        ProductionOrderValidationEvidence.objects.create(
            production_order=order,
            order_version=order.order_version,
            policy_version=VALIDATION_POLICY_VERSION,
            validated_at=timezone.now(),
            released_at=timezone.now() if release and blocking == 0 else None,
            actor=actor if getattr(actor, 'is_authenticated', False) else None,
            valid=blocking == 0,
            release_ready=blocking == 0,
            counts=counts,
            issues=issues,
        )
    return result


@transaction.atomic
def release_production_order(order: ProductionOrder, *, actor, expected_version) -> ProductionOrder:
    _require_releaser(actor)
    order = ProductionOrder.objects.select_for_update().get(pk=order.pk)
    check_expected_version(order, expected_version)
    if order.status not in {ProductionOrder.STATUS_PLANNED, ProductionOrder.STATUS_DRAFT}:
        raise EngineeringLifecycleError({'status': 'Only draft or planned orders can be released.'})
    result = validate_production_order(order, actor=actor, persist=True, release=True)
    if not result['release_ready']:
        raise EngineeringLifecycleError({'validation': result})
    order.status = ProductionOrder.STATUS_RELEASED
    order.released_by = actor
    order.released_at = timezone.now()
    order.order_version += 1
    order.operations.update(status=ManufacturingOperation.STATUS_RELEASED)
    order.save(update_fields=['status', 'released_by', 'released_at', 'order_version', 'updated_at'])
    log_event('production_order_released', target=order, category='business')
    return order


@transaction.atomic
def cancel_production_order(order: ProductionOrder, *, actor, expected_version) -> ProductionOrder:
    _require_manager(actor)
    order = ProductionOrder.objects.select_for_update().get(pk=order.pk)
    check_expected_version(order, expected_version)
    if order.status == ProductionOrder.STATUS_CANCELLED:
        raise EngineeringLifecycleError({'status': 'Production order is already cancelled.'})
    order.status = ProductionOrder.STATUS_CANCELLED
    order.cancelled_by = actor
    order.cancelled_at = timezone.now()
    order.order_version += 1
    order.operations.update(status=ManufacturingOperation.STATUS_CANCELLED)
    order.save(update_fields=['status', 'cancelled_by', 'cancelled_at', 'order_version', 'updated_at'])
    log_event('production_order_cancelled', target=order, category='business')
    return order
