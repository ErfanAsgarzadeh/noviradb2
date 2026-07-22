"""
Microsoft Project XML importer.

Imports Microsoft Project summary rows as WBS nodes and leaf rows as tasks
into an existing Project/Revision while preserving the mixed sibling order
used by the ``reorder-mixed`` endpoint.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import IO, Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import (
    Dependency,
    Project,
    Revision,
    Task,
    TaskVersion,
    WBSNode,
    WBSNodeVersion,
)

MSP_NS = "http://schemas.microsoft.com/project"


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _namespace_uri(root: ET.Element) -> str:
    """Return the document namespace URI, or an empty string."""
    if root.tag.startswith("{") and "}" in root.tag:
        return root.tag[1:].split("}", 1)[0]
    return ""


def _qname(name: str, namespace: str) -> str:
    return f"{{{namespace}}}{name}" if namespace else name


def _find(element: ET.Element, name: str, namespace: str) -> ET.Element | None:
    return element.find(_qname(name, namespace))


def _findall(element: ET.Element, name: str, namespace: str) -> list[ET.Element]:
    return list(element.findall(_qname(name, namespace)))


def _text(
    element: ET.Element,
    name: str,
    namespace: str,
    default: str | None = None,
) -> str | None:
    node = _find(element, name, namespace)
    if node is None or node.text is None:
        return default
    value = node.text.strip()
    return value if value else default


def _int(
    element: ET.Element,
    name: str,
    namespace: str,
    default: int = 0,
) -> int:
    try:
        return int(_text(element, name, namespace))
    except (TypeError, ValueError):
        return default


def _bool(
    element: ET.Element,
    name: str,
    namespace: str,
    default: bool = False,
) -> bool:
    value = _text(element, name, namespace)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes"}


def _parse_dt(raw: str | None) -> datetime | None:
    """Parse common MSP ISO date/datetime values into aware datetimes."""
    if not raw:
        return None

    value = raw.strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None

    if parsed is None:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _parse_duration(raw: str | None) -> Decimal:
    """
    Convert an MSP ISO-8601 duration to decimal working hours.

    Examples:
        PT8H       -> 8
        PT1H30M    -> 1.5
        P1DT2H     -> 10, assuming an 8-hour working day
    """
    if not raw:
        return Decimal("0")

    match = re.fullmatch(
        r"P(?:(?P<days>\d+(?:\.\d+)?)D)?"
        r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?",
        raw.strip(),
    )
    if not match:
        return Decimal("0")

    try:
        days = Decimal(match.group("days") or "0")
        hours = Decimal(match.group("hours") or "0")
        minutes = Decimal(match.group("minutes") or "0")
        seconds = Decimal(match.group("seconds") or "0")
    except InvalidOperation:
        return Decimal("0")

    return (days * Decimal("8")) + hours + (minutes / Decimal("60")) + (
        seconds / Decimal("3600")
    )


# ---------------------------------------------------------------------------
# MSP outline parsing
# ---------------------------------------------------------------------------


def _build_wbs_outline(tasks_element: ET.Element, namespace: str) -> dict[str, dict[str, Any]]:
    """
    Read MSP Task rows in source order.

    Python dictionaries preserve insertion order, so iterating the returned
    mapping later keeps the exact row order exported by Microsoft Project.
    """
    tasks: dict[str, dict[str, Any]] = {}

    for source_index, task_element in enumerate(
        _findall(tasks_element, "Task", namespace),
        start=1,
    ):
        uid = _text(task_element, "UID", namespace)
        if uid is None or uid == "0":
            # UID 0 is commonly the optional project-summary row.
            continue

        predecessors: list[tuple[str, str, int]] = []
        for predecessor_element in _findall(
            task_element,
            "PredecessorLink",
            namespace,
        ):
            predecessor_uid = _text(
                predecessor_element,
                "PredecessorUID",
                namespace,
            )
            if not predecessor_uid or predecessor_uid == "0":
                continue

            link_type_number = _int(
                predecessor_element,
                "Type",
                namespace,
                default=1,
            )
            link_type = {0: "FF", 1: "FS", 2: "SS", 3: "SF"}.get(
                link_type_number,
                "FS",
            )

            lag_minutes = _int(
                predecessor_element,
                "LinkLag",
                namespace,
                default=0,
            )
            lag_hours = round(lag_minutes / 60)
            predecessors.append((predecessor_uid, link_type, lag_hours))

        tasks[uid] = {
            "name": _text(task_element, "Name", namespace)
            or f"Unnamed Task (UID: {uid})",
            "outline_level": _int(
                task_element,
                "OutlineLevel",
                namespace,
                default=1,
            ),
            "outline_number": _text(
                task_element,
                "OutlineNumber",
                namespace,
                default="",
            ),
            "is_summary": _bool(task_element, "Summary", namespace),
            "start": _text(task_element, "Start", namespace),
            "finish": _text(task_element, "Finish", namespace),
            "duration": _text(task_element, "Duration", namespace),
            "predecessors": predecessors,
            "source_index": source_index,
        }

    return tasks


def _infer_parents(tasks: dict[str, dict[str, Any]]) -> dict[str, str | None]:
    """
    Infer the nearest summary parent from MSP OutlineLevel values.

    Only summary rows remain on the parent stack. This prevents a malformed or
    unusual outline from attaching a task beneath another non-summary task.
    """
    parent_map: dict[str, str | None] = {}
    summary_stack: list[tuple[int, str]] = []

    for uid, info in tasks.items():
        level = int(info["outline_level"])

        while summary_stack and summary_stack[-1][0] >= level:
            summary_stack.pop()

        parent_map[uid] = summary_stack[-1][1] if summary_stack else None

        if info["is_summary"]:
            summary_stack.append((level, uid))

    return parent_map


def _build_sibling_sequences(
    tasks: dict[str, dict[str, Any]],
    parent_map: dict[str, str | None],
) -> dict[str, int]:
    """
    Build one shared sequence for WBS and task siblings under each parent.

    This mirrors ``reorder_mixed``: sequence values are local to a WBS parent,
    and WBS/task rows participate in the same ordered range.
    """
    counters: defaultdict[str | None, int] = defaultdict(int)
    sequences: dict[str, int] = {}

    for uid in tasks:
        parent_uid = parent_map[uid]
        counters[parent_uid] += 1
        sequences[uid] = counters[parent_uid]

    return sequences


# ---------------------------------------------------------------------------
# Main importer
# ---------------------------------------------------------------------------


@transaction.atomic
def import_msp_xml(
    xml_file: IO[bytes],
    project_id: str,
    revision_id: int,
    active_node_id: str | None = None,
    user: Any = None,
) -> dict[str, Any]:
    warnings: list[str] = []

    project = Project.objects.get(pk=project_id)
    revision = Revision.objects.get(pk=revision_id, project=project)

    tree = ET.parse(xml_file)
    root = tree.getroot()
    namespace = _namespace_uri(root)

    tasks_element = _find(root, "Tasks", namespace)
    if tasks_element is None:
        return {"error": "No <Tasks> element found in XML."}

    tasks_info = _build_wbs_outline(tasks_element, namespace)
    if not tasks_info:
        return {"error": "No tasks found in XML."}

    parent_map = _infer_parents(tasks_info)
    sibling_sequence = _build_sibling_sequences(tasks_info, parent_map)

    # Resolve the database WBS node beneath which the imported top-level rows
    # will be inserted.
    if active_node_id is not None:
        try:
            import_root = WBSNodeVersion.objects.get(
                node__id=active_node_id,
                revision=revision,
                is_deleted=False,
            )
        except WBSNodeVersion.DoesNotExist:
            return {
                "error": (
                    f"Selected node (id={active_node_id}) does not belong to "
                    f"revision {revision_id}. Import aborted."
                )
            }
    else:
        import_root = WBSNodeVersion.objects.filter(
            revision=revision,
            parent=None,
            is_deleted=False,
        ).first()

    if import_root is None:
        root_node = WBSNode.objects.create(project=project)
        import_root = WBSNodeVersion.objects.create(
            node=root_node,
            revision=revision,
            title=f"Root: {project.name}",
            sequence=1,
            parent=None,
        )
        warnings.append("Root WBSNodeVersion was missing and has been created.")

    uid_to_wbs_version: dict[str, WBSNodeVersion] = {}
    uid_to_task: dict[str, Task] = {}

    # Existing rows under the selected import root must remain before the new
    # imported top-level rows. Newly-created imported WBS parents start empty.
    parent_sequence_offsets: dict[int, int] = {}

    def _existing_max_mixed_sequence(parent: WBSNodeVersion) -> int:
        max_wbs_sequence = (
            WBSNodeVersion.objects.filter(
                revision=revision,
                parent=parent,
                is_deleted=False,
            ).aggregate(value=Max("sequence"))["value"]
            or 0
        )
        max_task_sequence = (
            TaskVersion.objects.filter(
                revision=revision,
                wbs_node=parent,
                is_deleted=False,
            ).aggregate(value=Max("sequence"))["value"]
            or 0
        )
        return max(max_wbs_sequence, max_task_sequence)

    def _sequence_for(uid: str, database_parent: WBSNodeVersion) -> int:
        parent_key = database_parent.pk
        if parent_key not in parent_sequence_offsets:
            parent_sequence_offsets[parent_key] = _existing_max_mixed_sequence(
                database_parent
            )
        return parent_sequence_offsets[parent_key] + sibling_sequence[uid]

    def _get_or_create_wbs_version(uid: str) -> WBSNodeVersion:
        """Create an imported summary row and its missing ancestors."""
        existing = uid_to_wbs_version.get(uid)
        if existing is not None:
            return existing

        info = tasks_info[uid]
        if not info["is_summary"]:
            raise ValueError(f"MSP UID {uid} is not a summary row.")

        parent_uid = parent_map[uid]
        if parent_uid is None:
            database_parent = import_root
        else:
            database_parent = _get_or_create_wbs_version(parent_uid)

        node = WBSNode.objects.create(project=project)
        version = WBSNodeVersion.objects.create(
            node=node,
            revision=revision,
            parent=database_parent,
            title=info["name"],
            sequence=_sequence_for(uid, database_parent),
            planned_start=_parse_dt(info["start"]),
            planned_finish=_parse_dt(info["finish"]),
        )
        uid_to_wbs_version[uid] = version
        return version

    imported_tasks = 0

    # A single source-order pass is easier to reason about. WBS ancestors are
    # still created recursively when needed, but every saved row receives the
    # mixed sibling sequence computed from its original XML position.
    for uid, info in tasks_info.items():
        if info["is_summary"]:
            _get_or_create_wbs_version(uid)
            continue

        parent_uid = parent_map[uid]
        if parent_uid is None:
            database_parent = import_root
        else:
            database_parent = _get_or_create_wbs_version(parent_uid)

        base_task = Task.objects.create(project=project)
        TaskVersion.objects.create(
            task=base_task,
            revision=revision,
            wbs_node=database_parent,
            title=info["name"],
            duration_hours=_parse_duration(info["duration"]),
            planned_start=_parse_dt(info["start"]),
            planned_finish=_parse_dt(info["finish"]),
            sequence=_sequence_for(uid, database_parent),
        )
        uid_to_task[uid] = base_task
        imported_tasks += 1

    imported_dependencies = 0
    for uid, info in tasks_info.items():
        if info["is_summary"]:
            continue

        successor = uid_to_task.get(uid)
        if successor is None:
            continue

        for predecessor_uid, link_type, lag_hours in info["predecessors"]:
            predecessor = uid_to_task.get(predecessor_uid)
            if predecessor is None:
                warnings.append(
                    f"Predecessor UID {predecessor_uid} for task UID {uid} "
                    "was not imported as a leaf task; dependency skipped."
                )
                continue

            _, created = Dependency.objects.get_or_create(
                revision=revision,
                predecessor=predecessor,
                successor=successor,
                defaults={
                    "dependency_type": link_type,
                    "lag_hours": lag_hours,
                },
            )
            if created:
                imported_dependencies += 1

    return {
        "project_id": str(project.id),
        "revision_id": revision.id,
        "wbs_nodes": len(uid_to_wbs_version),
        "tasks_imported": imported_tasks,
        "dependencies_imported": imported_dependencies,
        "warnings": warnings,
        "message": "Import completed successfully.",
    }
