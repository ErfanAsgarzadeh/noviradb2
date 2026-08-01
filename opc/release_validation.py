from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

from django.utils import timezone

from enterprise_items.models import ItemRevision

from .models import ControlledDocumentRevision, OPCDiagram, OPCEdge, OPCNode, ProcessDefinition
from .resource_validation import execution_bucket, is_operation_capable

POLICY_VERSION = 'opc-release-v1'
VALIDATION_MODES = {'draft', 'release'}
NORMAL_EDGE_TYPES = {'FLOW', 'OPTIONAL'}
REWORK_EDGE_TYPE = 'REWORK'
REWORK_ALLOWED_SOURCES = {'INSPECTION', 'OPERATION'}
REWORK_ALLOWED_TARGETS = {'OPERATION', 'INSPECTION'}
ACKNOWLEDGEMENT_REQUIRED_WARNING_CODES = {'external_missing_lead_time'}

SEVERITY_ORDER = {'ERROR': 0, 'WARNING': 1, 'INFO': 2}
SCOPE_ORDER = {'DIAGRAM': 0, 'NODE': 1, 'EDGE': 2, 'TOOLING_REQUIREMENT': 3}


class OPCValidationConflict(Exception):
    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(payload.get('detail', 'OPC validation conflict.'))


@dataclass(frozen=True)
class ValidationIssueInput:
    code: str
    severity: str
    scope: str
    message: str
    target_id: str
    target_label: str
    field: str = ''
    acknowledgeable: bool = False
    blocking: bool | None = None
    targets: tuple[dict, ...] = ()
    details: dict | None = None
    sort_index: int = 0


def _node_target(node: OPCNode) -> dict:
    return {'scope': 'NODE', 'id': str(node.pk), 'label': node.label}


def _edge_target(edge: OPCEdge) -> dict:
    return {'scope': 'EDGE', 'id': str(edge.pk), 'label': edge.label or f'{edge.source.label} -> {edge.target.label}'}


def _diagram_target(diagram: OPCDiagram) -> dict:
    return {'scope': 'DIAGRAM', 'id': str(diagram.pk), 'label': diagram.title}


def _sorted_nodes(diagram: OPCDiagram) -> list[OPCNode]:
    return list(
        diagram.nodes
        .select_related('process_definition', 'plant', 'work_center', 'machine_asset')
        .prefetch_related('tooling_requirements__tooling_definition', 'material_allocations__bom_line__component_item_revision__item', 'material_allocations__component_item_revision__item', 'document_requirements__document_revision__document')
        .order_by('sequence', 'created_at', 'id')
    )


def _sorted_edges(diagram: OPCDiagram) -> list[OPCEdge]:
    return list(diagram.edges.select_related('source', 'target').order_by('sequence', 'created_at', 'id'))


def _stable_issue_key(diagram: OPCDiagram, issue: ValidationIssueInput) -> str:
    field = issue.field or 'general'
    return f'{POLICY_VERSION}:{diagram.graph_version}:{issue.code}:{issue.scope}:{issue.target_id}:{field}'


def _issue_dict(diagram: OPCDiagram, issue: ValidationIssueInput, acknowledged: set[str]) -> dict:
    key = _stable_issue_key(diagram, issue)
    blocking = issue.severity == 'ERROR' if issue.blocking is None else issue.blocking
    acknowledgement_required = issue.code in ACKNOWLEDGEMENT_REQUIRED_WARNING_CODES
    is_acknowledged = issue.acknowledgeable and key in acknowledged
    return {
        'issue_key': key,
        'code': issue.code,
        'severity': issue.severity,
        'scope': issue.scope,
        'message': issue.message,
        'blocking': blocking,
        'acknowledgeable': issue.acknowledgeable,
        'acknowledgement_required': acknowledgement_required,
        'acknowledged': is_acknowledged,
        'target_id': issue.target_id,
        'target_label': issue.target_label,
        'field': issue.field,
        'targets': list(issue.targets) or [{'scope': issue.scope, 'id': issue.target_id, 'label': issue.target_label}],
        'details': issue.details or {},
        '_sort': [
            SEVERITY_ORDER.get(issue.severity, 9),
            SCOPE_ORDER.get(issue.scope, 9),
            issue.sort_index,
            issue.code,
            issue.target_label,
            issue.target_id,
            issue.field,
        ],
    }


def _add(
    issues: list[ValidationIssueInput],
    *,
    code: str,
    severity: str,
    scope: str,
    message: str,
    target: dict,
    field: str = '',
    acknowledgeable: bool = False,
    blocking: bool | None = None,
    targets: Iterable[dict] = (),
    details: dict | None = None,
    sort_index: int = 0,
):
    issues.append(
        ValidationIssueInput(
            code=code,
            severity=severity,
            scope=scope,
            message=message,
            target_id=str(target['id']),
            target_label=str(target.get('label') or target['id']),
            field=field,
            acknowledgeable=acknowledgeable,
            blocking=blocking,
            targets=tuple(targets),
            details=details,
            sort_index=sort_index,
        )
    )


def _normal_adjacency(nodes: list[OPCNode], edges: list[OPCEdge]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    node_ids = {str(node.pk) for node in nodes}
    adjacency = {node_id: [] for node_id in node_ids}
    reverse = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.edge_type not in NORMAL_EDGE_TYPES:
            continue
        source_id = str(edge.source_id)
        target_id = str(edge.target_id)
        adjacency.setdefault(source_id, []).append(target_id)
        reverse.setdefault(target_id, []).append(source_id)
    return adjacency, reverse


def _reachable(start_ids: Iterable[str], adjacency: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    queue = deque(start_ids)
    while queue:
        node_id = queue.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)
        queue.extend(adjacency.get(node_id, []))
    return seen


def _normal_cycle(nodes: list[OPCNode], edges: list[OPCEdge]) -> list[str]:
    adjacency, _reverse = _normal_adjacency(nodes, edges)
    labels = {str(node.pk): node.label for node in nodes}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str, path: list[str]) -> list[str]:
        if node_id in visiting:
            idx = path.index(node_id) if node_id in path else 0
            return path[idx:] + [node_id]
        if node_id in visited:
            return []
        visiting.add(node_id)
        for target_id in sorted(adjacency.get(node_id, []), key=lambda value: labels.get(value, value)):
            result = visit(target_id, [*path, target_id])
            if result:
                return result
        visiting.remove(node_id)
        visited.add(node_id)
        return []

    for node in sorted(nodes, key=lambda value: (value.sequence, value.label, str(value.pk))):
        result = visit(str(node.pk), [str(node.pk)])
        if result:
            return [labels.get(value, value) for value in result]
    return []


def _strong_components(nodes: list[OPCNode], edges: list[OPCEdge]) -> list[set[str]]:
    adjacency: dict[str, list[str]] = {str(node.pk): [] for node in nodes}
    for edge in edges:
        adjacency.setdefault(str(edge.source_id), []).append(str(edge.target_id))
    index = 0
    stack: list[str] = []
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def strongconnect(node_id: str):
        nonlocal index
        indexes[node_id] = index
        lowlinks[node_id] = index
        index += 1
        stack.append(node_id)
        on_stack.add(node_id)
        for target_id in adjacency.get(node_id, []):
            if target_id not in indexes:
                strongconnect(target_id)
                lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target_id])
            elif target_id in on_stack:
                lowlinks[node_id] = min(lowlinks[node_id], indexes[target_id])
        if lowlinks[node_id] == indexes[node_id]:
            component: set[str] = set()
            while stack:
                value = stack.pop()
                on_stack.remove(value)
                component.add(value)
                if value == node_id:
                    break
            components.append(component)

    for node in sorted(nodes, key=lambda value: (value.sequence, value.label, str(value.pk))):
        node_id = str(node.pk)
        if node_id not in indexes:
            strongconnect(node_id)
    return components


def _validate_header(diagram: OPCDiagram, issues: list[ValidationIssueInput]):
    target = _diagram_target(diagram)
    if not diagram.item_revision_id:
        _add(issues, code='missing_item_revision', severity='ERROR', scope='DIAGRAM', field='item_revision', target=target, message='OPC revision must be assigned to an ItemRevision before release.')
    elif diagram.item_revision.status not in {ItemRevision.STATUS_APPROVED, ItemRevision.STATUS_RELEASED}:
        _add(issues, code='item_revision_not_release_ready', severity='ERROR', scope='DIAGRAM', field='item_revision', target=target, message='Released OPC requires an approved or released ItemRevision.')
    if not diagram.revision.strip():
        _add(issues, code='missing_revision_code', severity='ERROR', scope='DIAGRAM', field='revision', target=target, message='OPC revision code is required.')
    if not diagram.effective_from:
        _add(issues, code='missing_effective_from', severity='ERROR', scope='DIAGRAM', field='effective_from', target=target, message='Effective start date is required before release.')


def _validate_topology(diagram: OPCDiagram, nodes: list[OPCNode], edges: list[OPCEdge], issues: list[ValidationIssueInput]):
    diagram_target = _diagram_target(diagram)
    if not nodes:
        _add(issues, code='missing_nodes', severity='ERROR', scope='DIAGRAM', field='nodes', target=diagram_target, message='OPC revision must contain at least one operation node.')
        return
    operation_nodes = [node for node in nodes if node.node_type in {'OPERATION', 'INSPECTION', 'TRANSPORT'}]
    if not operation_nodes:
        _add(issues, code='missing_operation_node', severity='ERROR', scope='DIAGRAM', field='nodes', target=diagram_target, message='OPC revision must contain at least one operation, inspection, or transport node.')

    node_ids = {node.pk for node in nodes}
    normal_incoming = {str(node.pk): 0 for node in nodes}
    normal_outgoing = {str(node.pk): 0 for node in nodes}
    any_incoming = {str(node.pk): 0 for node in nodes}
    any_outgoing = {str(node.pk): 0 for node in nodes}
    edge_pairs = set()

    for edge in edges:
        target = _edge_target(edge)
        if edge.source_id == edge.target_id:
            _add(issues, code='self_loop_edge', severity='ERROR', scope='EDGE', field='target', target=target, targets=(_edge_target(edge),), message='OPC edges cannot reference the same source and target node.')
        if edge.source_id not in node_ids or edge.target_id not in node_ids or edge.source.diagram_id != diagram.pk or edge.target.diagram_id != diagram.pk:
            _add(issues, code='cross_diagram_edge', severity='ERROR', scope='EDGE', target=target, targets=(_edge_target(edge),), message='Every OPC edge must reference nodes in the same OPC revision.')
        pair = (edge.source_id, edge.target_id)
        if pair in edge_pairs:
            _add(issues, code='duplicate_edge', severity='ERROR', scope='EDGE', target=target, targets=(_edge_target(edge),), message='Duplicate OPC edges are not allowed.')
        edge_pairs.add(pair)
        any_incoming[str(edge.target_id)] = any_incoming.get(str(edge.target_id), 0) + 1
        any_outgoing[str(edge.source_id)] = any_outgoing.get(str(edge.source_id), 0) + 1
        if edge.edge_type in NORMAL_EDGE_TYPES:
            normal_incoming[str(edge.target_id)] = normal_incoming.get(str(edge.target_id), 0) + 1
            normal_outgoing[str(edge.source_id)] = normal_outgoing.get(str(edge.source_id), 0) + 1

    start_ids = [str(node.pk) for node in nodes if normal_incoming.get(str(node.pk), 0) == 0]
    end_ids = [str(node.pk) for node in nodes if normal_outgoing.get(str(node.pk), 0) == 0]
    if not start_ids:
        _add(issues, code='missing_start_node', severity='ERROR', scope='DIAGRAM', field='topology', target=diagram_target, message='At least one start operation must be identifiable.')
    if not end_ids:
        _add(issues, code='missing_terminal_node', severity='ERROR', scope='DIAGRAM', field='topology', target=diagram_target, message='At least one terminal operation must be identifiable.')

    adjacency, reverse = _normal_adjacency(nodes, edges)
    reached = _reachable(start_ids, adjacency) if start_ids else set()
    can_reach_end = _reachable(end_ids, reverse) if end_ids else set()
    for node in nodes:
        node_id = str(node.pk)
        target = _node_target(node)
        if any_incoming.get(node_id, 0) == 0 and any_outgoing.get(node_id, 0) == 0:
            _add(issues, code='isolated_node', severity='ERROR', scope='NODE', field='topology', target=target, message=f'Node {node.label} is not connected to the OPC flow.', sort_index=node.sequence)
        elif start_ids and node_id not in reached:
            _add(issues, code='unreachable_node', severity='ERROR', scope='NODE', field='topology', target=target, message=f'Node {node.label} is not reachable from a start operation.', sort_index=node.sequence)
        elif end_ids and node_id not in can_reach_end:
            _add(issues, code='dead_end_node', severity='ERROR', scope='NODE', field='topology', target=target, message=f'Node {node.label} cannot reach a terminal operation.', sort_index=node.sequence)

    cycle = _normal_cycle(nodes, edges)
    if cycle:
        _add(issues, code='invalid_cycle', severity='ERROR', scope='DIAGRAM', field='topology', target=diagram_target, message='OPC graph cycle detected: ' + ' -> '.join(cycle), details={'cycle': cycle})

    node_by_id = {str(node.pk): node for node in nodes}
    components = _strong_components(nodes, edges)
    rework_edges = [edge for edge in edges if edge.edge_type == REWORK_EDGE_TYPE]
    for edge in rework_edges:
        edge_target = _edge_target(edge)
        if edge.source.node_type not in REWORK_ALLOWED_SOURCES:
            _add(issues, code='invalid_rework_source', severity='ERROR', scope='EDGE', field='source', target=edge_target, targets=(_edge_target(edge), _node_target(edge.source)), message='Rework edges must start from an inspection or operation node.')
        if edge.target.node_type not in REWORK_ALLOWED_TARGETS:
            _add(issues, code='invalid_rework_target', severity='ERROR', scope='EDGE', field='target', target=edge_target, targets=(_edge_target(edge), _node_target(edge.target)), message='Rework edges must return to an operation or inspection node.')
        if edge.source.operation_number is not None and edge.target.operation_number is not None and edge.target.operation_number >= edge.source.operation_number:
            _add(issues, code='rework_not_upstream', severity='ERROR', scope='EDGE', field='target', target=edge_target, targets=(_edge_target(edge), _node_target(edge.source), _node_target(edge.target)), message='Rework edges must return to an earlier operation number.')
    for component in components:
        if len(component) <= 1:
            continue
        component_rework = [edge for edge in rework_edges if str(edge.source_id) in component and str(edge.target_id) in component]
        if not component_rework:
            continue
        has_normal_exit = any(edge.edge_type in NORMAL_EDGE_TYPES and str(edge.source_id) in component and str(edge.target_id) not in component for edge in edges)
        if not has_normal_exit:
            labels = sorted(node_by_id[node_id].label for node_id in component if node_id in node_by_id)
            _add(issues, code='rework_has_no_exit', severity='ERROR', scope='DIAGRAM', field='topology', target=diagram_target, targets=tuple(_node_target(node_by_id[node_id]) for node_id in sorted(component) if node_id in node_by_id), message='Rework loop must have a normal forward exit.', details={'nodes': labels})


def _validate_node_assignments(mode: str, nodes: list[OPCNode], issues: list[ValidationIssueInput]):
    operation_numbers: dict[int, OPCNode] = {}
    for node in nodes:
        target = _node_target(node)
        if not node.label.strip():
            _add(issues, code='missing_node_label', severity='ERROR', scope='NODE', field='label', target=target, message='Every OPC node requires a label.', sort_index=node.sequence)
        for field in ('setup_time_hours', 'run_time_per_unit_hours', 'queue_time_hours', 'move_time_hours', 'inspection_time_hours', 'external_lead_time_days', 'buffer_time_hours'):
            if getattr(node, field) < 0:
                _add(issues, code='negative_duration', severity='ERROR', scope='NODE', field=field, target=target, message='Duration values cannot be negative.', sort_index=node.sequence)
        tooling = list(node.tooling_requirements.all())
        if not is_operation_capable(node.node_type) and (
            node.operation_number
            or node.process_definition_id
            or node.plant_id
            or node.work_center_id
            or node.machine_asset_id
            or tooling
        ):
            _add(issues, code='assignment_on_non_operation_node', severity='ERROR', scope='NODE', field='resource_assignment', target=target, message=f'Node {node.label} cannot carry structured operation-resource assignments.', sort_index=node.sequence)

        if node.operation_number is not None:
            if node.operation_number <= 0:
                _add(issues, code='invalid_operation_number', severity='ERROR', scope='NODE', field='operation_number', target=target, message=f'Node {node.label} has an invalid operation number.', sort_index=node.sequence)
            elif node.operation_number in operation_numbers:
                other = operation_numbers[node.operation_number]
                _add(issues, code='duplicate_operation_number', severity='ERROR', scope='NODE', field='operation_number', target=target, targets=(_node_target(other), target), message=f'Operation number {node.operation_number} is used more than once.', sort_index=node.sequence)
            else:
                operation_numbers[node.operation_number] = node

        if not is_operation_capable(node.node_type):
            continue

        legacy_present = bool((node.process_code or '').strip() or (node.station or '').strip())
        if node.operation_number is None:
            severity = 'WARNING' if legacy_present or mode == 'draft' else 'ERROR'
            _add(issues, code='missing_operation_number', severity=severity, scope='NODE', field='operation_number', target=target, message=f'Operation {node.label} needs an operation number before release.', sort_index=node.sequence)
        if not node.process_definition_id:
            severity = 'WARNING' if legacy_present or mode == 'draft' else 'ERROR'
            message = (
                f'Operation {node.label} still uses legacy process/station fields without structured assignment.'
                if legacy_present
                else f'Operation {node.label} needs a structured process definition before release.'
            )
            _add(issues, code='missing_process_definition', severity=severity, scope='NODE', field='process_definition', target=target, message=message, acknowledgeable=legacy_present, sort_index=node.sequence)
        elif not node.process_definition.active:
            _add(issues, code='inactive_process_definition', severity='WARNING', scope='NODE', field='process_definition', target=target, message=f'Operation {node.label} references inactive process {node.process_definition.process_code}.', acknowledgeable=True, sort_index=node.sequence)

        if node.process_definition_id:
            process = node.process_definition
            if process.execution_classification != ProcessDefinition.EXECUTION_FLEXIBLE and process.execution_classification != execution_bucket(node.execution_type):
                _add(issues, code='process_execution_mismatch', severity='ERROR', scope='NODE', field='process_definition', target=target, message=f'Operation {node.label} process execution classification does not match node execution type.', sort_index=node.sequence)

        if node.execution_type == 'EXTERNAL':
            if node.external_lead_time_days <= 0:
                _add(issues, code='external_missing_lead_time', severity='WARNING', scope='NODE', field='external_lead_time_days', target=target, message=f'External operation {node.label} has no external lead time.', acknowledgeable=True, sort_index=node.sequence)
            if node.plant_id or node.work_center_id or node.machine_asset_id:
                _add(issues, code='external_resources_assigned', severity='ERROR', scope='NODE', field='resource_assignment', target=target, message=f'External operation {node.label} cannot reference internal resources.', sort_index=node.sequence)
        elif node.execution_type in {'INTERNAL', 'INSPECTION', 'TRANSPORT', 'MIXED'}:
            if not node.plant_id:
                severity = 'WARNING' if legacy_present or mode == 'draft' else 'ERROR'
                _add(issues, code='missing_plant', severity=severity, scope='NODE', field='plant', target=target, message=f'Operation {node.label} needs a plant before release.', sort_index=node.sequence)
            if not node.work_center_id:
                severity = 'WARNING' if legacy_present or mode == 'draft' else 'ERROR'
                _add(issues, code='missing_work_center', severity=severity, scope='NODE', field='work_center', target=target, message=f'Operation {node.label} needs a work center before release.', sort_index=node.sequence)

        if node.work_center_id and not node.plant_id:
            _add(issues, code='work_center_without_plant', severity='ERROR', scope='NODE', field='plant', target=target, message=f'Operation {node.label} has a work center without a plant.', sort_index=node.sequence)
        if node.work_center_id and node.plant_id and node.work_center.plant_id != node.plant_id:
            _add(issues, code='work_center_outside_plant', severity='ERROR', scope='NODE', field='work_center', target=target, message=f'Operation {node.label} work center is outside the selected plant.', sort_index=node.sequence)
        if node.machine_asset_id and not node.work_center_id:
            _add(issues, code='machine_without_work_center', severity='ERROR', scope='NODE', field='machine_asset', target=target, message=f'Operation {node.label} has a machine without a work center.', sort_index=node.sequence)
        if node.machine_asset_id and node.work_center_id and node.machine_asset.work_center_id != node.work_center_id:
            _add(issues, code='machine_outside_work_center', severity='ERROR', scope='NODE', field='machine_asset', target=target, message=f'Operation {node.label} machine is outside the selected work center.', sort_index=node.sequence)
        if node.plant_id and not node.plant.active:
            _add(issues, code='inactive_plant', severity='WARNING', scope='NODE', field='plant', target=target, message=f'Operation {node.label} references inactive plant {node.plant.code}.', acknowledgeable=True, sort_index=node.sequence)
        if node.work_center_id and not node.work_center.active:
            _add(issues, code='inactive_work_center', severity='WARNING', scope='NODE', field='work_center', target=target, message=f'Operation {node.label} references inactive work center {node.work_center.code}.', acknowledgeable=True, sort_index=node.sequence)
        if node.machine_asset_id and not node.machine_asset.active:
            _add(issues, code='inactive_machine_asset', severity='WARNING', scope='NODE', field='machine_asset', target=target, message=f'Operation {node.label} references inactive machine {node.machine_asset.asset_code}.', acknowledgeable=True, sort_index=node.sequence)

        seen_tooling = set()
        for requirement in tooling:
            tooling_target = {'scope': 'TOOLING_REQUIREMENT', 'id': str(requirement.pk), 'label': requirement.tooling_definition.tooling_code}
            if requirement.tooling_definition_id in seen_tooling:
                _add(issues, code='duplicate_tooling_requirement', severity='ERROR', scope='TOOLING_REQUIREMENT', field='tooling_definition', target=tooling_target, targets=(target, tooling_target), message=f'Operation {node.label} has duplicate tooling requirements.', sort_index=node.sequence)
            seen_tooling.add(requirement.tooling_definition_id)
            if requirement.quantity <= 0:
                _add(issues, code='invalid_tooling_quantity', severity='ERROR', scope='TOOLING_REQUIREMENT', field='quantity', target=tooling_target, targets=(target, tooling_target), message=f'Operation {node.label} has invalid tooling quantity.', sort_index=node.sequence)
            if not requirement.tooling_definition.active:
                _add(issues, code='inactive_tooling_definition', severity='WARNING', scope='TOOLING_REQUIREMENT', field='tooling_definition', target=tooling_target, targets=(target, tooling_target), message=f'Operation {node.label} references inactive tooling {requirement.tooling_definition.tooling_code}.', acknowledgeable=True, sort_index=node.sequence)


def _validate_phase6_references(diagram: OPCDiagram, nodes: list[OPCNode], issues: list[ValidationIssueInput]):
    diagram_target = _diagram_target(diagram)
    if not diagram.item_revision_id:
        return
    mbom = diagram.manufacturing_bom_revision
    if not mbom:
        _add(issues, code='missing_manufacturing_bom', severity='ERROR', scope='DIAGRAM', field='manufacturing_bom_revision', target=diagram_target, message='Release requires an exact released manufacturing BOM revision.')
        return
    if mbom.bom.bom_type != 'MANUFACTURING':
        _add(issues, code='invalid_manufacturing_bom_type', severity='ERROR', scope='DIAGRAM', field='manufacturing_bom_revision', target=diagram_target, message='OPC manufacturing BOM reference must use BOM type MANUFACTURING.')
    if mbom.bom.parent_item_revision_id != diagram.item_revision_id:
        _add(issues, code='manufacturing_bom_context_mismatch', severity='ERROR', scope='DIAGRAM', field='manufacturing_bom_revision', target=diagram_target, message='Manufacturing BOM must belong to the OPC ItemRevision.')
    if mbom.status != 'RELEASED':
        _add(issues, code='manufacturing_bom_not_released', severity='ERROR', scope='DIAGRAM', field='manufacturing_bom_revision', target=diagram_target, message='Release requires a released manufacturing BOM revision.')
    if diagram.effective_from and mbom.effective_from and diagram.effective_from < mbom.effective_from:
        _add(issues, code='manufacturing_bom_effectivity_mismatch', severity='ERROR', scope='DIAGRAM', field='effective_from', target=diagram_target, message='OPC effective date cannot precede the manufacturing BOM effective date.')

    mbom_lines = list(mbom.lines.select_related('component_item_revision__item').order_by('sequence', 'created_at'))
    line_by_id = {line.pk: line for line in mbom_lines}
    allocations = []
    for node in nodes:
        allocations.extend(list(node.material_allocations.select_related('bom_line__component_item_revision__item', 'component_item_revision__item').all()))
    allocated_by_line: dict[object, object] = {}
    for allocation in allocations:
        node = allocation.node
        target = _node_target(node)
        if allocation.quantity <= 0:
            _add(issues, code='invalid_material_allocation_quantity', severity='ERROR', scope='NODE', field='material_allocations', target=target, message=f'Material allocation on {node.label} must have positive quantity.', sort_index=node.sequence)
        if allocation.scrap_percent < 0:
            _add(issues, code='invalid_material_allocation_scrap', severity='ERROR', scope='NODE', field='material_allocations', target=target, message=f'Material allocation on {node.label} cannot have negative scrap.', sort_index=node.sequence)
        if allocation.bom_line_id:
            line = line_by_id.get(allocation.bom_line_id)
            if not line:
                _add(issues, code='allocation_wrong_mbom_context', severity='ERROR', scope='NODE', field='material_allocations', target=target, message=f'Material allocation on {node.label} references a BOM line outside the selected MBOM.', sort_index=node.sequence)
                continue
            if allocation.component_item_revision_id != line.component_item_revision_id:
                _add(issues, code='allocation_component_mismatch', severity='ERROR', scope='NODE', field='material_allocations', target=target, message=f'Material allocation on {node.label} component revision does not match the MBOM line.', sort_index=node.sequence)
            allocated_by_line[line.pk] = allocated_by_line.get(line.pk, 0) + allocation.quantity
        elif allocation.allocation_type != 'CONSUMABLE':
            _add(issues, code='allocation_missing_mbom_line', severity='ERROR', scope='NODE', field='material_allocations', target=target, message=f'Material allocation on {node.label} must reference an MBOM line unless it is a consumable.', sort_index=node.sequence)

    for line in mbom_lines:
        if line.is_phantom or line.is_optional:
            continue
        allocated = allocated_by_line.get(line.pk, 0)
        if allocated == 0:
            _add(issues, code='mbom_line_unallocated', severity='ERROR', scope='DIAGRAM', field='material_allocations', target=diagram_target, message=f'MBOM line {line.sequence} {line.component_item_revision.item.item_code}/{line.component_item_revision.revision} is not allocated to an operation.', details={'bom_line_id': str(line.pk)})
        elif allocated > line.quantity:
            _add(issues, code='mbom_line_overallocated', severity='ERROR', scope='DIAGRAM', field='material_allocations', target=diagram_target, message=f'MBOM line {line.sequence} is over-allocated across operations.', details={'bom_line_id': str(line.pk), 'required_quantity': str(line.quantity), 'allocated_quantity': str(allocated)})
        elif allocated < line.quantity:
            _add(issues, code='mbom_line_underallocated', severity='ERROR', scope='DIAGRAM', field='material_allocations', target=diagram_target, message=f'MBOM line {line.sequence} is under-allocated across operations.', details={'bom_line_id': str(line.pk), 'required_quantity': str(line.quantity), 'allocated_quantity': str(allocated)})

    for node in nodes:
        if not is_operation_capable(node.node_type):
            continue
        target = _node_target(node)
        requirements = list(node.document_requirements.select_related('document_revision__document').all())
        if node.node_type == 'OPERATION' and not any(req.purpose == 'WORK_INSTRUCTION' and req.mandatory for req in requirements):
            _add(issues, code='missing_mandatory_work_instruction', severity='ERROR', scope='NODE', field='document_requirements', target=target, message=f'Operation {node.label} requires a mandatory work instruction document.', sort_index=node.sequence)
        seen = set()
        for requirement in requirements:
            key = (requirement.document_revision_id, requirement.purpose)
            if key in seen:
                _add(issues, code='duplicate_document_requirement', severity='ERROR', scope='NODE', field='document_requirements', target=target, message=f'Operation {node.label} has duplicate document requirements.', sort_index=node.sequence)
            seen.add(key)
            revision = requirement.document_revision
            if revision.status != ControlledDocumentRevision.STATUS_RELEASED:
                _add(issues, code='document_revision_not_released', severity='ERROR', scope='NODE', field='document_requirements', target=target, message=f'Document {revision.document.document_number}/{revision.revision} assigned to {node.label} is not released.', sort_index=node.sequence)
            if diagram.effective_from and revision.effective_from and diagram.effective_from < revision.effective_from:
                _add(issues, code='document_effectivity_mismatch', severity='ERROR', scope='NODE', field='document_requirements', target=target, message=f'Document {revision.document.document_number}/{revision.revision} is not effective on the OPC release date.', sort_index=node.sequence)


def _compatibility(issues: list[dict]) -> tuple[dict[str, str], list[str]]:
    errors: dict[str, str] = {}
    warnings: list[str] = []
    code_counts: defaultdict[str, int] = defaultdict(int)
    key_map = {
        'invalid_cycle': 'cycle',
        'missing_start_node': 'start',
        'missing_terminal_node': 'terminal',
        'missing_operation_node': 'operation',
        'missing_nodes': 'nodes',
        'missing_item_revision': 'item_revision',
        'item_revision_not_release_ready': 'item_revision',
        'missing_revision_code': 'revision',
        'missing_effective_from': 'effective_from',
    }
    for issue in issues:
        if issue['severity'] == 'ERROR':
            code_counts[issue['code']] += 1
            key = key_map.get(issue['code']) or issue['field'] or issue['code']
            if key in errors:
                key = f'{issue["code"]}_{code_counts[issue["code"]]}'
            errors[key] = issue['message']
        elif issue['severity'] == 'WARNING':
            warnings.append(issue['message'])
    return errors, warnings


def validate_opc_release_readiness(
    diagram: OPCDiagram,
    *,
    mode: str = 'draft',
    expected_graph_version: int | None = None,
    acknowledged_issue_keys: Iterable[str] = (),
) -> dict:
    if mode not in VALIDATION_MODES:
        mode = 'draft'
    if expected_graph_version is not None and diagram.graph_version != int(expected_graph_version):
        raise OPCValidationConflict(
            {
                'code': 'validation_graph_version_conflict',
                'detail': 'The OPC diagram changed before validation could complete.',
                'expected_version': int(expected_graph_version),
                'current_version': diagram.graph_version,
                'diagram_id': str(diagram.pk),
            }
        )

    nodes = _sorted_nodes(diagram)
    edges = _sorted_edges(diagram)
    raw_issues: list[ValidationIssueInput] = []
    _validate_header(diagram, raw_issues)
    _validate_topology(diagram, nodes, edges, raw_issues)
    _validate_node_assignments(mode, nodes, raw_issues)
    if mode == 'release':
        _validate_phase6_references(diagram, nodes, raw_issues)

    acknowledged = {str(value) for value in acknowledged_issue_keys if value}
    issues = [_issue_dict(diagram, issue, acknowledged) for issue in raw_issues]
    known_keys = {issue['issue_key'] for issue in issues}
    for key in sorted(acknowledged - known_keys):
        _add(
            raw_issues,
            code='unknown_warning_acknowledgement',
            severity='ERROR',
            scope='DIAGRAM',
            field='acknowledgements',
            target=_diagram_target(diagram),
            message='Warning acknowledgement is stale or does not match this OPC validation policy.',
        )
    if len(raw_issues) != len(issues):
        issues = [_issue_dict(diagram, issue, acknowledged) for issue in raw_issues]

    issues.sort(key=lambda issue: issue['_sort'])
    for issue in issues:
        issue.pop('_sort', None)

    errors, warnings = _compatibility(issues)
    unacknowledged_required = [
        issue for issue in issues
        if issue['severity'] == 'WARNING'
        and issue['acknowledgement_required']
        and not issue['acknowledged']
    ]
    acknowledged_warning_codes = sorted({
        issue['code'] for issue in issues
        if issue['severity'] == 'WARNING' and issue['acknowledgeable'] and issue['acknowledged']
    })
    counts = {
        'error_count': sum(1 for issue in issues if issue['severity'] == 'ERROR'),
        'blocking_error_count': sum(1 for issue in issues if issue['blocking']),
        'warning_count': sum(1 for issue in issues if issue['severity'] == 'WARNING'),
        'info_count': sum(1 for issue in issues if issue['severity'] == 'INFO'),
        'acknowledgeable_warning_count': sum(1 for issue in issues if issue['severity'] == 'WARNING' and issue['acknowledgeable']),
        'unacknowledged_warning_count': len(unacknowledged_required),
    }
    valid = counts['error_count'] == 0
    release_ready = valid and not unacknowledged_required
    return {
        'diagram_id': str(diagram.pk),
        'graph_version': diagram.graph_version,
        'validated_at': timezone.now().isoformat(),
        'policy_version': POLICY_VERSION,
        'mode': mode,
        'valid': valid,
        'release_ready': release_ready,
        'counts': counts,
        'issues': issues,
        'acknowledged_warning_codes': acknowledged_warning_codes,
        'compatibility': {
            'errors': errors,
            'warnings': warnings,
        },
        'errors': errors,
        'warnings': warnings,
    }
