from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max

from opc.models import OPCDiagram, OPCNode

from .models import (
    Assignment,
    Dependency,
    Project,
    ProjectOPCImport,
    Resource,
    Revision,
    Task,
    TaskVersion,
    WBSNode,
    WBSNodeVersion,
)
from .permissions import require_can_edit_project


DEFAULT_OPERATION_HOURS = Decimal('8.00')
MIN_OPERATION_HOURS = Decimal('0.25')


def _check_revision_is_open(revision: Revision, actor):
    if revision.approved_at is not None:
        raise ValidationError({'revision': 'Approved revisions cannot be changed.'})
    require_can_edit_project(actor, revision.project)


def _operation_duration(node: OPCNode, quantity: Decimal) -> Decimal:
    duration = (
        Decimal(node.setup_time_hours or 0)
        + Decimal(node.run_time_per_unit_hours or 0) * quantity
        + Decimal(node.queue_time_hours or 0)
        + Decimal(node.move_time_hours or 0)
        + Decimal(node.inspection_time_hours or 0)
        + Decimal(node.buffer_time_hours or 0)
        + Decimal(node.external_lead_time_days or 0) * Decimal('24')
    )
    if duration <= 0:
        duration = DEFAULT_OPERATION_HOURS
    return max(duration.quantize(Decimal('0.01')), MIN_OPERATION_HOURS)


def _resource_code(prefix: str, value: str) -> str:
    clean = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '-' for ch in str(value).upper()).strip('-')
    return f'{prefix}-{clean}'[:50]


def _resource_payload(node: OPCNode):
    if node.machine_asset_id:
        machine = node.machine_asset
        code = _resource_code('MACHINE', machine.asset_code or machine.pk)
        return {
            'code': code,
            'name': machine.name or machine.asset_code or code,
            'resource_type': Resource.EQUIPMENT,
            'source': {'type': 'machine_asset', 'id': str(machine.pk), 'code': machine.asset_code},
        }
    if node.work_center_id:
        work_center = node.work_center
        code = _resource_code('WC', work_center.code or work_center.pk)
        return {
            'code': code,
            'name': work_center.name or work_center.code or code,
            'resource_type': Resource.EQUIPMENT,
            'source': {'type': 'work_center', 'id': str(work_center.pk), 'code': work_center.code},
        }
    return None


def _ensure_resource(node: OPCNode):
    payload = _resource_payload(node)
    if not payload:
        return None, None
    resource, _created = Resource.objects.get_or_create(
        code=payload['code'],
        defaults={
            'name': payload['name'],
            'resource_type': payload['resource_type'],
            'max_units': Decimal('100.00'),
            'is_active': True,
        },
    )
    return resource, payload


def _operation_nodes(diagram: OPCDiagram) -> list[OPCNode]:
    return list(
        diagram.nodes.select_related('process_definition', 'plant', 'work_center', 'machine_asset')
        .filter(node_type__in=['OPERATION', 'INSPECTION', 'TRANSPORT'])
        .order_by('sequence', 'operation_number', 'created_at')
    )


def _dependency_pairs(diagram: OPCDiagram, operations: Iterable[OPCNode]) -> list[tuple[str, str, str]]:
    operation_ids = {node.pk for node in operations}
    pairs = []
    seen = set()
    for edge in diagram.edges.select_related('source', 'target').order_by('sequence', 'created_at'):
        if edge.source_id not in operation_ids or edge.target_id not in operation_ids:
            continue
        key = (str(edge.source_id), str(edge.target_id))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((str(edge.source_id), str(edge.target_id), str(edge.pk)))

    if pairs:
        return pairs

    ordered = list(operations)
    for predecessor, successor in zip(ordered, ordered[1:]):
        pairs.append((str(predecessor.pk), str(successor.pk), 'sequence'))
    return pairs


def build_opc_wbs_preview(*, revision: Revision, parent_wbs_node: WBSNodeVersion, opc_diagram: OPCDiagram, quantity=1) -> dict:
    if parent_wbs_node.revision_id != revision.pk:
        raise ValidationError({'parent_wbs_node': 'Parent WBS must belong to the selected revision.'})
    if revision.project_id != parent_wbs_node.node.project_id:
        raise ValidationError({'revision': 'Revision and parent WBS project mismatch.'})
    if opc_diagram.status != OPCDiagram.STATUS_RELEASED:
        raise ValidationError({'opc_diagram': 'Only released OPC diagrams can be imported into a project plan.'})

    quantity_decimal = Decimal(str(quantity or 1))
    if quantity_decimal <= 0:
        raise ValidationError({'quantity': 'Quantity must be positive.'})

    operations = _operation_nodes(opc_diagram)
    if not operations:
        raise ValidationError({'opc_diagram': 'Released OPC has no schedulable operation nodes.'})

    tasks = []
    warnings = []
    total_hours = Decimal('0.00')
    for index, node in enumerate(operations, start=1):
        duration = _operation_duration(node, quantity_decimal)
        total_hours += duration
        resource_payload = _resource_payload(node)
        if not resource_payload:
            warnings.append({
                'code': 'missing_resource_mapping',
                'opcNodeId': str(node.pk),
                'operationNumber': node.operation_number,
                'message': 'OPC operation has no machine or work center; task will be created without an assignment.',
            })
        tasks.append({
            'opcNodeId': str(node.pk),
            'operationNumber': node.operation_number,
            'sequence': index,
            'name': f"OP{node.operation_number:03d} - {node.label}" if node.operation_number else node.label,
            'durationHours': str(duration),
            'resource': resource_payload,
            'executionType': node.execution_type,
            'processCode': node.process_code or (node.process_definition.process_code if node.process_definition_id else ''),
        })

    dependencies = [
        {'predecessorOpcNodeId': predecessor, 'successorOpcNodeId': successor, 'sourceEdgeId': edge_id, 'type': 'FS'}
        for predecessor, successor, edge_id in _dependency_pairs(opc_diagram, operations)
    ]

    return {
        'opcDiagram': {
            'id': str(opc_diagram.pk),
            'title': opc_diagram.title,
            'partCode': opc_diagram.part_code,
            'revision': opc_diagram.revision,
            'graphVersion': opc_diagram.graph_version,
        },
        'parentWbsNode': str(parent_wbs_node.node_id),
        'summaryWbsName': f'OPC {opc_diagram.part_code} - {opc_diagram.title}',
        'quantity': str(quantity_decimal),
        'taskCount': len(tasks),
        'dependencyCount': len(dependencies),
        'totalDurationHours': str(total_hours.quantize(Decimal('0.01'))),
        'tasks': tasks,
        'dependencies': dependencies,
        'warnings': warnings,
    }


@transaction.atomic
def import_opc_to_wbs(
    *,
    revision: Revision,
    parent_wbs_node: WBSNodeVersion,
    opc_diagram: OPCDiagram,
    actor,
    quantity=1,
    expected_project_version=None,
    idempotency_key: str,
) -> ProjectOPCImport:
    idempotency_key = (idempotency_key or '').strip()
    if not idempotency_key:
        raise ValidationError({'idempotency_key': 'Idempotency key is required.'})

    revision = Revision.objects.select_for_update().select_related('project').get(pk=revision.pk)
    project = Project.objects.select_for_update().get(pk=revision.project_id)
    parent_wbs_node = WBSNodeVersion.objects.select_for_update().select_related('node', 'revision').get(pk=parent_wbs_node.pk)
    opc_diagram = OPCDiagram.objects.select_for_update().get(pk=opc_diagram.pk)

    existing = ProjectOPCImport.objects.filter(
        revision=revision,
        parent_wbs_node=parent_wbs_node,
        opc_diagram=opc_diagram,
        idempotency_key=idempotency_key,
    ).first()
    if existing:
        return existing

    _check_revision_is_open(revision, actor)
    if expected_project_version is not None and int(expected_project_version) != project.project_version:
        raise ValidationError({'project_version': 'Project version changed. Refresh the plan before importing OPC.'})

    preview = build_opc_wbs_preview(
        revision=revision,
        parent_wbs_node=parent_wbs_node,
        opc_diagram=opc_diagram,
        quantity=quantity,
    )
    operations = _operation_nodes(opc_diagram)

    max_wbs_seq = WBSNodeVersion.objects.filter(revision=revision, parent=parent_wbs_node, is_deleted=False).aggregate(Max('sequence'))['sequence__max'] or 0
    max_task_seq = TaskVersion.objects.filter(revision=revision, wbs_node=parent_wbs_node, is_deleted=False).aggregate(Max('sequence'))['sequence__max'] or 0
    summary_node = WBSNode.objects.create(project=project)
    summary_wbs = WBSNodeVersion.objects.create(
        node=summary_node,
        revision=revision,
        parent=parent_wbs_node,
        title=preview['summaryWbsName'],
        sequence=max(max_wbs_seq, max_task_seq) + 1,
    )

    operation_task_map = {}
    resource_assignment_map = {}
    task_by_node_id = {}
    for index, node in enumerate(operations, start=1):
        task = Task.objects.create(project=project, created_by=actor)
        duration = _operation_duration(node, Decimal(preview['quantity']))
        task_version = TaskVersion.objects.create(
            task=task,
            revision=revision,
            wbs_node=summary_wbs,
            title=f"OP{node.operation_number:03d} - {node.label}" if node.operation_number else node.label,
            duration_hours=duration,
            description=node.description or f'Imported from OPC {opc_diagram.part_code} / {opc_diagram.revision or opc_diagram.graph_version}',
            sequence=index,
        )
        operation_task_map[str(node.pk)] = {
            'taskId': str(task.pk),
            'taskVersionId': task_version.pk,
            'title': task_version.title,
        }
        task_by_node_id[str(node.pk)] = task

        resource, payload = _ensure_resource(node)
        if resource:
            assignment = Assignment.objects.create(
                revision=revision,
                task=task,
                resource=resource,
                units_percent=Decimal('100.00'),
                planned_hours=duration,
            )
            resource_assignment_map[str(node.pk)] = {
                'assignmentId': assignment.pk,
                'resourceId': resource.pk,
                'resourceCode': resource.code,
                'resourceName': resource.name,
                'source': payload['source'],
            }

    dependency_map = []
    for predecessor_id, successor_id, edge_id in _dependency_pairs(opc_diagram, operations):
        predecessor = task_by_node_id.get(predecessor_id)
        successor = task_by_node_id.get(successor_id)
        if predecessor and successor:
            dependency, _created = Dependency.objects.get_or_create(
                revision=revision,
                predecessor=predecessor,
                successor=successor,
                defaults={'dependency_type': 'FS', 'lag_hours': 0},
            )
            dependency_map.append({
                'dependencyId': dependency.pk,
                'predecessorOpcNodeId': predecessor_id,
                'successorOpcNodeId': successor_id,
                'sourceEdgeId': edge_id,
            })

    imported = ProjectOPCImport.objects.create(
        project=project,
        revision=revision,
        parent_wbs_node=parent_wbs_node,
        opc_diagram=opc_diagram,
        opc_graph_version=opc_diagram.graph_version,
        created_wbs_node=summary_wbs,
        idempotency_key=idempotency_key,
        operation_task_map=operation_task_map,
        resource_assignment_map=resource_assignment_map,
        dependency_map=dependency_map,
        warnings=preview['warnings'],
        created_by=actor,
    )
    Project.objects.filter(pk=project.pk).update(project_version=F('project_version') + 1)
    return imported
