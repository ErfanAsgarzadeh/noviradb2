"""
موتور تسطیح منابع چندپروژه‌ای (Multi-Project Resource Leveling)
─────────────────────────────────────────────────────────────────
ویژگی‌های اصلی:
- لایه شبیه‌سازی: خروجی در TaskLevelingMetrics ذخیره می‌شود، نه TaskVersion.
- پشتیبانی از چند پروژه: دریافت یک GlobalLevelingRun شامل چندین پروژه.
- منابع یکپارچه: کسر ظرفیت منابع به صورت سراسری انجام می‌شود.
- ذخیره هیستوگرام: خروجی توزیع روزانه در ResourceUsage (متصل به LevelingRun) ذخیره می‌شود.
"""

from __future__ import annotations

import datetime
import heapq
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from django.db import transaction

from .cpm import CPMEngine, TaskNode, EdgeInfo
from .calendar import CalendarEngine
from .models import (
    Assignment,
    Resource,
    ResourceUsage,
    TaskVersion,
    TaskLevelingMetrics,
    GlobalLevelingRun,
    Revision
)

logger = logging.getLogger(__name__)


class MultiProjectLevelingEngine:
    def __init__(self, leveling_run: GlobalLevelingRun):
        self.leveling_run = leveling_run

        # معیارهای اولویت‌بندی تسک‌ها برای رقابت بر سر منابع (قابل تنظیم توسط کاربر روی GlobalLevelingRun)
        self.priority_rules: List[dict] = leveling_run.get_priority_rules()

        # مجموعه‌ای از موتورهای CPM برای هر پروژه (برای دسترسی به گراف وابستگی‌ها و تقویم تسک‌ها)
        # key: revision_id -> CPMEngine
        self.cpm_engines: Dict[int, CPMEngine] = {}

        # تجمیع تمام Nodeها از همه پروژه‌ها در یک دیکشنری مشترک
        # key: task_id (str) -> TaskNode
        self.global_nodes: Dict[str, TaskNode] = {}

        # Engines تقویم برای منابع: resource_id -> CalendarEngine
        self.resource_cal_engines: Dict[int, CalendarEngine] = {}
        self.resources: Dict[int, Resource] = {}

        # کش TaskVersion و اولویت پروژه برای استفاده در محاسبه معیارهای اولویت‌بندی (بدون کوئری تکراری)
        # key: tv_id (str) -> TaskVersion
        self.task_versions: Dict[str, TaskVersion] = {}
        # key: tv_id (str) -> project priority (int)
        self.project_priority: Dict[str, int] = {}

        # ظرفیت باقیمانده منابع در هر روز
        # structure: resource_id -> date -> remaining_hours
        self.remaining_capacity: Dict[int, Dict[datetime.date, float]] = defaultdict(dict)

        # تخصیص‌های هر تسک: task_id -> list of assignments
        self.task_assignments: Dict[str, List[dict]] = defaultdict(list)

        # نتایج شبیه‌سازی تسطیح
        self.leveled_starts: Dict[str, datetime.datetime] = {}
        self.leveled_finishes: Dict[str, datetime.datetime] = {}

        # برای ذخیره در ResourceUsage
        self.daily_planned_usage: Dict[Tuple[int, datetime.date], float] = defaultdict(float)

    def _load_and_prepare_data(self) -> None:
        """Load the exact revision snapshots selected by this independent plan."""
        entries = list(
            self.leveling_run.plan_projects.select_related("project", "revision")
        )
        active_revisions = [entry.revision for entry in entries]
        if not active_revisions:
            raise ValueError("Select at least one project revision before running leveling.")

        plan_priority = {entry.project_id: entry.priority for entry in entries}

        # Run CPM entirely in memory. Calling CPMEngine.run() would persist dates
        # into TaskVersion, which is forbidden for an independent leveling plan.
        for revision in active_revisions:
            engine = CPMEngine(revision, data_date=self.leveling_run.data_date)
            engine._load()
            if engine.nodes:
                engine._normalize_data_date()
                order = engine._topological_sort()
                engine._forward_pass(order)
                engine._backward_pass(order)
                engine._compute_floats()
            self.cpm_engines[revision.id] = engine
            for node in engine.nodes.values():
                if not node.is_external:
                    self.global_nodes[str(node.tv_id)] = node

        tv_ids = [int(tv_id) for tv_id in self.global_nodes]
        for task_version in TaskVersion.objects.filter(id__in=tv_ids).select_related(
            "revision", "revision__project"
        ):
            key = str(task_version.id)
            self.task_versions[key] = task_version
            self.project_priority[key] = plan_priority.get(
                task_version.revision.project_id,
                task_version.revision.project.priority,
            )

        assignments = list(
            Assignment.objects.filter(revision__in=active_revisions)
            .select_related("resource", "resource__calendar")
        )
        resource_ids = {assignment.resource_id for assignment in assignments}
        resources = Resource.objects.filter(
            id__in=resource_ids, is_active=True
        ).select_related("calendar")

        for resource in resources:
            self.resources[resource.id] = resource
            self.resource_cal_engines[resource.id] = (
                CalendarEngine(resource.calendar) if resource.calendar_id else None
            )

        task_to_version = {
            str(node.task_id): str(node.tv_id)
            for node in self.global_nodes.values()
        }
        for assignment in assignments:
            version_id = task_to_version.get(str(assignment.task_id))
            if version_id and assignment.resource_id in self.resources:
                self.task_assignments[version_id].append({
                    "resource_id": assignment.resource_id,
                    "units_percent": float(assignment.units_percent) / 100.0,
                })

    def _get_resource_capacity(
        self,
        resource_id: int,
        date: datetime.date,
        fallback_cal_engine: Optional[CalendarEngine] = None,
    ) -> float:
        if date not in self.remaining_capacity[resource_id]:
            res = self.resources[resource_id]
            cal_engine = self.resource_cal_engines.get(resource_id)
            if cal_engine is not None:
                base_hours = cal_engine.get_day_schedule(date).total_hours
            elif fallback_cal_engine is not None:
                base_hours = fallback_cal_engine.get_day_schedule(date).total_hours
            else:
                base_hours = 8.0 if date.weekday() < 5 else 0.0
            max_units_factor = float(res.max_units) / 100.0
            self.remaining_capacity[resource_id][date] = base_hours * max_units_factor
        return self.remaining_capacity[resource_id][date]

    def _get_cpm_engine_for_node(self, node: TaskNode) -> CPMEngine:
        """پیدا کردن موتور CPM مربوط به پروژه‌ی یک نود (برای استفاده از متدهای تقویم)."""
        # با توجه به اینکه node متعلق به یک revision است، باید موتور آن را پیدا کنیم
        # در CPMEngine شما tv_id دارید. باید ببینیم این tv_id مال کدام revision است.
        from .models import TaskVersion
        tv = TaskVersion.objects.get(id=node.tv_id)
        return self.cpm_engines[tv.revision_id]

    # ------------------------------------------------------------------
    # اولویت‌بندی قابل تنظیم تسک‌ها برای رقابت بر سر منابع
    # ------------------------------------------------------------------

    # اولویت پیش‌فرض وقتی تسکی هیچ تخصیص منبعی ندارد (نه بهترین و نه بدترین)
    DEFAULT_RESOURCE_PRIORITY = 100

    def _get_task_priority_value(self, node: TaskNode, criterion: str) -> float:
        """محاسبه‌ی مقدار عددیِ یک معیار مشخص برای یک تسک؛ برای مقایسه در صف اولویت استفاده می‌شود."""
        tv_id_str = str(node.tv_id)
        tv = self.task_versions.get(tv_id_str)

        if criterion == GlobalLevelingRun.PRIORITY_TOTAL_FLOAT:
            return float(node.total_float_hours or 0)

        if criterion == GlobalLevelingRun.PRIORITY_LATE_START:
            return node.late_start.timestamp() if node.late_start else 0.0

        if criterion == GlobalLevelingRun.PRIORITY_EARLY_START:
            return node.early_start.timestamp() if node.early_start else 0.0

        if criterion == GlobalLevelingRun.PRIORITY_PLANNED_START:
            if tv and tv.planned_start:
                return tv.planned_start.timestamp()
            # اگر تسک تاریخ برنامه‌ریزی‌شده نداشت، از early_start به عنوان جایگزین استفاده می‌شود
            return node.early_start.timestamp() if node.early_start else 0.0

        if criterion == GlobalLevelingRun.PRIORITY_TASK_WEIGHT:
            return float(tv.weight) if tv is not None else 0.0

        if criterion == GlobalLevelingRun.PRIORITY_SEQUENCE:
            return float(tv.sequence) if tv is not None else 0.0

        if criterion == GlobalLevelingRun.PRIORITY_RESOURCE_PRIORITY:
            assignments = self.task_assignments.get(tv_id_str, [])
            priorities = [
                self.resources[a["resource_id"]].priority
                for a in assignments
                if a["resource_id"] in self.resources
            ]
            # عدد کمتر در Resource.priority یعنی اولویت بالاتر؛ وقتی چند منبع به یک تسک تخصیص دارد
            # بالاترین اولویت (کمترین عدد) بین منابع تسک ملاک قرار می‌گیرد
            return float(min(priorities)) if priorities else float(self.DEFAULT_RESOURCE_PRIORITY)

        if criterion == GlobalLevelingRun.PRIORITY_PROJECT_PRIORITY:
            return float(self.project_priority.get(tv_id_str, self.DEFAULT_RESOURCE_PRIORITY))

        raise ValueError(f"معیار اولویت‌بندی نامعتبر یا پشتیبانی‌نشده: {criterion}")

    def _build_priority_key(self, node: TaskNode) -> tuple:
        """
        ساخت تاپل مرتب‌سازی نهایی بر اساس self.priority_rules.
        اولین معیار مهم‌ترین است؛ در صورت تساوی، معیار بعدی تعیین‌کننده خواهد بود.
        برای معیارهای «desc» مقدار منفی می‌شود تا با heapq (که صعودی/min-heap است) سازگار بماند.
        در انتها یک شناسه‌ی رشته‌ای (tv_id) برای شکستن تساوی کامل و پایدار بودن مرتب‌سازی اضافه می‌شود.
        """
        key_parts = []
        for rule in self.priority_rules:
            value = self._get_task_priority_value(node, rule["criterion"])
            key_parts.append(-value if rule["direction"] == "desc" else value)
        key_parts.append(str(node.tv_id))
        return tuple(key_parts)

    def _distribute_task_hours(
        self,
        start_dt: datetime.datetime,
        duration: float,
        cal_engine: Optional[CalendarEngine],
    ) -> Dict[datetime.date, float]:
        dist = {}
        current = start_dt
        remaining = duration
        limit = 1000

        while remaining > 0 and limit > 0:
            limit -= 1
            if cal_engine:
                sched = cal_engine.get_day_schedule(current.date())
                if not sched.is_working():
                    current = current.replace(hour=0, minute=0) + datetime.timedelta(days=1)
                    continue
                available_today = sched.hours_from(current.time())
            else:
                # A resource without an explicit calendar uses the standard
                # 08:00-16:00, Monday-Friday working pattern.
                if current.weekday() >= 5:
                    current = current.replace(hour=8, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
                    continue
                day_start = current.replace(hour=8, minute=0, second=0, microsecond=0)
                day_end = current.replace(hour=16, minute=0, second=0, microsecond=0)
                if current < day_start:
                    current = day_start
                if current >= day_end:
                    current = day_start + datetime.timedelta(days=1)
                    continue
                available_today = (day_end - current).total_seconds() / 3600

            if available_today <= 0:
                current = current.replace(hour=0, minute=0) + datetime.timedelta(days=1)
                continue

            take = min(remaining, available_today)
            dist[current.date()] = dist.get(current.date(), 0.0) + take
            remaining -= take
            if remaining > 0:
                current = current.replace(hour=0, minute=0) + datetime.timedelta(days=1)

        return dist
    def _earliest_start_from_predecessors(self, node: TaskNode) -> datetime.datetime:
        cpm = self._get_cpm_engine_for_node(node)
        min_start = max(
            cpm.revision.project_start,
            node.early_start or cpm.revision.project_start,
        )
        candidates = []

        for edge in node.predecessors:
            # در گراف شما from_task_id آیدی Task است. باید تبدیلش کنیم به tv_id
            tv_mapping = {n.task_id: str(n.tv_id) for n in self.global_nodes.values()}
            pred_tv_id = tv_mapping.get(edge.from_task_id)

            if pred_tv_id and pred_tv_id in self.leveled_finishes:
                pred_start = self.leveled_starts[pred_tv_id]
                pred_finish = self.leveled_finishes[pred_tv_id]

                if edge.dep_type == "FS":
                    candidates.append(cpm._add_hours(node, pred_finish, edge.lag_hours))
                elif edge.dep_type == "SS":
                    candidates.append(cpm._add_hours(node, pred_start, edge.lag_hours))
                elif edge.dep_type == "FF":
                    ef_lagged = cpm._add_hours(node, pred_finish, edge.lag_hours)
                    candidates.append(cpm._subtract_hours(node, ef_lagged, node.duration_hours))
                elif edge.dep_type == "SF":
                    es_lagged = cpm._add_hours(node, pred_start, edge.lag_hours)
                    candidates.append(cpm._subtract_hours(node, es_lagged, node.duration_hours))

        if candidates:
            min_start = max(candidates)
        return min_start

    def _find_window_and_consume(self, node: TaskNode, min_start: datetime.datetime) -> Tuple[datetime.datetime, datetime.datetime]:
        cpm = self._get_cpm_engine_for_node(node)
        cal_engine = cpm._get_engine(node) or cpm._default_cal_engine

        tv_id_str = str(node.tv_id)
        assignments = self.task_assignments.get(tv_id_str, [])

        if not assignments or node.duration_hours <= 0:
            start = cal_engine.next_working_moment(min_start) if cal_engine else min_start
            finish = cpm._add_hours(node, start, node.duration_hours)
            return start, finish

        current_attempt = min_start
        limit = 1000

        while limit > 0:
            limit -= 1
            if cal_engine:
                current_attempt = cal_engine.next_working_moment(current_attempt)

            daily_distribution = self._distribute_task_hours(current_attempt, node.duration_hours, cal_engine)

            can_schedule = True
            for asgn in assignments:
                res_id = asgn["resource_id"]
                req_percent = asgn["units_percent"]

                for date, task_hours in daily_distribution.items():
                    res_needed = task_hours * req_percent
                    available = self._get_resource_capacity(res_id, date, cal_engine)
                    if res_needed > available + 0.001:
                        can_schedule = False
                        break
                if not can_schedule:
                    break

            if can_schedule:
                for asgn in assignments:
                    res_id = asgn["resource_id"]
                    req_percent = asgn["units_percent"]
                    for date, task_hours in daily_distribution.items():
                        consumed = task_hours * req_percent
                        self.remaining_capacity[res_id][date] -= consumed
                        self.daily_planned_usage[(res_id, date)] += consumed

                finish = cpm._add_hours(node, current_attempt, node.duration_hours)
                return current_attempt, finish

            current_attempt = current_attempt.replace(hour=0, minute=0) + datetime.timedelta(days=1)

        finish = cpm._add_hours(node, min_start, node.duration_hours)
        return min_start, finish

    @transaction.atomic
    def run(self) -> dict:
        self._load_and_prepare_data()

        if not self.global_nodes:
            return {"status": "No tasks to level"}

        # محاسبه In-Degree (تعداد پیش‌نیازها) برای تمام نودها
        in_degree = {tv_id: 0 for tv_id in self.global_nodes}
        for node in self.global_nodes.values():
            for edge in node.successors:
                # تبدیل task_id به tv_id
                tv_mapping = {n.task_id: str(n.tv_id) for n in self.global_nodes.values()}
                succ_tv_id = tv_mapping.get(edge.to_task_id)
                if succ_tv_id and succ_tv_id in in_degree:
                    in_degree[succ_tv_id] += 1

        # ساخت Priority Queue سراسری (همه پروژه‌ها با هم رقابت می‌کنند)
        # اولویت بر اساس self.priority_rules تعیین می‌شود (پیش‌فرض: 1. Total Float کمتر 2. Late Start زودتر)
        pq = []
        for tv_id, deg in in_degree.items():
            if deg == 0:
                n = self.global_nodes[tv_id]
                heapq.heappush(pq, (self._build_priority_key(n), tv_id))

        # حلقه Leveling
        while pq:
            _, tv_id = heapq.heappop(pq)
            node = self.global_nodes[tv_id]

            min_start = self._earliest_start_from_predecessors(node)
            actual_start, actual_finish = self._find_window_and_consume(node, min_start)

            self.leveled_starts[tv_id] = actual_start
            self.leveled_finishes[tv_id] = actual_finish

            for edge in node.successors:
                tv_mapping = {n.task_id: str(n.tv_id) for n in self.global_nodes.values()}
                succ_tv_id = tv_mapping.get(edge.to_task_id)
                if succ_tv_id and succ_tv_id in in_degree:
                    in_degree[succ_tv_id] -= 1
                    if in_degree[succ_tv_id] == 0:
                        succ_node = self.global_nodes[succ_tv_id]
                        heapq.heappush(pq, (self._build_priority_key(succ_node), succ_tv_id))

        # === ذخیره در لایه شبیه‌سازی (Simulation Layer) ===

        # 1. پاک کردن دیتای قبلی این Run (در صورت اجرای مجدد)
        TaskLevelingMetrics.objects.filter(leveling_run=self.leveling_run).delete()
        ResourceUsage.objects.filter(leveling_run=self.leveling_run).delete()

        # 2. ذخیره TaskLevelingMetrics
        metrics_to_create = []
        for tv_id, node in self.global_nodes.items():
            leveled_start = self.leveled_starts.get(tv_id, node.early_start)
            leveled_finish = self.leveled_finishes.get(tv_id, node.early_finish)

            # محاسبه تاخیر (Delay) تحمیل شده توسط لولینگ نسبت به برنامه اصلی (early_start)
            cpm = self._get_cpm_engine_for_node(node)
            cal_engine = cpm._get_engine(node) or cpm._default_cal_engine
            delay_hours = (
                cal_engine.working_hours_between(node.early_start, leveled_start)
                if cal_engine
                else max(0.0, (leveled_start - node.early_start).total_seconds() / 3600)
            )

            metrics_to_create.append(
                TaskLevelingMetrics(
                    leveling_run=self.leveling_run,
                    task_version_id=int(tv_id),
                    original_start=node.early_start,
                    original_finish=node.early_finish,
                    leveled_start=leveled_start,
                    leveled_finish=leveled_finish,
                    leveling_delay_hours=delay_hours,
                    decision_reason=(
                        "Resource capacity delay" if delay_hours > 0 else "No resource delay"
                    ),
                )
            )
        TaskLevelingMetrics.objects.bulk_create(metrics_to_create, batch_size=1000)

        # 3. ذخیره ResourceUsage
        usages_to_create = []
        for (res_id, date), planned_hrs in self.daily_planned_usage.items():
            remaining = self.remaining_capacity[res_id][date]
            usages_to_create.append(
                ResourceUsage(
                    leveling_run=self.leveling_run,
                    resource_id=res_id,
                    usage_date=date,
                    planned_hours=planned_hrs,
                    capacity_hours=planned_hrs + remaining,
                    remaining_capacity=remaining,
                    # مقدار default
                    revision=None
                )
            )
        ResourceUsage.objects.bulk_create(usages_to_create, batch_size=1000)

        self.leveling_run.status = GlobalLevelingRun.STATUS_CALCULATED
        self.leveling_run.last_run_at = datetime.datetime.now(datetime.timezone.utc)
        self.leveling_run.is_committed = False
        self.leveling_run.save(update_fields=[
            "status", "last_run_at", "is_committed", "updated_at"
        ])
        logger.info(f"Resource Leveling Plan {self.leveling_run.id} calculated.")
        return {"status": "Success", "tasks_evaluated": len(self.global_nodes)}


