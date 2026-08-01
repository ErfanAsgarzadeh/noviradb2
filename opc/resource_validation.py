from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from rest_framework.exceptions import ValidationError

from .models import MachineAsset, OPCNode, OPCToolingRequirement, Plant, ProcessDefinition, ToolingDefinition, WorkCenter


OPERATION_CAPABLE_NODE_TYPES = {'OPERATION', 'INSPECTION', 'TRANSPORT'}


def execution_bucket(execution_type: str | None) -> str:
    return 'EXTERNAL' if execution_type == 'EXTERNAL' else 'INTERNAL'


def is_operation_capable(node_type: str | None) -> bool:
    return node_type in OPERATION_CAPABLE_NODE_TYPES


def _same(existing, field: str, value) -> bool:
    if not existing:
        return False
    return getattr(existing, f'{field}_id', None) == value


def _new_assignment(existing, field: str, value) -> bool:
    return bool(value) and not _same(existing, field, value)


def validate_node_resource_assignments(
    *,
    index: int,
    attrs: dict[str, Any],
    tooling_payloads: list[dict[str, Any]],
    existing_node: OPCNode | None,
    plants: dict[Any, Plant],
    work_centers: dict[Any, WorkCenter],
    machines: dict[Any, MachineAsset],
    processes: dict[Any, ProcessDefinition],
    tooling_definitions: dict[Any, ToolingDefinition],
    existing_tooling: dict[Any, OPCToolingRequirement] | None = None,
):
    node_type = attrs.get('node_type')
    process_id = attrs.get('process_definition_id')
    plant_id = attrs.get('plant_id')
    work_center_id = attrs.get('work_center_id')
    machine_id = attrs.get('machine_asset_id')
    execution_type = attrs.get('execution_type')
    assigned = bool(process_id or plant_id or work_center_id or machine_id or tooling_payloads)

    if assigned and not is_operation_capable(node_type):
        raise ValidationError({'nodes': {index: 'Structured assignments are only allowed on operation-capable nodes.'}})

    if attrs.get('operation_number') is not None and attrs['operation_number'] <= 0:
        raise ValidationError({'nodes': {index: {'operation_number': 'Operation number must be positive.'}}})

    process = processes.get(process_id) if process_id else None
    plant = plants.get(plant_id) if plant_id else None
    work_center = work_centers.get(work_center_id) if work_center_id else None
    machine = machines.get(machine_id) if machine_id else None

    if process_id and not process:
        raise ValidationError({'nodes': {index: {'process_definition': 'Unknown process definition.'}}})
    if plant_id and not plant:
        raise ValidationError({'nodes': {index: {'plant': 'Unknown plant.'}}})
    if work_center_id and not work_center:
        raise ValidationError({'nodes': {index: {'work_center': 'Unknown work center.'}}})
    if machine_id and not machine:
        raise ValidationError({'nodes': {index: {'machine_asset': 'Unknown machine asset.'}}})

    if process and _new_assignment(existing_node, 'process_definition', process_id) and not process.active:
        raise ValidationError({'nodes': {index: {'process_definition': 'Inactive process definitions cannot be newly assigned.'}}})
    if plant and _new_assignment(existing_node, 'plant', plant_id) and not plant.active:
        raise ValidationError({'nodes': {index: {'plant': 'Inactive plants cannot be newly assigned.'}}})
    if work_center and _new_assignment(existing_node, 'work_center', work_center_id) and not work_center.active:
        raise ValidationError({'nodes': {index: {'work_center': 'Inactive work centers cannot be newly assigned.'}}})
    if machine and _new_assignment(existing_node, 'machine_asset', machine_id) and not machine.active:
        raise ValidationError({'nodes': {index: {'machine_asset': 'Inactive machine assets cannot be newly assigned.'}}})

    bucket = execution_bucket(execution_type)
    if process and process.execution_classification != ProcessDefinition.EXECUTION_FLEXIBLE and process.execution_classification != bucket:
        raise ValidationError({'nodes': {index: {'process_definition': 'Process execution classification does not match the OPC node execution type.'}}})

    if bucket == 'EXTERNAL' and (plant_id or work_center_id or machine_id):
        raise ValidationError({'nodes': {index: {'execution_type': 'External operations cannot reference internal plant, work center, or machine resources.'}}})
    if work_center_id and not plant_id:
        raise ValidationError({'nodes': {index: {'plant': 'Plant is required when a work center is selected.'}}})
    if machine_id and not work_center_id:
        raise ValidationError({'nodes': {index: {'machine_asset': 'Machine assignment requires a work center.'}}})
    if plant and work_center and work_center.plant_id != plant.pk:
        raise ValidationError({'nodes': {index: {'work_center': 'Work center must belong to the selected plant.'}}})
    if machine and work_center and machine.work_center_id != work_center.pk:
        raise ValidationError({'nodes': {index: {'machine_asset': 'Machine asset must belong to the selected work center.'}}})
    if machine and plant and machine.plant_id != plant.pk:
        raise ValidationError({'nodes': {index: {'machine_asset': 'Machine asset must belong to the selected plant.'}}})

    existing_by_tool = existing_tooling or {}
    seen_tooling: set[Any] = set()
    for req_index, requirement in enumerate(tooling_payloads):
        tooling_id = requirement.get('tooling_definition_id')
        if not tooling_id:
            raise ValidationError({'nodes': {index: {'tooling_requirements': {req_index: {'tooling_definition': 'Tooling definition is required.'}}}}})
        if tooling_id in seen_tooling:
            raise ValidationError({'nodes': {index: {'tooling_requirements': {req_index: 'Duplicate tooling requirements are not allowed.'}}}})
        seen_tooling.add(tooling_id)
        tooling = tooling_definitions.get(tooling_id)
        if not tooling:
            raise ValidationError({'nodes': {index: {'tooling_requirements': {req_index: {'tooling_definition': 'Unknown tooling definition.'}}}}})
        if not tooling.active and tooling_id not in existing_by_tool:
            raise ValidationError({'nodes': {index: {'tooling_requirements': {req_index: {'tooling_definition': 'Inactive tooling cannot be newly assigned.'}}}}})
        try:
            quantity = Decimal(str(requirement.get('quantity', 1)))
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError({'nodes': {index: {'tooling_requirements': {req_index: {'quantity': 'Tooling quantity must be numeric.'}}}}}) from exc
        if quantity <= 0:
            raise ValidationError({'nodes': {index: {'tooling_requirements': {req_index: {'quantity': 'Tooling quantity must be positive.'}}}}})
