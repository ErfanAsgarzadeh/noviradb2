"""
Microsoft Project XML exporter.

The generated file is a standard MSP XML document. Microsoft Project can open
it directly via File > Open, while we keep the extension as .xml because that is
the native interchange format.
"""
from __future__ import annotations

from decimal import Decimal
from xml.etree import ElementTree as ET

from django.utils import timezone

from .models import Dependency, Revision, TaskVersion, WBSNodeVersion

MSP_NS = "http://schemas.microsoft.com/project"
ET.register_namespace("", MSP_NS)


def _tag(name: str) -> str:
    return f"{{{MSP_NS}}}{name}"


def _child(parent: ET.Element, name: str, text=None) -> ET.Element:
    node = ET.SubElement(parent, _tag(name))
    if text is not None:
        node.text = str(text)
    return node


def _format_dt(value) -> str:
    if value is None:
        value = timezone.now()
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _duration_to_msp(hours) -> str:
    try:
        total_minutes = int(round(Decimal(str(hours or 0)) * Decimal("60")))
    except Exception:
        total_minutes = 0
    total_minutes = max(0, total_minutes)
    h, m = divmod(total_minutes, 60)
    return f"PT{h}H{m}M0S"


def _safe_finish(start, finish, duration_hours):
    if finish:
        return finish
    if start:
        return start
    return timezone.now()


def _task_common(
    task_el: ET.Element,
    *,
    uid: int,
    name: str,
    outline_level: int,
    outline_number: str,
    start,
    finish,
    duration_hours,
    summary: bool,
    sequence: int,
) -> None:
    finish_value = _safe_finish(start, finish, duration_hours)
    start_value = start or finish_value

    _child(task_el, "UID", uid)
    _child(task_el, "ID", uid)
    _child(task_el, "Name", name or f"Task {uid}")
    _child(task_el, "Type", 1)
    _child(task_el, "IsNull", 0)
    _child(task_el, "CreateDate", _format_dt(timezone.now()))
    _child(task_el, "WBS", outline_number)
    _child(task_el, "OutlineNumber", outline_number)
    _child(task_el, "OutlineLevel", outline_level)
    _child(task_el, "Priority", 500)
    _child(task_el, "Start", _format_dt(start_value))
    _child(task_el, "Finish", _format_dt(finish_value))
    _child(task_el, "Duration", _duration_to_msp(duration_hours))
    _child(task_el, "DurationFormat", 7)
    _child(task_el, "Work", _duration_to_msp(0 if summary else duration_hours))
    _child(task_el, "ResumeValid", 0)
    _child(task_el, "EffortDriven", 0)
    _child(task_el, "Recurring", 0)
    _child(task_el, "OverAllocated", 0)
    _child(task_el, "Estimated", 0)
    _child(task_el, "Milestone", 1 if not summary and Decimal(str(duration_hours or 0)) == 0 else 0)
    _child(task_el, "Summary", 1 if summary else 0)
    _child(task_el, "Critical", 0)
    _child(task_el, "IsSubproject", 0)
    _child(task_el, "IsSubprojectReadOnly", 0)
    _child(task_el, "ExternalTask", 0)
    _child(task_el, "Manual", 0)
    _child(task_el, "Active", 1)
    _child(task_el, "Sequence", sequence)


def _build_wbs_duration(wbs: WBSNodeVersion, tasks_by_wbs: dict[int, list[TaskVersion]]) -> Decimal:
    descendants = [wbs.id] + list(
        wbs.get_descendants().filter(is_deleted=False).values_list("id", flat=True)
    )
    task_versions = []
    for wbs_id in descendants:
        task_versions.extend(tasks_by_wbs.get(wbs_id, []))
    total = Decimal("0")
    for task in task_versions:
        total += Decimal(str(task.duration_hours or 0))
    return total


def export_revision_to_msp_xml(revision: Revision) -> bytes:
    project = revision.project
    wbs_nodes = list(
        WBSNodeVersion.objects.filter(revision=revision, is_deleted=False)
        .select_related("node", "parent", "parent__node")
        .order_by("tree_id", "lft", "sequence")
    )
    tasks = list(
        TaskVersion.objects.filter(revision=revision, is_deleted=False)
        .select_related("task", "wbs_node", "wbs_node__node")
        .order_by("wbs_node__tree_id", "wbs_node__lft", "sequence", "planned_start", "id")
    )
    dependencies = list(
        Dependency.objects.filter(revision=revision)
        .select_related("predecessor", "successor")
    )

    tasks_by_wbs: dict[int, list[TaskVersion]] = {}
    for task in tasks:
        tasks_by_wbs.setdefault(task.wbs_node_id, []).append(task)

    child_wbs_by_parent: dict[int | None, list[WBSNodeVersion]] = {}
    for wbs in wbs_nodes:
        child_wbs_by_parent.setdefault(wbs.parent_id, []).append(wbs)

    root = ET.Element(_tag("Project"))
    _child(root, "Name", f"{project.name} - Rev {revision.number}")
    _child(root, "Title", project.name)
    _child(root, "Company", "NOVIRA")
    _child(root, "ScheduleFromStart", 1)
    _child(root, "StartDate", _format_dt(revision.project_start))
    _child(root, "FinishDate", _format_dt(revision.project_end or project.end_date or revision.project_start))
    _child(root, "CurrentDate", _format_dt(timezone.now()))
    _child(root, "MinutesPerDay", 480)
    _child(root, "MinutesPerWeek", 2400)
    _child(root, "DaysPerMonth", 20)
    _child(root, "DefaultStartTime", "08:00:00")
    _child(root, "DefaultFinishTime", "17:00:00")
    _child(root, "CurrencyDigits", 2)
    _child(root, "CurrencySymbol", "")
    _child(root, "CurrencyCode", "IRR")

    tasks_el = _child(root, "Tasks")

    uid_counter = 0
    task_uid_by_task_id: dict[str, int] = {}

    def next_uid() -> int:
        nonlocal uid_counter
        uid_counter += 1
        return uid_counter

    def add_wbs_branch(wbs: WBSNodeVersion, outline_number: str, outline_level: int) -> None:
        uid = next_uid()
        wbs_duration = _build_wbs_duration(wbs, tasks_by_wbs)
        task_el = _child(tasks_el, "Task")
        _task_common(
            task_el,
            uid=uid,
            name=wbs.title,
            outline_level=outline_level,
            outline_number=outline_number,
            start=wbs.planned_start or revision.project_start,
            finish=wbs.planned_finish or revision.project_end or wbs.planned_start or revision.project_start,
            duration_hours=wbs_duration,
            summary=True,
            sequence=wbs.sequence,
        )

        for index, child in enumerate(child_wbs_by_parent.get(wbs.id, []), start=1):
            add_wbs_branch(child, f"{outline_number}.{index}", outline_level + 1)

        for index, task in enumerate(tasks_by_wbs.get(wbs.id, []), start=1):
            task_uid = next_uid()
            task_uid_by_task_id[str(task.task_id)] = task_uid
            task_el = _child(tasks_el, "Task")
            task_outline = f"{outline_number}.{len(child_wbs_by_parent.get(wbs.id, [])) + index}"
            _task_common(
                task_el,
                uid=task_uid,
                name=task.title,
                outline_level=outline_level + 1,
                outline_number=task_outline,
                start=task.planned_start,
                finish=task.planned_finish,
                duration_hours=task.duration_hours,
                summary=False,
                sequence=task.sequence,
            )

    roots = child_wbs_by_parent.get(None, [])
    for root_index, root_wbs in enumerate(roots, start=1):
        add_wbs_branch(root_wbs, str(root_index), 1)

    link_type_to_msp = {"FF": 0, "FS": 1, "SS": 2, "SF": 3}
    task_elements_by_uid = {
        int(task_el.findtext(_tag("UID"))): task_el
        for task_el in tasks_el.findall(_tag("Task"))
        if task_el.findtext(_tag("Summary")) == "0"
    }
    for dep in dependencies:
        successor_uid = task_uid_by_task_id.get(str(dep.successor_id))
        predecessor_uid = task_uid_by_task_id.get(str(dep.predecessor_id))
        if not successor_uid or not predecessor_uid:
            continue
        successor_el = task_elements_by_uid.get(successor_uid)
        if successor_el is None:
            continue
        pred_el = _child(successor_el, "PredecessorLink")
        _child(pred_el, "PredecessorUID", predecessor_uid)
        _child(pred_el, "Type", link_type_to_msp.get(dep.dependency_type, 1))
        _child(pred_el, "CrossProject", 0)
        _child(pred_el, "LinkLag", int(dep.lag_hours or 0) * 60)
        _child(pred_el, "LagFormat", 7)

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return xml_bytes
