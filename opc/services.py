from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from auditlog.services import log_event, model_to_dict_safe
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.models import ItemRevision
from enterprise_items.permissions import can_manage_engineering, is_engineering_releaser

from .models import OPCDiagram, OPCEdge, OPCNode

TRANSITIONS = {
    OPCDiagram.STATUS_DRAFT: {OPCDiagram.STATUS_UNDER_REVIEW},
    OPCDiagram.STATUS_UNDER_REVIEW: {OPCDiagram.STATUS_DRAFT, OPCDiagram.STATUS_APPROVED},
    OPCDiagram.STATUS_APPROVED: {OPCDiagram.STATUS_UNDER_REVIEW, OPCDiagram.STATUS_RELEASED},
    OPCDiagram.STATUS_RELEASED: {OPCDiagram.STATUS_SUPERSEDED, OPCDiagram.STATUS_OBSOLETE},
    OPCDiagram.STATUS_SUPERSEDED: {OPCDiagram.STATUS_OBSOLETE},
    OPCDiagram.STATUS_OBSOLETE: set(),
}
HEADER_FIELDS = {'item_revision', 'title', 'part_code', 'part_name', 'revision', 'description', 'effective_from', 'effective_to', 'status'}


def _require_manager(actor):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError('You do not have permission to manage OPC revisions.')


def _require_releaser(actor):
    if not is_engineering_releaser(actor):
        raise EngineeringPermissionError('You do not have permission to release OPC revisions.')


def _validate_transition(diagram: OPCDiagram, target_status: str):
    if target_status not in TRANSITIONS.get(diagram.status, set()):
        raise EngineeringLifecycleError({'status': f'Cannot transition OPC revision from {diagram.status} to {target_status}.'})


def _periods_overlap(a_start: date, a_end: date | None, b_start: date, b_end: date | None) -> bool:
    return a_start <= (b_end or date.max) and b_start <= (a_end or date.max)


def ensure_diagram_editable(diagram: OPCDiagram | None, attrs: dict | None = None):
    if not diagram or diagram.status not in OPCDiagram.LOCKED_STATUSES:
        return
    if attrs is None:
        raise EngineeringLifecycleError({'status': 'Released OPC revisions are immutable.'})
    changed = [field for field in HEADER_FIELDS if field in attrs and attrs[field] != getattr(diagram, field)]
    if changed:
        raise ValidationError({field: 'Released OPC revisions are immutable.' for field in changed})


def ensure_node_editable(node: OPCNode | None):
    if node and node.diagram.status in OPCDiagram.LOCKED_STATUSES:
        raise EngineeringLifecycleError({'diagram': 'Released OPC revisions are immutable.'})


def ensure_edge_editable(edge: OPCEdge | None):
    if edge and edge.diagram.status in OPCDiagram.LOCKED_STATUSES:
        raise EngineeringLifecycleError({'diagram': 'Released OPC revisions are immutable.'})


def _graph_cycle(nodes: Iterable[OPCNode], edges: Iterable[OPCEdge]) -> list[str]:
    adjacency: dict[str, list[str]] = {str(node.pk): [] for node in nodes}
    labels = {str(node.pk): node.label for node in nodes}
    for edge in edges:
        adjacency.setdefault(str(edge.source_id), []).append(str(edge.target_id))
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str, path: list[str]) -> list[str]:
        if node_id in visiting:
            idx = path.index(node_id) if node_id in path else 0
            return path[idx:] + [node_id]
        if node_id in visited:
            return []
        visiting.add(node_id)
        for target in adjacency.get(node_id, []):
            result = visit(target, [*path, target])
            if result:
                return result
        visiting.remove(node_id)
        visited.add(node_id)
        return []

    for node_id in adjacency:
        result = visit(node_id, [node_id])
        if result:
            return [labels.get(value, value) for value in result]
    return []


def validate_opc_graph(diagram: OPCDiagram) -> dict:
    errors: dict[str, str] = {}
    warnings: list[str] = []
    nodes = list(diagram.nodes.all())
    edges = list(diagram.edges.select_related('source', 'target'))
    if not diagram.item_revision_id:
        errors['item_revision'] = 'OPC revision must be assigned to an ItemRevision before release.'
    if not diagram.revision.strip():
        errors['revision'] = 'OPC revision code is required.'
    if diagram.item_revision and diagram.item_revision.status not in {ItemRevision.STATUS_APPROVED, ItemRevision.STATUS_RELEASED}:
        errors['item_revision'] = 'Released OPC requires an approved or released ItemRevision.'
    if not diagram.effective_from:
        errors['effective_from'] = 'Effective start date is required before release.'
    if not nodes:
        errors['nodes'] = 'OPC revision must contain at least one operation node.'
    operation_nodes = [node for node in nodes if node.node_type in {'OPERATION', 'INSPECTION', 'TRANSPORT'}]
    if not operation_nodes:
        errors['operation'] = 'OPC revision must contain at least one operation, inspection, or transport node.'
    edge_pairs = set()
    node_ids = {node.pk for node in nodes}
    incoming = {node.pk: 0 for node in nodes}
    outgoing = {node.pk: 0 for node in nodes}
    for edge in edges:
        if edge.source_id == edge.target_id:
            errors['edge'] = 'OPC edges cannot reference the same source and target node.'
        if edge.source_id not in node_ids or edge.target_id not in node_ids or edge.source.diagram_id != diagram.pk or edge.target.diagram_id != diagram.pk:
            errors['edge'] = 'Every OPC edge must reference nodes in the same OPC revision.'
        pair = (edge.source_id, edge.target_id)
        if pair in edge_pairs:
            errors['edge'] = 'Duplicate OPC edges are not allowed.'
        edge_pairs.add(pair)
        incoming[edge.target_id] = incoming.get(edge.target_id, 0) + 1
        outgoing[edge.source_id] = outgoing.get(edge.source_id, 0) + 1
    cycle = _graph_cycle(nodes, edges)
    if cycle:
        errors['cycle'] = 'OPC graph cycle detected: ' + ' -> '.join(cycle)
    if nodes and not [node for node in nodes if incoming.get(node.pk, 0) == 0]:
        errors['start'] = 'At least one start operation must be identifiable.'
    if nodes and not [node for node in nodes if outgoing.get(node.pk, 0) == 0]:
        errors['terminal'] = 'At least one terminal operation must be identifiable.'
    for node in nodes:
        if not node.label.strip():
            errors['node'] = 'Every OPC node requires a label.'
        for field in ('setup_time_hours', 'run_time_per_unit_hours', 'queue_time_hours', 'move_time_hours', 'inspection_time_hours', 'external_lead_time_days', 'buffer_time_hours'):
            if getattr(node, field) < 0:
                errors[field] = 'Duration values cannot be negative.'
        if node.execution_type == 'EXTERNAL' and node.external_lead_time_days <= 0:
            warnings.append(f'External operation {node.label} has no external lead time.')
    return {'valid': not errors, 'errors': errors, 'warnings': warnings}


def _validate_no_overlap(diagram: OPCDiagram, *, exclude_ids: Iterable[str] = ()): 
    if not diagram.effective_from or not diagram.item_revision_id:
        return
    released = OPCDiagram.objects.filter(item_revision=diagram.item_revision, status=OPCDiagram.STATUS_RELEASED).exclude(pk=diagram.pk)
    excluded = {str(value) for value in exclude_ids}
    if excluded:
        released = released.exclude(pk__in=excluded)
    for existing in released:
        if existing.effective_from and _periods_overlap(diagram.effective_from, diagram.effective_to, existing.effective_from, existing.effective_to):
            raise EngineeringLifecycleError({'effective_from': 'Released OPC revisions cannot have overlapping effective periods.'})


def validate_opc_for_release(diagram: OPCDiagram):
    result = validate_opc_graph(diagram)
    if result['errors']:
        raise EngineeringLifecycleError(result['errors'])
    _validate_no_overlap(diagram)


def _save_status(diagram: OPCDiagram, *, new_status: str, actor, update_fields: list[str] | None = None):
    old = diagram.status
    diagram.status = new_status
    fields = list(dict.fromkeys([*(update_fields or []), 'status', 'updated_by', 'updated_at']))
    diagram.updated_by = actor
    diagram.save(update_fields=fields)
    log_event(f'opc_revision_{new_status.lower()}', target=diagram, category='business', changes={'status': {'old': old, 'new': new_status}}, extra={'item_revision_id': str(diagram.item_revision_id) if diagram.item_revision_id else None, 'revision': diagram.revision})
    return diagram


@transaction.atomic
def submit_opc_revision(diagram: OPCDiagram, *, actor):
    _require_manager(actor)
    diagram = OPCDiagram.objects.select_for_update().get(pk=diagram.pk)
    _validate_transition(diagram, OPCDiagram.STATUS_UNDER_REVIEW)
    diagram.submitted_by = actor
    diagram.submitted_at = timezone.now()
    return _save_status(diagram, new_status=OPCDiagram.STATUS_UNDER_REVIEW, actor=actor, update_fields=['submitted_by', 'submitted_at'])


@transaction.atomic
def return_opc_revision_to_draft(diagram: OPCDiagram, *, actor):
    _require_manager(actor)
    diagram = OPCDiagram.objects.select_for_update().get(pk=diagram.pk)
    _validate_transition(diagram, OPCDiagram.STATUS_DRAFT)
    return _save_status(diagram, new_status=OPCDiagram.STATUS_DRAFT, actor=actor)


@transaction.atomic
def return_opc_revision_to_review(diagram: OPCDiagram, *, actor):
    _require_manager(actor)
    diagram = OPCDiagram.objects.select_for_update().get(pk=diagram.pk)
    _validate_transition(diagram, OPCDiagram.STATUS_UNDER_REVIEW)
    return _save_status(diagram, new_status=OPCDiagram.STATUS_UNDER_REVIEW, actor=actor)


@transaction.atomic
def approve_opc_revision(diagram: OPCDiagram, *, actor):
    _require_releaser(actor)
    diagram = OPCDiagram.objects.select_for_update().get(pk=diagram.pk)
    _validate_transition(diagram, OPCDiagram.STATUS_APPROVED)
    diagram.approved_by = actor
    diagram.approved_at = timezone.now()
    return _save_status(diagram, new_status=OPCDiagram.STATUS_APPROVED, actor=actor, update_fields=['approved_by', 'approved_at'])


@transaction.atomic
def release_opc_revision(diagram: OPCDiagram, *, actor, effective_from=None, supersede_current=False):
    _require_releaser(actor)
    diagram = OPCDiagram.objects.select_for_update().prefetch_related('nodes', 'edges').get(pk=diagram.pk)
    _validate_transition(diagram, OPCDiagram.STATUS_RELEASED)
    if effective_from is not None:
        diagram.effective_from = effective_from
    validate_opc_for_release(diagram)
    current = OPCDiagram.objects.select_for_update().filter(item_revision=diagram.item_revision, status=OPCDiagram.STATUS_RELEASED, effective_to__isnull=True).exclude(pk=diagram.pk).order_by('-released_at', '-created_at').first()
    if current and not supersede_current:
        raise EngineeringLifecycleError({'supersede': 'A currently released OPC revision exists. Use supersede.'})
    old_snapshot = model_to_dict_safe(diagram)
    now = timezone.now()
    if current:
        if not diagram.effective_from or not current.effective_from or diagram.effective_from <= current.effective_from:
            raise EngineeringLifecycleError({'effective_from': 'Superseding OPC revision must start after the current released revision starts.'})
        current.status = OPCDiagram.STATUS_SUPERSEDED
        current.effective_to = diagram.effective_from - timedelta(days=1)
        current.superseded_at = now
        current.superseded_by = diagram
        current.save(update_fields=['status', 'effective_to', 'superseded_at', 'superseded_by', 'updated_at'])
        log_event('opc_revision_superseded', target=current, category='business', changes={'status': {'old': OPCDiagram.STATUS_RELEASED, 'new': OPCDiagram.STATUS_SUPERSEDED}}, extra={'superseded_by': str(diagram.pk)})
    diagram.status = OPCDiagram.STATUS_RELEASED
    diagram.released_by = actor
    diagram.released_at = now
    diagram.updated_by = actor
    diagram.save(update_fields=['status', 'released_by', 'released_at', 'effective_from', 'updated_by', 'updated_at'])
    log_event('opc_revision_released', target=diagram, category='business', changes={'status': {'old': old_snapshot.get('status'), 'new': OPCDiagram.STATUS_RELEASED}}, extra={'item_revision_id': str(diagram.item_revision_id), 'revision': diagram.revision})
    return diagram


@transaction.atomic
def obsolete_opc_revision(diagram: OPCDiagram, *, actor):
    _require_releaser(actor)
    diagram = OPCDiagram.objects.select_for_update().get(pk=diagram.pk)
    _validate_transition(diagram, OPCDiagram.STATUS_OBSOLETE)
    return _save_status(diagram, new_status=OPCDiagram.STATUS_OBSOLETE, actor=actor)


@transaction.atomic
def clone_opc_revision(source: OPCDiagram, *, actor, revision_code: str):
    _require_manager(actor)
    source = OPCDiagram.objects.select_for_update().prefetch_related('nodes', 'edges').get(pk=source.pk)
    clone = OPCDiagram.objects.create(
        item_revision=source.item_revision,
        title=source.title,
        part_code=source.part_code,
        part_name=source.part_name,
        revision=revision_code,
        description=source.description,
        status=OPCDiagram.STATUS_DRAFT,
        created_by=actor,
        updated_by=actor,
    )
    node_map: dict[str, OPCNode] = {}
    for node in source.nodes.all():
        copied = OPCNode.objects.create(
            diagram=clone,
            node_type=node.node_type,
            label=node.label,
            part_code=node.part_code,
            process_code=node.process_code,
            station=node.station,
            execution_type=node.execution_type,
            setup_time_hours=node.setup_time_hours,
            run_time_per_unit_hours=node.run_time_per_unit_hours,
            queue_time_hours=node.queue_time_hours,
            move_time_hours=node.move_time_hours,
            inspection_time_hours=node.inspection_time_hours,
            external_lead_time_days=node.external_lead_time_days,
            buffer_time_hours=node.buffer_time_hours,
            description=node.description,
            sequence=node.sequence,
            x=node.x,
            y=node.y,
            meta=node.meta,
        )
        node_map[str(node.pk)] = copied
    for edge in source.edges.all():
        OPCEdge.objects.create(
            diagram=clone,
            source=node_map[str(edge.source_id)],
            target=node_map[str(edge.target_id)],
            edge_type=edge.edge_type,
            label=edge.label,
            sequence=edge.sequence,
            meta=edge.meta,
        )
    log_event('opc_revision_cloned', target=clone, category='business', extra={'source_revision_id': str(source.pk), 'revision': clone.revision})
    return clone
