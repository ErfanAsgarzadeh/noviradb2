"""
MSP XML Importer — msp_importer.py
Imports Tasks and WBS hierarchy from a Microsoft Project XML file
into an existing Project + Revision.
"""
import string
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from typing import IO, Any
import re

from django.db import transaction
from django.contrib.auth import get_user_model
from django.db.models import Max
from django.utils import timezone

from .models import (
    Project, Revision,
    WBSNode, WBSNodeVersion,
    Task, TaskVersion,
    Dependency,
)

User = get_user_model()

MSP_NS = "http://schemas.microsoft.com/project"
NS = {"m": MSP_NS}


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _tag(name: str) -> str:
    return f"{{{MSP_NS}}}{name}"


def _find(el, path: str):
    return el.find(f"m:{path}", NS)


def _text(el, path: str, default=None):
    node = _find(el, path)
    return node.text.strip() if node is not None and node.text is not None else default


def _int(el, path: str, default=0) -> int:
    try:
        return int(_text(el, path))
    except (ValueError, TypeError):
        return default


def _decimal(el, path: str, default=Decimal("0")) -> Decimal:
    try:
        return Decimal(str(_text(el, path)))
    except Exception:
        return default


def _bool(el, path: str, default=False) -> bool:
    v = _text(el, path)
    return v.strip() in ("1", "true", "True") if v else default


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            continue
    return None


def _parse_duration(raw: str | None) -> Decimal:
    """Convert MSP duration string (e.g. PT8H, P1DT2H) to decimal hours."""
    if not raw:
        return Decimal("0")
    total = Decimal("0")
    days = re.search(r"(\d+(?:\.\d+)?)D", raw)
    hours = re.search(r"(\d+(?:\.\d+)?)H", raw)
    if days:
        total += Decimal(days.group(1)) * 8
    if hours:
        total += Decimal(hours.group(1))
    return total


# ---------------------------------------------------------------------------
# WBS outline-level parser
# ---------------------------------------------------------------------------

def _build_wbs_outline(tasks_el) -> dict:
    """
    Walk all <Task> elements and reconstruct the WBS parent-child
    relationships using the OutlineLevel field that MSP exports.

    Returns a dict keyed by UID (str) with shape:
        {
            uid: {
                "name": str,
                "outline_level": int,
                "outline_number": str,   # e.g. "1.2.3"
                "is_summary": bool,
                "start": str | None,
                "finish": str | None,
                "duration": str | None,
                "predecessors": [(pred_uid, link_type, lag_hours), ...]
            }
        }
    """
    tasks = {}
    for t_el in tasks_el.findall("m:Task", NS):
        uid = _text(t_el, "UID")
        if uid is None or uid == "0":
            continue

        name = _text(t_el, "Name") or f"Unnamed Task (UID: {uid})"
        is_summary = _bool(t_el, "Summary")
        outline_level = _int(t_el, "OutlineLevel", default=1)
        outline_number = _text(t_el, "OutlineNumber", default="")

        # Collect predecessor links
        predecessors = []
        preds_el = _find(t_el, "PredecessorLink")
        # PredecessorLink may repeat; iterate all siblings manually
        for pl in t_el.findall("m:PredecessorLink", NS):
            pred_uid = _text(pl, "PredecessorUID")
            link_type_int = _int(pl, "Type", default=1)  # 1=FS,0=FF,2=SS,3=SF
            lag_minutes = _int(pl, "LinkLag", default=0)
            lag_hours = round(lag_minutes / 60)
            type_map = {0: "FF", 1: "FS", 2: "SS", 3: "SF"}
            link_type = type_map.get(link_type_int, "FS")
            if pred_uid and pred_uid != "0":
                predecessors.append((pred_uid, link_type, lag_hours))

        tasks[uid] = {
            "name": name,
            "outline_level": outline_level,
            "outline_number": outline_number,
            "is_summary": is_summary,
            "start": _text(t_el, "Start"),
            "finish": _text(t_el, "Finish"),
            "duration": _text(t_el, "Duration"),
            "predecessors": predecessors,
        }
    return tasks


def _infer_parents(tasks: dict) -> dict[str, str | None]:
    """
    Given the ordered dict of tasks with outline_level,
    compute parent_uid for each task using a stack-based approach.

    Returns: {uid: parent_uid_or_None}
    """
    parent_map: dict[str, str | None] = {}
    # Stack of (outline_level, uid)
    stack: list[tuple[int, str]] = []

    for uid, info in tasks.items():
        level = info["outline_level"]

        # Pop stack until we find a node at a strictly lower level
        while stack and stack[-1][0] >= level:
            stack.pop()

        parent_uid = stack[-1][1] if stack else None
        parent_map[uid] = parent_uid
        stack.append((level, uid))

    return parent_map


# ---------------------------------------------------------------------------
# Main importer
# ---------------------------------------------------------------------------

@transaction.atomic
def import_msp_xml(
    xml_file: IO[bytes],
    project_id: string,
    revision_id: int,
    active_node_id: string | None = None,
    user: Any = None,
) -> dict:
    warnings: list[str] = []

    project = Project.objects.get(pk=project_id)
    revision = Revision.objects.get(pk=revision_id, project=project)

    tree = ET.parse(xml_file)
    root = tree.getroot()

    # Handle files exported without a namespace
    if not root.tag.startswith("{"):
        global NS, MSP_NS
        MSP_NS = ""
        NS = {}

    tasks_el = _find(root, "Tasks")
    if tasks_el is None:
        return {"error": "No <Tasks> element found in XML."}

    # ------------------------------------------------------------------
    # 1. Parse all task metadata and infer WBS parent relationships
    # ------------------------------------------------------------------
    tasks_info = _build_wbs_outline(tasks_el)
    if not tasks_info:
        return {"error": "No tasks found in XML."}

    parent_map = _infer_parents(tasks_info)

    # ------------------------------------------------------------------
    # 2. Determine starting sequence numbers to avoid collisions
    # ------------------------------------------------------------------
    last_wbs_seq = (
        WBSNodeVersion.objects.filter(revision=revision)
        .aggregate(Max("sequence"))["sequence__max"] or 0
    )
    last_task_seq = (
        TaskVersion.objects.filter(revision=revision)
        .aggregate(Max("sequence"))["sequence__max"] or 0
    )

    # ------------------------------------------------------------------
    # 3. Create WBSNode / WBSNodeVersion for every summary (and root)
    #    uid_to_wbs_version: maps MSP task UID → WBSNodeVersion instance
    # ------------------------------------------------------------------
    uid_to_wbs_version: dict[str, WBSNodeVersion] = {}

    # ------------------------------------------------------------------
    # Resolve the WBSNodeVersion that will act as the import root.
    #
    # If active_node_id is supplied (the node selected in the UI), import
    # the entire MSP tree as children of that node.
    # Otherwise fall back to the revision root (created by post_save signal).
    # ------------------------------------------------------------------
    if active_node_id is not None:
        try:
            root_wbs_version = WBSNodeVersion.objects.get(
                node__id=active_node_id, revision=revision
            )
        except WBSNodeVersion.DoesNotExist:
            return {
                "error": (
                    f"Selected node (id={active_node_id}) does not belong to "
                    f"revision {revision_id}. Import aborted."
                )
            }
    else:
        root_wbs_version = WBSNodeVersion.objects.filter(
            revision=revision, parent=None
        ).first()

    if root_wbs_version is None:
        # Safety-net: create a root if it somehow does not exist.
        root_wbs_node = WBSNode.objects.create(project=project)
        root_wbs_version = WBSNodeVersion.objects.create(
            node=root_wbs_node,
            revision=revision,
            title=f"Root: {project.name}",
            sequence=1,
            parent=None,
        )
        warnings.append("Root WBSNodeVersion was missing and has been created.")

    wbs_seq_counter = last_wbs_seq

    def _get_or_create_wbs_version(uid: str) -> WBSNodeVersion:
        """
        Recursively ensure the WBSNodeVersion for a given UID exists,
        creating parent nodes first if needed.
        """
        nonlocal wbs_seq_counter

        if uid in uid_to_wbs_version:
            return uid_to_wbs_version[uid]

        info = tasks_info[uid]
        parent_uid = parent_map[uid]

        if parent_uid is None:
            parent_version = root_wbs_version
        else:
            parent_version = _get_or_create_wbs_version(parent_uid)

        wbs_seq_counter += 1
        wbs_node = WBSNode.objects.create(project=project)
        wbs_version = WBSNodeVersion.objects.create(
            node=wbs_node,
            revision=revision,
            parent=parent_version,
            title=info["name"],
            sequence=wbs_seq_counter,
            planned_start=_parse_dt(info["start"]),
            planned_finish=_parse_dt(info["finish"]),
        )
        uid_to_wbs_version[uid] = wbs_version
        return wbs_version

    # Build WBSNodeVersions for all summary tasks first (they are the tree nodes)
    for uid, info in tasks_info.items():
        if info["is_summary"]:
            _get_or_create_wbs_version(uid)

    # ------------------------------------------------------------------
    # 4. Create Task + TaskVersion for every leaf (non-summary) task
    #    uid_to_task: maps MSP task UID → Task instance (for dependencies)
    # ------------------------------------------------------------------
    uid_to_task: dict[str, Task] = {}
    task_seq_counter = last_task_seq
    imported_tasks = 0

    for uid, info in tasks_info.items():
        if info["is_summary"]:
            continue  # summary rows become WBS nodes, not tasks

        # The parent of this leaf task is its WBS node.
        # If it has no summary parent, it hangs directly under root.
        parent_uid = parent_map[uid]
        if parent_uid is not None and tasks_info[parent_uid]["is_summary"]:
            wbs_version = _get_or_create_wbs_version(parent_uid)
        else:
            # Leaf with no summary parent — use root
            wbs_version = root_wbs_version

        task_seq_counter += 1
        base_task = Task.objects.create(project=project)
        TaskVersion.objects.create(
            task=base_task,
            revision=revision,
            wbs_node=wbs_version,
            title=info["name"],
            duration_hours=_parse_duration(info["duration"]),
            planned_start=_parse_dt(info["start"]),
            planned_finish=_parse_dt(info["finish"]),
            sequence=task_seq_counter,
        )
        uid_to_task[uid] = base_task
        imported_tasks += 1

    # ------------------------------------------------------------------
    # 5. Import Dependency links between leaf tasks
    # ------------------------------------------------------------------
    imported_deps = 0
    for uid, info in tasks_info.items():
        if info["is_summary"]:
            continue
        successor_task = uid_to_task.get(uid)
        if successor_task is None:
            continue

        for pred_uid, link_type, lag_hours in info["predecessors"]:
            predecessor_task = uid_to_task.get(pred_uid)
            if predecessor_task is None:
                warnings.append(
                    f"Predecessor UID {pred_uid} for task UID {uid} not found "
                    f"(may be a summary task or missing); link skipped."
                )
                continue

            _, created = Dependency.objects.get_or_create(
                revision=revision,
                predecessor=predecessor_task,
                successor=successor_task,
                defaults={"dependency_type": link_type, "lag_hours": lag_hours},
            )
            if created:
                imported_deps += 1

    return {
        "project_id": str(project.id),
        "revision_id": revision.id,
        "wbs_nodes": len(uid_to_wbs_version),
        "tasks_imported": imported_tasks,
        "dependencies_imported": imported_deps,
        "warnings": warnings,
        "message": "Import completed successfully.",
    }
