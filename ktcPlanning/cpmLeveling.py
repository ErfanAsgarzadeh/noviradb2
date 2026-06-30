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
from typing import TYPE_CHECKING, Dict, List, Tuple

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

        # مجموعه‌ای از موتورهای CPM برای هر پروژه (برای دسترسی به گراف وابستگی‌ها و تقویم تسک‌ها)
        # key: revision_id -> CPMEngine
        self.cpm_engines: Dict[int, CPMEngine] = {}

        # تجمیع تمام Nodeها از همه پروژه‌ها در یک دیکشنری مشترک
        # key: task_id (str) -> TaskNode
        self.global_nodes: Dict[str, TaskNode] = {}

        # Engines تقویم برای منابع: resource_id -> CalendarEngine
        self.resource_cal_engines: Dict[int, CalendarEngine] = {}
        self.resources: Dict[int, Resource] = {}

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
        """بارگذاری داده‌های تمام پروژه‌ها، اجرای CPM اولیه و آماده‌سازی منابع."""

        # ۱. پیدا کردن ریویژن‌های فعال (یا آخرین ریویژن تایید نشده) برای پروژه‌های انتخاب شده
        active_revisions = Revision.objects.filter(
            project__in=self.leveling_run.participating_projects.all(),
            approved_at__isnull=True  # یا هر منطقی که برای انتخاب ریویژن جاری دارید
        )

        if not active_revisions.exists():
             raise ValueError("هیچ ریویژن فعالی برای پروژه‌های انتخاب شده یافت نشد.")

        # ۲. اجرای CPM برای هر پروژه به صورت مجزا تا گراف و Floatها محاسبه شوند
        for rev in active_revisions:
            engine = CPMEngine(rev)
            engine.run()  # این متد نودها را می‌سازد و Floatها را حساب می‌کند
            self.cpm_engines[rev.id] = engine
            # ادغام نودهای این پروژه در دیکشنری سراسری
            for tid, node in engine.nodes.items():
                # برای اطمینان از یکتا بودن آیدی در کل پایگاه داده، از tv_id استفاده می‌کنیم
                self.global_nodes[str(node.tv_id)] = node

        # ۳. بارگذاری تمام منابعی که متعلق به این پروژه‌ها هستند
        resources = Resource.objects.filter(
            pool__project__in=self.leveling_run.participating_projects.all(),
            is_active=True
        ).select_related("calendar")

        for res in resources:
            self.resources[res.id] = res
            self.resource_cal_engines[res.id] = CalendarEngine(res.calendar)

        # ۴. بارگذاری تخصیص‌ها (Assignments) برای همه ریویژن‌های فعال
        assignments = Assignment.objects.filter(revision__in=active_revisions)
        for asgn in assignments:
            # اینجا هم کلید را tv_id در نظر می‌گیریم تا با global_nodes مچ شود
            # نکته: در مدل شما Assignment به Task وصل است. باید TaskVersion متناظر را پیدا کنیم.
            # چون global_nodes را بر اساس tv_id ساختیم، یک مپینگ کمکی نیاز داریم

            # مپینگ کمکی: task_id -> tv_id
            tv_mapping = {str(n.task_id): str(n.tv_id) for n in self.global_nodes.values()}

            t_id_str = str(asgn.task_id)
            if t_id_str in tv_mapping:
                tv_id_str = tv_mapping[t_id_str]
                self.task_assignments[tv_id_str].append({
                    "resource_id": asgn.resource_id,
                    "units_percent": float(asgn.units_percent) / 100.0,
                })

    def _get_resource_capacity(self, resource_id: int, date: datetime.date) -> float:
        if date not in self.remaining_capacity[resource_id]:
            res = self.resources[resource_id]
            cal_engine = self.resource_cal_engines[resource_id]
            sched = cal_engine.get_day_schedule(date)
            base_hours = sched.total_hours
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

    def _distribute_task_hours(self, start_dt: datetime.datetime, duration: float, cal_engine: CalendarEngine) -> Dict[datetime.date, float]:
        dist = {}
        current = start_dt
        remaining = duration
        limit = 1000

        while remaining > 0 and limit > 0:
            limit -= 1
            sched = cal_engine.get_day_schedule(current.date())

            if not sched.is_working():
                current = current.replace(hour=0, minute=0) + datetime.timedelta(days=1)
                continue

            available_today = sched.hours_from(current.time())
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
        min_start = cpm.revision.project_start
        candidates = []

        for edge in node.predecessors:
            # در گراف شما from_task_id آیدی Task است. باید تبدیلش کنیم به tv_id
            tv_mapping = {n.task_id: str(n.tv_id) for n in self.global_nodes.values()}
            pred_tv_id = tv_mapping.get(edge.from_task_id)

            if pred_tv_id and pred_tv_id in self.leveled_finishes:
                pred_start = self.leveled_starts[pred_tv_id]
                pred_finish = self.leveled_finishes[pred_tv_id]

                if edge.dep_type == "FS":
                    candidates.append(cpm._add_lag(node, pred_finish, edge.lag_hours))
                elif edge.dep_type == "SS":
                    candidates.append(cpm._add_lag(node, pred_start, edge.lag_hours))
                elif edge.dep_type == "FF":
                    ef_lagged = cpm._add_lag(node, pred_finish, edge.lag_hours)
                    candidates.append(cpm._subtract_hours(node, ef_lagged, node.duration_hours))
                elif edge.dep_type == "SF":
                    es_lagged = cpm._add_lag(node, pred_start, edge.lag_hours)
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
                    available = self._get_resource_capacity(res_id, date)
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
        # اولویت: 1. Total Float کمتر 2. Late Start زودتر
        pq = []
        for tv_id, deg in in_degree.items():
            if deg == 0:
                n = self.global_nodes[tv_id]
                ls_ts = n.late_start.timestamp() if n.late_start else 0
                heapq.heappush(pq, (n.total_float_hours, ls_ts, tv_id))

        # حلقه Leveling
        while pq:
            _, _, tv_id = heapq.heappop(pq)
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
                        ls_ts = succ_node.late_start.timestamp() if succ_node.late_start else 0
                        heapq.heappush(pq, (succ_node.total_float_hours, ls_ts, succ_tv_id))

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
            delay_hours = cpm._working_hours_between(node, node.early_start, leveled_start)

            metrics_to_create.append(
                TaskLevelingMetrics(
                    leveling_run=self.leveling_run,
                    task_version_id=int(tv_id),
                    leveled_start=leveled_start,
                    leveled_finish=leveled_finish,
                    leveling_delay_hours=delay_hours
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
                    remaining_capacity=remaining,
                    # مقدار default
                    revision=None
                )
            )
        ResourceUsage.objects.bulk_create(usages_to_create, batch_size=1000)

        logger.info(f"Global Resource Leveling Run {self.leveling_run.id} completed.")
        return {"status": "Success", "tasks_evaluated": len(self.global_nodes)}