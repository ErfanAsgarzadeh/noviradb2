from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from auditlog.services import log_event
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.models import BOMLine, ItemRevision
from enterprise_items.permissions import can_manage_engineering

from .models import (
    ControlledDocumentRevision,
    MachineAsset,
    OPCDiagram,
    OPCEdge,
    OPCNode,
    OPCNodeDocumentRequirement,
    OPCOperationMaterialAllocation,
    OPCToolingRequirement,
    Plant,
    ProcessDefinition,
    ToolingDefinition,
    WorkCenter,
)
from .resource_validation import validate_node_resource_assignments


GRAPH_HEADER_FIELDS = {
    'item_revision',
    'manufacturing_bom_revision',
    'title',
    'part_code',
    'part_name',
    'revision',
    'description',
    'effective_from',
    'effective_to',
}
NODE_FIELDS = {
    'node_type',
    'label',
    'part_code',
    'process_code',
    'station',
    'execution_type',
    'operation_number',
    'process_definition',
    'plant',
    'work_center',
    'machine_asset',
    'setup_time_hours',
    'run_time_per_unit_hours',
    'queue_time_hours',
    'move_time_hours',
    'inspection_time_hours',
    'external_lead_time_days',
    'buffer_time_hours',
    'description',
    'sequence',
    'x',
    'y',
    'meta',
}
EDGE_FIELDS = {'edge_type', 'label', 'sequence', 'meta'}
DECIMAL_NODE_FIELDS = {
    'setup_time_hours',
    'run_time_per_unit_hours',
    'queue_time_hours',
    'move_time_hours',
    'inspection_time_hours',
    'external_lead_time_days',
    'buffer_time_hours',
}


class OPCGraphConflict(Exception):
    code = 'graph_version_conflict'

    def __init__(self, *, diagram: OPCDiagram, expected_version: int):
        self.payload = {
            'code': self.code,
            'detail': 'The OPC diagram has been modified by another user.',
            'expected_version': expected_version,
            'current_version': diagram.graph_version,
            'diagram_id': str(diagram.pk),
        }
        super().__init__(self.payload['detail'])


@dataclass
class OPCGraphSaveResult:
    diagram: OPCDiagram
    node_id_map: dict[str, str]
    edge_id_map: dict[str, str]


def increment_graph_version(diagram: OPCDiagram | str | uuid.UUID, *, actor=None) -> OPCDiagram:
    diagram_id = diagram.pk if isinstance(diagram, OPCDiagram) else diagram
    update = {'graph_version': F('graph_version') + 1, 'updated_at': timezone.now()}
    if actor is not None:
        update['updated_by'] = actor
    OPCDiagram.objects.filter(pk=diagram_id).update(**update)
    return OPCDiagram.objects.get(pk=diagram_id)


def _uuid_or_error(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError({field: 'Use a valid UUID.'}) from exc


def _nullable_uuid(value: Any, field: str) -> uuid.UUID | None:
    if value in (None, ''):
        return None
    return _uuid_or_error(value, field)


def _normalize_header(diagram_payload: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for field in GRAPH_HEADER_FIELDS:
        if field not in diagram_payload:
            continue
        value = diagram_payload[field]
        if field == 'item_revision':
            attrs['item_revision_id'] = value or None
        elif field == 'manufacturing_bom_revision':
            attrs['manufacturing_bom_revision_id'] = value or None
        else:
            attrs[field] = value if value != '' or field not in {'effective_from', 'effective_to'} else None
    return attrs


def _normalize_node(payload: dict[str, Any]) -> dict[str, Any]:
    attrs = {
        'node_type': payload.get('type') or payload.get('node_type'),
        'label': payload.get('label', ''),
        'part_code': payload.get('part_code', ''),
        'process_code': payload.get('process_code', ''),
        'station': payload.get('station', ''),
        'execution_type': payload.get('execution_type', 'INTERNAL'),
        'operation_number': payload.get('operation_number', None) if payload.get('operation_number', None) not in ('', None) else None,
        'process_definition_id': _nullable_uuid(payload.get('process_definition'), 'process_definition'),
        'plant_id': _nullable_uuid(payload.get('plant'), 'plant'),
        'work_center_id': _nullable_uuid(payload.get('work_center'), 'work_center'),
        'machine_asset_id': _nullable_uuid(payload.get('machine_asset'), 'machine_asset'),
        'description': payload.get('description', ''),
        'sequence': payload.get('sequence', 1),
        'x': payload.get('x', 120),
        'y': payload.get('y', 120),
        'meta': payload.get('meta') or {},
    }
    if attrs['operation_number'] is not None:
        try:
            attrs['operation_number'] = int(attrs['operation_number'])
        except (TypeError, ValueError) as exc:
            raise ValidationError({'operation_number': 'Operation number must be an integer.'}) from exc
    for field in DECIMAL_NODE_FIELDS:
        attrs[field] = payload.get(field, 0) if payload.get(field, None) is not None else 0
    return attrs


def _normalize_tooling_requirements(payload: dict[str, Any], node_index: int) -> list[dict[str, Any]]:
    raw_requirements = payload.get('tooling_requirements') or []
    if not isinstance(raw_requirements, list):
        raise ValidationError({'nodes': {node_index: {'tooling_requirements': 'Tooling requirements must be a list.'}}})
    normalized: list[dict[str, Any]] = []
    for req_index, requirement in enumerate(raw_requirements):
        if not isinstance(requirement, dict):
            raise ValidationError({'nodes': {node_index: {'tooling_requirements': {req_index: 'Each tooling requirement must be an object.'}}}})
        normalized.append({
            'tooling_definition_id': _nullable_uuid(requirement.get('tooling_definition'), f'nodes.{node_index}.tooling_requirements.{req_index}.tooling_definition'),
            'quantity': requirement.get('quantity', 1),
            'mandatory': requirement.get('mandatory', True),
            'notes': requirement.get('notes', ''),
            'sequence': requirement.get('sequence', req_index + 1),
        })
    return normalized


def _normalize_material_allocations(payload: dict[str, Any], node_index: int) -> list[dict[str, Any]]:
    raw_allocations = payload.get('material_allocations') or []
    if not isinstance(raw_allocations, list):
        raise ValidationError({'nodes': {node_index: {'material_allocations': 'Material allocations must be a list.'}}})
    normalized: list[dict[str, Any]] = []
    for allocation_index, allocation in enumerate(raw_allocations):
        if not isinstance(allocation, dict):
            raise ValidationError({'nodes': {node_index: {'material_allocations': {allocation_index: 'Each material allocation must be an object.'}}}})
        normalized.append({
            'bom_line_id': _nullable_uuid(allocation.get('bom_line'), f'nodes.{node_index}.material_allocations.{allocation_index}.bom_line'),
            'component_item_revision_id': _uuid_or_error(allocation.get('component_item_revision'), f'nodes.{node_index}.material_allocations.{allocation_index}.component_item_revision'),
            'quantity': allocation.get('quantity', 1),
            'unit': allocation.get('unit') or 'EA',
            'scrap_percent': allocation.get('scrap_percent', 0),
            'allocation_type': allocation.get('allocation_type') or OPCOperationMaterialAllocation.TYPE_CONSUME,
            'issue_at_operation': allocation.get('issue_at_operation', True),
            'backflush': allocation.get('backflush', False),
            'sequence': allocation.get('sequence', allocation_index + 1),
            'notes': allocation.get('notes', ''),
        })
    return normalized


def _normalize_document_requirements(payload: dict[str, Any], node_index: int) -> list[dict[str, Any]]:
    raw_requirements = payload.get('document_requirements') or []
    if not isinstance(raw_requirements, list):
        raise ValidationError({'nodes': {node_index: {'document_requirements': 'Document requirements must be a list.'}}})
    normalized: list[dict[str, Any]] = []
    for requirement_index, requirement in enumerate(raw_requirements):
        if not isinstance(requirement, dict):
            raise ValidationError({'nodes': {node_index: {'document_requirements': {requirement_index: 'Each document requirement must be an object.'}}}})
        normalized.append({
            'document_revision_id': _uuid_or_error(requirement.get('document_revision'), f'nodes.{node_index}.document_requirements.{requirement_index}.document_revision'),
            'purpose': requirement.get('purpose') or OPCNodeDocumentRequirement.PURPOSE_WORK_INSTRUCTION,
            'mandatory': requirement.get('mandatory', True),
            'sequence': requirement.get('sequence', requirement_index + 1),
            'notes': requirement.get('notes', ''),
        })
    return normalized


def _normalize_edge(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        'edge_type': payload.get('type') or payload.get('edge_type') or 'FLOW',
        'label': payload.get('label', ''),
        'sequence': payload.get('sequence', 1),
        'meta': payload.get('meta') or {},
    }


def _validate_actor(actor):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError('You do not have permission to manage OPC revisions.')


def _validate_request_shape(payload: dict[str, Any]) -> tuple[int, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if 'expected_version' not in payload:
        raise ValidationError({'expected_version': 'This field is required.'})
    try:
        expected_version = int(payload['expected_version'])
    except (TypeError, ValueError) as exc:
        raise ValidationError({'expected_version': 'Expected version must be an integer.'}) from exc
    if expected_version < 0:
        raise ValidationError({'expected_version': 'Expected version cannot be negative.'})
    diagram_payload = payload.get('diagram')
    if not isinstance(diagram_payload, dict):
        raise ValidationError({'diagram': 'This object is required.'})
    nodes = payload.get('nodes')
    edges = payload.get('edges')
    if not isinstance(nodes, list):
        raise ValidationError({'nodes': 'This list is required.'})
    if not isinstance(edges, list):
        raise ValidationError({'edges': 'This list is required.'})
    return expected_version, diagram_payload, nodes, edges


def _validate_node_id_sets(nodes_payload: list[dict[str, Any]], existing_nodes: dict[str, OPCNode], diagram: OPCDiagram | None, *, creating_diagram: bool = False):
    seen_ids: set[str] = set()
    seen_client_ids: set[str] = set()
    seen_operation_numbers: set[int] = set()
    normalized: list[tuple[str, str | None, uuid.UUID | None, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]] = []
    for index, node_payload in enumerate(nodes_payload):
        if not isinstance(node_payload, dict):
            raise ValidationError({'nodes': {index: 'Each node must be an object.'}})
        node_id_value = node_payload.get('id') or None
        client_id = (node_payload.get('client_id') or '').strip()
        if not node_id_value and not client_id:
            raise ValidationError({'nodes': {index: 'Each node requires id or client_id.'}})
        node_uuid = _uuid_or_error(node_id_value, f'nodes.{index}.id') if node_id_value else None
        if node_uuid:
            node_key = str(node_uuid)
            if node_key in seen_ids:
                raise ValidationError({'nodes': {index: 'Duplicate node id.'}})
            seen_ids.add(node_key)
            if node_key not in existing_nodes:
                raise ValidationError({'nodes': {index: 'Persisted node must belong to this OPC diagram.'}})
        if client_id:
            if client_id in seen_client_ids:
                raise ValidationError({'nodes': {index: 'Duplicate node client_id.'}})
            seen_client_ids.add(client_id)
        attrs = _normalize_node(node_payload)
        operation_number = attrs.get('operation_number')
        if operation_number is not None:
            if operation_number in seen_operation_numbers:
                raise ValidationError({'nodes': {index: {'operation_number': 'Operation number must be unique within the OPC diagram.'}}})
            seen_operation_numbers.add(operation_number)
        tooling_payloads = _normalize_tooling_requirements(node_payload, index)
        material_payloads = _normalize_material_allocations(node_payload, index)
        document_payloads = _normalize_document_requirements(node_payload, index)
        candidate = OPCNode(diagram=diagram)
        if node_uuid:
            candidate.pk = node_uuid
        for field, value in attrs.items():
            setattr(candidate, field, value)
        try:
            excluded = ['process_definition', 'plant', 'work_center', 'machine_asset']
            if creating_diagram or diagram is None:
                excluded.append('diagram')
            candidate.full_clean(exclude=excluded, validate_unique=False, validate_constraints=False)
        except DjangoValidationError as exc:
            raise ValidationError({'nodes': {index: exc.message_dict if hasattr(exc, 'message_dict') else exc.messages}}) from exc
        normalized.append((client_id or str(node_uuid), client_id or None, node_uuid, attrs, tooling_payloads, material_payloads, document_payloads))
    return normalized


def _validate_edges(
    edges_payload: list[dict[str, Any]],
    existing_edges: dict[str, OPCEdge],
    endpoint_refs: set[str],
):
    seen_ids: set[str] = set()
    seen_client_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    normalized: list[tuple[str, str | None, uuid.UUID | None, str, str, dict[str, Any]]] = []
    for index, edge_payload in enumerate(edges_payload):
        if not isinstance(edge_payload, dict):
            raise ValidationError({'edges': {index: 'Each edge must be an object.'}})
        edge_id_value = edge_payload.get('id') or None
        client_id = (edge_payload.get('client_id') or '').strip()
        if not edge_id_value and not client_id:
            raise ValidationError({'edges': {index: 'Each edge requires id or client_id.'}})
        edge_uuid = _uuid_or_error(edge_id_value, f'edges.{index}.id') if edge_id_value else None
        if edge_uuid:
            edge_key = str(edge_uuid)
            if edge_key in seen_ids:
                raise ValidationError({'edges': {index: 'Duplicate edge id.'}})
            seen_ids.add(edge_key)
            if edge_key not in existing_edges:
                raise ValidationError({'edges': {index: 'Persisted edge must belong to this OPC diagram.'}})
        if client_id:
            if client_id in seen_client_ids:
                raise ValidationError({'edges': {index: 'Duplicate edge client_id.'}})
            seen_client_ids.add(client_id)
        source = str(edge_payload.get('source') or '')
        target = str(edge_payload.get('target') or '')
        if not source or source not in endpoint_refs:
            raise ValidationError({'edges': {index: {'source': 'Unknown source node reference.'}}})
        if not target or target not in endpoint_refs:
            raise ValidationError({'edges': {index: {'target': 'Unknown target node reference.'}}})
        if source == target:
            raise ValidationError({'edges': {index: {'target': 'Source and target cannot be the same node.'}}})
        pair = (source, target)
        if pair in seen_pairs:
            raise ValidationError({'edges': {index: 'Duplicate OPC edges are not allowed.'}})
        seen_pairs.add(pair)
        attrs = _normalize_edge(edge_payload)
        candidate = existing_edges[str(edge_uuid)] if edge_uuid else OPCEdge()
        for field, value in attrs.items():
            setattr(candidate, field, value)
        try:
            candidate.full_clean(exclude=['diagram', 'source', 'target'])
        except DjangoValidationError as exc:
            raise ValidationError({'edges': {index: exc.message_dict if hasattr(exc, 'message_dict') else exc.messages}}) from exc
        normalized.append((client_id or str(edge_uuid), client_id or None, edge_uuid, source, target, attrs))
    return normalized


def _raise_fault(fault_at: str | None, marker: str):
    if fault_at == marker:
        raise RuntimeError(f'Injected OPC graph persistence fault at {marker}.')


@transaction.atomic
def save_opc_graph(*, diagram_id: str, payload: dict[str, Any], actor, fault_at: str | None = None, lock_acquired=None) -> OPCGraphSaveResult:
    _validate_actor(actor)
    expected_version, diagram_payload, nodes_payload, edges_payload = _validate_request_shape(payload)
    diagram_uuid = _uuid_or_error(diagram_id, 'diagram_id')

    diagram = (
        OPCDiagram.objects.select_for_update(of=('self',))
        .select_related('item_revision')
        .filter(pk=diagram_uuid)
        .first()
    )
    creating = diagram is None
    if creating and expected_version != 0:
        raise ValidationError({'expected_version': 'New OPC graph saves must use expected_version 0.'})
    if creating:
        diagram = OPCDiagram(id=diagram_uuid, status=OPCDiagram.STATUS_DRAFT, created_by=actor, updated_by=actor)
    elif diagram.graph_version != expected_version:
        raise OPCGraphConflict(diagram=diagram, expected_version=expected_version)
    elif diagram.status in OPCDiagram.LOCKED_STATUSES:
        raise EngineeringLifecycleError({'status': 'Released OPC revisions are immutable.'})
    if lock_acquired is not None:
        lock_acquired()

    header_attrs = _normalize_header(diagram_payload)
    if 'item_revision_id' in header_attrs and header_attrs['item_revision_id']:
        if not ItemRevision.objects.filter(pk=header_attrs['item_revision_id']).exists():
            raise ValidationError({'diagram': {'item_revision': 'Invalid ItemRevision.'}})
    if 'manufacturing_bom_revision_id' in header_attrs and header_attrs['manufacturing_bom_revision_id']:
        from enterprise_items.models import BOMRevision
        if not BOMRevision.objects.filter(pk=header_attrs['manufacturing_bom_revision_id']).exists():
            raise ValidationError({'diagram': {'manufacturing_bom_revision': 'Invalid manufacturing BOM revision.'}})
    for field, value in header_attrs.items():
        setattr(diagram, field, value)
    diagram.updated_by = actor
    if creating:
        diagram.created_by = actor
    try:
        diagram.full_clean()
    except DjangoValidationError as exc:
        raise ValidationError({'diagram': exc.message_dict if hasattr(exc, 'message_dict') else exc.messages}) from exc

    existing_nodes = {
        str(node.pk): node
        for node in diagram.nodes.select_related('process_definition', 'plant', 'work_center', 'machine_asset')
    } if not creating else {}
    existing_edges = {str(edge.pk): edge for edge in diagram.edges.select_related('source', 'target')} if not creating else {}
    normalized_nodes = _validate_node_id_sets(nodes_payload, existing_nodes, diagram, creating_diagram=creating)
    plant_ids = {attrs['plant_id'] for _key, _client_id, _node_uuid, attrs, _tooling, _materials, _documents in normalized_nodes if attrs.get('plant_id')}
    work_center_ids = {attrs['work_center_id'] for _key, _client_id, _node_uuid, attrs, _tooling, _materials, _documents in normalized_nodes if attrs.get('work_center_id')}
    machine_ids = {attrs['machine_asset_id'] for _key, _client_id, _node_uuid, attrs, _tooling, _materials, _documents in normalized_nodes if attrs.get('machine_asset_id')}
    process_ids = {attrs['process_definition_id'] for _key, _client_id, _node_uuid, attrs, _tooling, _materials, _documents in normalized_nodes if attrs.get('process_definition_id')}
    tooling_ids = {requirement['tooling_definition_id'] for _key, _client_id, _node_uuid, _attrs, tooling, _materials, _documents in normalized_nodes for requirement in tooling if requirement.get('tooling_definition_id')}
    bom_line_ids = {allocation['bom_line_id'] for _key, _client_id, _node_uuid, _attrs, _tooling, materials, _documents in normalized_nodes for allocation in materials if allocation.get('bom_line_id')}
    component_ids = {allocation['component_item_revision_id'] for _key, _client_id, _node_uuid, _attrs, _tooling, materials, _documents in normalized_nodes for allocation in materials}
    document_revision_ids = {requirement['document_revision_id'] for _key, _client_id, _node_uuid, _attrs, _tooling, _materials, documents in normalized_nodes for requirement in documents}
    plants = Plant.objects.in_bulk(plant_ids)
    work_centers = WorkCenter.objects.select_related('plant').in_bulk(work_center_ids)
    machines = MachineAsset.objects.select_related('plant', 'work_center').in_bulk(machine_ids)
    processes = ProcessDefinition.objects.in_bulk(process_ids)
    tooling_definitions = ToolingDefinition.objects.in_bulk(tooling_ids)
    bom_lines = BOMLine.objects.select_related('bom_revision__bom', 'component_item_revision__item').in_bulk(bom_line_ids)
    components = ItemRevision.objects.select_related('item').in_bulk(component_ids)
    document_revisions = ControlledDocumentRevision.objects.select_related('document').in_bulk(document_revision_ids)
    existing_tooling_by_node: dict[str, dict[uuid.UUID, OPCToolingRequirement]] = {}
    if existing_nodes:
        for requirement in OPCToolingRequirement.objects.select_related('tooling_definition').filter(node_id__in=existing_nodes.keys()):
            existing_tooling_by_node.setdefault(str(requirement.node_id), {})[requirement.tooling_definition_id] = requirement
    existing_allocations_by_node: dict[str, dict[tuple[uuid.UUID | None, uuid.UUID, str], OPCOperationMaterialAllocation]] = {}
    existing_documents_by_node: dict[str, dict[tuple[uuid.UUID, str], OPCNodeDocumentRequirement]] = {}
    if existing_nodes:
        for allocation in OPCOperationMaterialAllocation.objects.filter(node_id__in=existing_nodes.keys()):
            existing_allocations_by_node.setdefault(str(allocation.node_id), {})[(allocation.bom_line_id, allocation.component_item_revision_id, allocation.allocation_type)] = allocation
        for requirement in OPCNodeDocumentRequirement.objects.filter(node_id__in=existing_nodes.keys()):
            existing_documents_by_node.setdefault(str(requirement.node_id), {})[(requirement.document_revision_id, requirement.purpose)] = requirement
    for index, (_key, _client_id, node_uuid, attrs, tooling, materials, documents) in enumerate(normalized_nodes):
        existing_node = existing_nodes[str(node_uuid)] if node_uuid else None
        validate_node_resource_assignments(
            index=index,
            attrs=attrs,
            tooling_payloads=tooling,
            existing_node=existing_node,
            plants=plants,
            work_centers=work_centers,
            machines=machines,
            processes=processes,
            tooling_definitions=tooling_definitions,
            existing_tooling=existing_tooling_by_node.get(str(node_uuid), {}) if node_uuid else {},
        )
        for allocation in materials:
            bom_line = bom_lines.get(allocation.get('bom_line_id')) if allocation.get('bom_line_id') else None
            component = components.get(allocation['component_item_revision_id'])
            if not component:
                raise ValidationError({'nodes': {index: {'material_allocations': 'Invalid component ItemRevision.'}}})
            if allocation.get('bom_line_id') and not bom_line:
                raise ValidationError({'nodes': {index: {'material_allocations': 'Invalid MBOM line.'}}})
            if bom_line and bom_line.component_item_revision_id != component.pk:
                raise ValidationError({'nodes': {index: {'material_allocations': 'Component revision must match the selected MBOM line.'}}})
            if bom_line and diagram.item_revision_id and bom_line.bom_revision.bom.parent_item_revision_id != diagram.item_revision_id:
                raise ValidationError({'nodes': {index: {'material_allocations': 'MBOM line must belong to this OPC ItemRevision.'}}})
        for requirement in documents:
            revision = document_revisions.get(requirement['document_revision_id'])
            if not revision:
                raise ValidationError({'nodes': {index: {'document_requirements': 'Invalid document revision.'}}})
            if revision.status != ControlledDocumentRevision.STATUS_RELEASED:
                raise ValidationError({'nodes': {index: {'document_requirements': 'Document requirements must reference released document revisions.'}}})
    endpoint_refs = {key for key, _client_id, node_uuid, _attrs, _tooling, _materials, _documents in normalized_nodes}
    if len(endpoint_refs) != len(normalized_nodes):
        raise ValidationError({'nodes': 'Node references must be unique.'})
    normalized_edges = _validate_edges(edges_payload, existing_edges, endpoint_refs)

    _raise_fault(fault_at, 'before_diagram_save')
    diagram.graph_version = expected_version + 1
    diagram.save()
    _raise_fault(fault_at, 'after_diagram_save')

    submitted_node_ids = {str(node_uuid) for _key, _client_id, node_uuid, _attrs, _tooling, _materials, _documents in normalized_nodes if node_uuid}
    submitted_edge_ids = {str(edge_uuid) for _key, _client_id, edge_uuid, _source, _target, _attrs in normalized_edges if edge_uuid}
    node_ref_map: dict[str, OPCNode] = {}
    node_id_map: dict[str, str] = {}
    counts = {
        'nodes_created': 0,
        'nodes_updated': 0,
        'nodes_deleted': max(0, len(existing_nodes) - len(submitted_node_ids)),
        'edges_created': 0,
        'edges_updated': 0,
        'edges_deleted': max(0, len(existing_edges) - len(submitted_edge_ids)),
        'process_assignments_changed': 0,
        'plant_assignments_changed': 0,
        'work_center_assignments_changed': 0,
        'machine_assignments_changed': 0,
        'operation_numbers_changed': 0,
        'tooling_requirements_created': 0,
        'tooling_requirements_updated': 0,
        'tooling_requirements_removed': 0,
        'material_allocations_created': 0,
        'material_allocations_updated': 0,
        'material_allocations_removed': 0,
        'document_requirements_created': 0,
        'document_requirements_updated': 0,
        'document_requirements_removed': 0,
    }

    obsolete_edges = [edge for edge_id, edge in existing_edges.items() if edge_id not in submitted_edge_ids]
    for edge in obsolete_edges:
        edge.delete()
    _raise_fault(fault_at, 'after_edge_delete')

    obsolete_nodes = [node for node_id, node in existing_nodes.items() if node_id not in submitted_node_ids]
    for node in obsolete_nodes:
        node.delete()
    _raise_fault(fault_at, 'after_node_delete')

    for key, client_id, node_uuid, attrs, _tooling, _materials, _documents in normalized_nodes:
        node = existing_nodes[str(node_uuid)] if node_uuid else OPCNode(diagram=diagram)
        if node_uuid:
            if node.process_definition_id != attrs.get('process_definition_id'):
                counts['process_assignments_changed'] += 1
            if node.plant_id != attrs.get('plant_id'):
                counts['plant_assignments_changed'] += 1
            if node.work_center_id != attrs.get('work_center_id'):
                counts['work_center_assignments_changed'] += 1
            if node.machine_asset_id != attrs.get('machine_asset_id'):
                counts['machine_assignments_changed'] += 1
            if node.operation_number != attrs.get('operation_number'):
                counts['operation_numbers_changed'] += 1
        else:
            counts['process_assignments_changed'] += int(bool(attrs.get('process_definition_id')))
            counts['plant_assignments_changed'] += int(bool(attrs.get('plant_id')))
            counts['work_center_assignments_changed'] += int(bool(attrs.get('work_center_id')))
            counts['machine_assignments_changed'] += int(bool(attrs.get('machine_asset_id')))
            counts['operation_numbers_changed'] += int(attrs.get('operation_number') is not None)
        for field, value in attrs.items():
            setattr(node, field, value)
        node.diagram = diagram
        node.save()
        if node_uuid:
            counts['nodes_updated'] += 1
        else:
            counts['nodes_created'] += 1
        node_ref_map[key] = node
        node_ref_map[str(node.pk)] = node
        if client_id:
            node_id_map[client_id] = str(node.pk)
    _raise_fault(fault_at, 'after_node_upsert')

    for key, _client_id, node_uuid, _attrs, tooling_payloads, material_payloads, document_payloads in normalized_nodes:
        node = node_ref_map[key]
        existing_for_node = existing_tooling_by_node.get(str(node_uuid), {}) if node_uuid else {}
        submitted_tooling_ids = {requirement['tooling_definition_id'] for requirement in tooling_payloads if requirement.get('tooling_definition_id')}
        obsolete_tooling = [requirement for tooling_id, requirement in existing_for_node.items() if tooling_id not in submitted_tooling_ids]
        counts['tooling_requirements_removed'] += len(obsolete_tooling)
        for requirement in obsolete_tooling:
            requirement.delete()
        for requirement_payload in tooling_payloads:
            tooling_id = requirement_payload['tooling_definition_id']
            requirement = existing_for_node.get(tooling_id) or OPCToolingRequirement(node=node, tooling_definition_id=tooling_id)
            for field in ('quantity', 'mandatory', 'notes', 'sequence'):
                setattr(requirement, field, requirement_payload[field])
            requirement.node = node
            requirement.tooling_definition_id = tooling_id
            requirement.save()
            if tooling_id in existing_for_node:
                counts['tooling_requirements_updated'] += 1
            else:
                counts['tooling_requirements_created'] += 1
        existing_materials = existing_allocations_by_node.get(str(node_uuid), {}) if node_uuid else {}
        submitted_material_keys = {(allocation.get('bom_line_id'), allocation['component_item_revision_id'], allocation['allocation_type']) for allocation in material_payloads}
        obsolete_materials = [allocation for allocation_key, allocation in existing_materials.items() if allocation_key not in submitted_material_keys]
        counts['material_allocations_removed'] += len(obsolete_materials)
        for allocation in obsolete_materials:
            allocation.delete()
        for allocation_payload in material_payloads:
            key_tuple = (allocation_payload.get('bom_line_id'), allocation_payload['component_item_revision_id'], allocation_payload['allocation_type'])
            allocation = existing_materials.get(key_tuple) or OPCOperationMaterialAllocation(node=node)
            for field in ('bom_line_id', 'component_item_revision_id', 'quantity', 'unit', 'scrap_percent', 'allocation_type', 'issue_at_operation', 'backflush', 'sequence', 'notes'):
                setattr(allocation, field, allocation_payload[field])
            allocation.node = node
            allocation.save()
            if key_tuple in existing_materials:
                counts['material_allocations_updated'] += 1
            else:
                counts['material_allocations_created'] += 1
        existing_documents = existing_documents_by_node.get(str(node_uuid), {}) if node_uuid else {}
        submitted_document_keys = {(requirement['document_revision_id'], requirement['purpose']) for requirement in document_payloads}
        obsolete_documents = [requirement for requirement_key, requirement in existing_documents.items() if requirement_key not in submitted_document_keys]
        counts['document_requirements_removed'] += len(obsolete_documents)
        for requirement in obsolete_documents:
            requirement.delete()
        for requirement_payload in document_payloads:
            key_tuple = (requirement_payload['document_revision_id'], requirement_payload['purpose'])
            requirement = existing_documents.get(key_tuple) or OPCNodeDocumentRequirement(node=node)
            for field in ('document_revision_id', 'purpose', 'mandatory', 'sequence', 'notes'):
                setattr(requirement, field, requirement_payload[field])
            requirement.node = node
            requirement.save()
            if key_tuple in existing_documents:
                counts['document_requirements_updated'] += 1
            else:
                counts['document_requirements_created'] += 1
    _raise_fault(fault_at, 'after_tooling_upsert')

    edge_id_map: dict[str, str] = {}
    try:
        for _key, client_id, edge_uuid, source_ref, target_ref, attrs in normalized_edges:
            edge = existing_edges[str(edge_uuid)] if edge_uuid else OPCEdge(diagram=diagram)
            for field, value in attrs.items():
                setattr(edge, field, value)
            edge.diagram = diagram
            edge.source = node_ref_map[source_ref]
            edge.target = node_ref_map[target_ref]
            edge.save()
            if edge_uuid:
                counts['edges_updated'] += 1
            else:
                counts['edges_created'] += 1
            if client_id:
                edge_id_map[client_id] = str(edge.pk)
    except IntegrityError as exc:
        raise ValidationError({'edges': 'Duplicate OPC edges are not allowed.'}) from exc
    _raise_fault(fault_at, 'after_edge_upsert')

    log_event(
        'opc_graph_saved',
        target=diagram,
        category='business',
        changes={'graph_version': {'old': expected_version, 'new': diagram.graph_version}},
        extra={
            'previous_graph_version': expected_version,
            'new_graph_version': diagram.graph_version,
            **counts,
        },
    )
    _raise_fault(fault_at, 'after_audit')

    refreshed = (
        OPCDiagram.objects.select_related('item_revision__item', 'submitted_by', 'approved_by', 'released_by', 'superseded_by')
        .prefetch_related('nodes__tooling_requirements__tooling_definition', 'nodes__material_allocations__bom_line__component_item_revision__item', 'nodes__document_requirements__document_revision__document', 'edges')
        .get(pk=diagram.pk)
    )
    return OPCGraphSaveResult(diagram=refreshed, node_id_map=node_id_map, edge_id_map=edge_id_map)
