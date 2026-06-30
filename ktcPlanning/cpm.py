from __future__ import annotations

import datetime
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.utils import timezone

from .calendar import CalendarEngine

if TYPE_CHECKING:
    from .models import Revision, TaskVersion, Dependency, TaskActual, WBSNodeVersion

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# Graph Node
# ─────────────────────────────────────────

@dataclass
class TaskNode:
    tv_id: int
    task_id: str
    duration_hours: float
    calendar_id: int | None

    early_start: datetime.datetime | None = None
    early_finish: datetime.datetime | None = None
    late_start: datetime.datetime | None = None
    late_finish: datetime.datetime | None = None

    total_float_hours: float = 0
    free_float_hours: float = 0
    is_critical: bool = False

    successors: list["EdgeInfo"] = field(default_factory=list)
    predecessors: list["EdgeInfo"] = field(default_factory=list)

    actual: "TaskActual | None" = None

    # ─── Freeze state (computed in _classify_freeze) ───
    is_completed: bool = False
    is_in_progress: bool = False
    remaining_duration_hours: float = 0.0


@dataclass
class EdgeInfo:
    from_task_id: str
    to_task_id: str
    dep_type: str  # FS, SS, FF, SF
    lag_hours: float


# ─────────────────────────────────────────
# CPM Engine
# ─────────────────────────────────────────

class CPMEngine:
    """
    موتور CPM با پشتیبانی از:
    - فریز کردن تسک‌هایی که پیشرفت دارند (completed / in-progress)
    - برنامه‌ریزی مجدد (replan) از تاریخ امروز (data_date) تا پایان پروژه
    - اگر شروع پروژه بعد از امروز باشد، از همان تاریخ شروع پروژه محاسبه می‌شود
    - پشتیبانی کامل از ۴ نوع وابستگی: FS, SS, FF, SF با lag
    """

    def __init__(self, revision: "Revision", data_date: datetime.datetime | None = None):
        self.revision = revision

        # data_date = "الان" (نقطه مرجع برنامه‌ریزی مجدد)
        self._data_date: datetime.datetime = data_date or timezone.now()

        self.nodes: dict[str, TaskNode] = {}
        self._cal_engines: dict[int, CalendarEngine] = {}
        self._default_cal_engine: CalendarEngine | None = None

        self._project_start: datetime.datetime | None = None
        self._project_finish: datetime.datetime | None = None

    # ═══════════════════════════════════════
    # Freeze classification
    # ═══════════════════════════════════════

    def _classify_freeze(self, node: TaskNode) -> None:
        """
        وضعیت فریز یک تسک را مشخص می‌کند:

        1. completed (is_completed=True):
           - actual_finish ست شده، یا
           - progress >= 100
           ⇒ ES/EF/LS/LF ثابت و از actual گرفته می‌شود.

        2. in-progress (is_in_progress=True):
           - actual_start ست شده ولی هنوز تموم نشده
           ⇒ ES = actual_start (ثابت)
           ⇒ EF = data_date + remaining_duration  (از الان به بعد ادامه می‌دهد)

        3. not started (هر دو False):
           ⇒ آزاد برای برنامه‌ریزی مجدد
        """
        if not node.actual:
            # هیچ اطلاعات واقعی ندارد — آزاد است
            node.is_completed = False
            node.is_in_progress = False
            node.remaining_duration_hours = node.duration_hours
            return

        progress = float(node.actual.progress or 0)

        # ── تسک تکمیل‌شده ──
        if node.actual.actual_finish is not None or progress >= 100:
            node.is_completed = True
            node.is_in_progress = False
            node.remaining_duration_hours = 0.0
            return

        # ── تسک در حال اجرا ──
        if node.actual.actual_start is not None:
            node.is_completed = False
            node.is_in_progress = True
            # مدت‌زمان باقیمانده بر اساس درصد پیشرفت
            # remaining = duration × (1 - progress/100)
            node.remaining_duration_hours = node.duration_hours * (1.0 - progress / 100.0)
            return

        # ── actual وجود دارد ولی start ست نشده (مثلاً فقط progress = 0) ──
        node.is_completed = False
        node.is_in_progress = False
        node.remaining_duration_hours = node.duration_hours

    def _is_frozen(self, node: TaskNode) -> bool:
        """آیا تسک فریز شده (تکمیل یا در حال اجرا)؟"""
        return node.is_completed or node.is_in_progress

    # ═══════════════════════════════════════
    # Load data
    # ═══════════════════════════════════════

    def _load(self) -> None:
        from .models import TaskVersion, Dependency, TaskActual, Calendar

        versions = TaskVersion.objects.filter(
            revision=self.revision,
            is_deleted=False
        ).values("id", "task_id", "duration_hours", "calendar_id")

        actual_map = {
            a.task_version_id: a
            for a in TaskActual.objects.filter(task_version__revision=self.revision)
        }

        for v in versions:
            tid = str(v["task_id"])
            node = TaskNode(
                tv_id=v["id"],
                task_id=tid,
                duration_hours=float(v["duration_hours"]),
                calendar_id=v["calendar_id"],
                actual=actual_map.get(v["id"]),
            )
            self._classify_freeze(node)
            self.nodes[tid] = node

        # ── Load dependencies ──
        deps = Dependency.objects.filter(revision=self.revision).values(
            "predecessor_id", "successor_id", "dependency_type", "lag_hours"
        )

        for d in deps:
            pred = str(d["predecessor_id"])
            succ = str(d["successor_id"])

            if pred not in self.nodes or succ not in self.nodes:
                continue

            edge = EdgeInfo(
                from_task_id=pred,
                to_task_id=succ,
                dep_type=d["dependency_type"],
                lag_hours=float(d["lag_hours"]),
            )

            self.nodes[pred].successors.append(edge)
            self.nodes[succ].predecessors.append(edge)

        # ── Load calendars ──
        self._load_calendars()

    def _load_calendars(self) -> None:
        """تقویم‌ها را بارگذاری و cache می‌کند."""
        from .models import Calendar

        calendar_ids = set(
            n.calendar_id for n in self.nodes.values() if n.calendar_id is not None
        )

        for cal in Calendar.objects.filter(id__in=calendar_ids):
            self._cal_engines[cal.id] = CalendarEngine(cal)

        # تقویم پیش‌فرض پروژه: اول تقویم الصاق‌شده به پروژه، سپس تقویم default قدیمی
        default_cal = getattr(self.revision.project, 'calendar', None)
        if default_cal is None:
            default_cal = Calendar.objects.filter(
                project=self.revision.project, is_default=True
            ).first()
        if default_cal:
            self._default_cal_engine = CalendarEngine(default_cal)

    # ═══════════════════════════════════════
    # Topological sort (Kahn's algorithm)
    # ═══════════════════════════════════════

    def _topological_sort(self) -> list[str]:
        in_degree = {k: 0 for k in self.nodes}

        for n in self.nodes.values():
            for e in n.successors:
                in_degree[e.to_task_id] += 1

        queue = deque([k for k, v in in_degree.items() if v == 0])
        order = []

        while queue:
            tid = queue.popleft()
            order.append(tid)

            for e in self.nodes[tid].successors:
                in_degree[e.to_task_id] -= 1
                if in_degree[e.to_task_id] == 0:
                    queue.append(e.to_task_id)

        if len(order) != len(self.nodes):
            raise ValueError("Cycle detected in CPM graph")

        return order

    # ═══════════════════════════════════════
    # Calendar helpers
    # ═══════════════════════════════════════

    def _get_engine(self, node: TaskNode) -> CalendarEngine | None:
        return self._cal_engines.get(node.calendar_id, self._default_cal_engine)

    def _add_hours(self, node: TaskNode, start: datetime.datetime, hours: float) -> datetime.datetime:
        if hours <= 0:
            return start
        engine = self._get_engine(node)
        if engine:
            return engine.add_working_hours(start, hours)
        return start + datetime.timedelta(hours=hours)

    def _subtract_hours(self, node: TaskNode, end: datetime.datetime, hours: float) -> datetime.datetime:
        if hours <= 0:
            return end
        engine = self._get_engine(node)
        if engine:
            return engine.subtract_working_hours(end, hours)
        return end - datetime.timedelta(hours=hours)

    # ═══════════════════════════════════════
    # Dependency resolution (all 4 types)
    # ═══════════════════════════════════════

    def _calc_es_from_edge(self, edge: EdgeInfo, anchor: datetime.datetime) -> datetime.datetime:
        """
        با توجه به نوع وابستگی، earliest start ممکن successor را بر اساس
        مقادیر forward pass predecessor محاسبه می‌کند.

        اگر مقدار مورد نیاز predecessor هنوز None باشد، anchor برگردانده می‌شود.

        FS (Finish-Start): successor نمی‌تواند زودتر از EF(pred) + lag شروع شود
        SS (Start-Start):  successor نمی‌تواند زودتر از ES(pred) + lag شروع شود
        FF (Finish-Finish): EF(succ) >= EF(pred) + lag
                           → ES(succ) >= EF(pred) + lag - duration(succ)
        SF (Start-Finish):  EF(succ) >= ES(pred) + lag
                           → ES(succ) >= ES(pred) + lag - duration(succ)
        """
        pred = self.nodes[edge.from_task_id]
        succ = self.nodes[edge.to_task_id]
        lag = edge.lag_hours

        if edge.dep_type == "FS":
            base = pred.early_finish
            if base is None:
                return anchor
            return self._add_hours(succ, base, lag) if lag else base

        elif edge.dep_type == "SS":
            base = pred.early_start
            if base is None:
                return anchor
            return self._add_hours(succ, base, lag) if lag else base

        elif edge.dep_type == "FF":
            base = pred.early_finish
            if base is None:
                return anchor
            constraint_ef = self._add_hours(succ, base, lag) if lag else base
            return self._subtract_hours(succ, constraint_ef, succ.remaining_duration_hours)

        elif edge.dep_type == "SF":
            base = pred.early_start
            if base is None:
                return anchor
            constraint_ef = self._add_hours(succ, base, lag) if lag else base
            return self._subtract_hours(succ, constraint_ef, succ.remaining_duration_hours)

        # fallback
        return pred.early_finish if pred.early_finish is not None else anchor

    def _calc_lf_from_edge(self, edge: EdgeInfo) -> datetime.datetime | None:
        """
        با توجه به نوع وابستگی، latest finish ممکن predecessor را بر اساس
        مقادیر backward pass successor محاسبه می‌کند.

        اگر مقدار مورد نیاز successor هنوز None باشد، None برگردانده می‌شود.

        FS: LF(pred) <= LS(succ) - lag
        SS: LS(pred) <= LS(succ) - lag → LF(pred) <= LS(succ) - lag + duration(pred)
        FF: LF(pred) <= LF(succ) - lag
        SF: LS(pred) <= LF(succ) - lag → LF(pred) <= LF(succ) - lag + duration(pred)
        """
        pred = self.nodes[edge.from_task_id]
        succ = self.nodes[edge.to_task_id]
        lag = edge.lag_hours

        if edge.dep_type == "FS":
            base = succ.late_start
            if base is None:
                return None
            return self._subtract_hours(pred, base, lag) if lag else base

        elif edge.dep_type == "SS":
            base = succ.late_start
            if base is None:
                return None
            constraint_ls = self._subtract_hours(pred, base, lag) if lag else base
            return self._add_hours(pred, constraint_ls, pred.remaining_duration_hours)

        elif edge.dep_type == "FF":
            base = succ.late_finish
            if base is None:
                return None
            return self._subtract_hours(pred, base, lag) if lag else base

        elif edge.dep_type == "SF":
            base = succ.late_finish
            if base is None:
                return None
            constraint_ls = self._subtract_hours(pred, base, lag) if lag else base
            return self._add_hours(pred, constraint_ls, pred.remaining_duration_hours)

        # fallback
        return succ.late_start

    # ═══════════════════════════════════════
    # Forward pass
    # ═══════════════════════════════════════

    def _forward_pass(self, order: list[str]) -> None:
        """
        محاسبه ES/EF:
        - اگر project_start بعد از data_date باشد → anchor = project_start
        - در غیر این صورت → anchor = data_date (الان)
        - تسک‌های completed: ES/EF ثابت (از actual)
        - تسک‌های in-progress: ES = actual_start (ثابت), EF = data_date + remaining_duration
        - تسک‌های آزاد: ES = max(anchor, dependency constraints)
        """
        project_start = self.revision.project_start

        # اگر شروع پروژه بعد از الان است، از همان تاریخ شروع پروژه استفاده کن
        if project_start > self._data_date:
            anchor = project_start
        else:
            anchor = self._data_date

        for tid in order:
            node = self.nodes[tid]

            # ── تسک تکمیل‌شده: ثابت ──
            if node.is_completed:
                node.early_start = node.actual.actual_start or anchor
                node.early_finish = node.actual.actual_finish or node.early_start
                continue

            # ── تسک در حال اجرا: ES ثابت، EF از الان + باقیمانده ──
            if node.is_in_progress:
                node.early_start = node.actual.actual_start

                # EF = max(data_date, dependency constraints) + remaining_duration
                ef_from_now = self._add_hours(node, self._data_date, node.remaining_duration_hours)

                # بررسی وابستگی‌ها (ممکنه predecessor هنوز تموم نشده باشه)
                if node.predecessors:
                    es_from_deps = []
                    for e in node.predecessors:
                        pred = self.nodes[e.from_task_id]
                        if pred.early_finish is None:
                            continue
                        dep_es = self._calc_es_from_edge(e, anchor)
                        es_from_deps.append(dep_es)

                    if es_from_deps:
                        latest_constraint = max(es_from_deps)
                        replan_point = max(latest_constraint, self._data_date)
                        ef_from_constraint = self._add_hours(node, replan_point, node.remaining_duration_hours)
                        ef_from_now = max(ef_from_now, ef_from_constraint)

                node.early_finish = ef_from_now
                continue

            # ── تسک شروع‌نشده: آزاد برای replan ──
            if not node.predecessors:
                es = anchor
            else:
                es_candidates = []
                for e in node.predecessors:
                    dep_es = self._calc_es_from_edge(e, anchor)
                    es_candidates.append(dep_es)

                es = max(es_candidates)
                # ES نمی‌تواند قبل از anchor باشد
                es = max(es, anchor)

            node.early_start = es
            node.early_finish = self._add_hours(node, es, node.remaining_duration_hours)

        # ── محاسبه بازه پروژه ──
        starts = [n.early_start for n in self.nodes.values() if n.early_start is not None]
        finishes = [n.early_finish for n in self.nodes.values() if n.early_finish is not None]

        self._project_start = min(starts) if starts else anchor
        self._project_finish = max(finishes) if finishes else anchor

    # ═══════════════════════════════════════
    # Backward pass
    # ═══════════════════════════════════════

    def _backward_pass(self, order: list[str]) -> None:
        """
        محاسبه LS/LF:
        - تسک‌های completed: LS/LF ثابت
        - تسک‌های in-progress: LS = actual_start (ثابت), LF محاسبه می‌شود
        - تسک‌های آزاد: LF = min(successor constraints)
        """
        for tid in reversed(order):
            node = self.nodes[tid]

            # ── تسک تکمیل‌شده: ثابت ──
            if node.is_completed:
                node.late_start = node.early_start
                node.late_finish = node.early_finish
                continue

            # ── تسک در حال اجرا ──
            if node.is_in_progress:
                node.late_start = node.early_start  # actual_start

                if not node.successors:
                    node.late_finish = self._project_finish
                else:
                    lf_candidates = []
                    for e in node.successors:
                        lf = self._calc_lf_from_edge(e)
                        if lf is not None:
                            lf_candidates.append(lf)
                    node.late_finish = min(lf_candidates) if lf_candidates else self._project_finish

                continue

            # ── تسک شروع‌نشده ──
            if not node.successors:
                lf = self._project_finish
            else:
                lf_candidates = []
                for e in node.successors:
                    lf_candidate = self._calc_lf_from_edge(e)
                    if lf_candidate is not None:
                        lf_candidates.append(lf_candidate)
                lf = min(lf_candidates) if lf_candidates else self._project_finish

            node.late_finish = lf
            node.late_start = self._subtract_hours(node, lf, node.remaining_duration_hours)

    # ═══════════════════════════════════════
    # Float calculation
    # ═══════════════════════════════════════

    def _compute_floats(self) -> None:
        for node in self.nodes.values():
            # Total Float = LS - ES (یا LF - EF)
            if node.late_start is not None and node.early_start is not None:
                node.total_float_hours = (
                    (node.late_start - node.early_start).total_seconds() / 3600
                )
            else:
                node.total_float_hours = 0

            # تسک‌های completed همیشه float = 0 دارند
            if node.is_completed:
                node.total_float_hours = 0
                node.free_float_hours = 0
                node.is_critical = True  # تسک‌های انجام‌شده روی مسیر واقعی هستند
                continue

            node.is_critical = node.total_float_hours <= 0

            # Free Float = min(ES(successor)) - EF(this)
            if node.successors:
                ff_candidates = []
                for e in node.successors:
                    succ = self.nodes[e.to_task_id]
                    if node.early_finish is not None and succ.early_start is not None:
                        ff = (succ.early_start - node.early_finish).total_seconds() / 3600
                        ff_candidates.append(ff)
                node.free_float_hours = min(ff_candidates) if ff_candidates else node.total_float_hours
            else:
                node.free_float_hours = node.total_float_hours

    # ═══════════════════════════════════════
    # Save results to DB
    # ═══════════════════════════════════════

    def _save_results(self) -> None:
        """ذخیره نتایج CPM در TaskVersion و TaskScheduleMetrics."""
        from .models import TaskVersion, TaskScheduleMetrics

        metrics_to_create = []
        metrics_to_update = []
        tv_updates = []

        existing_metrics = {
            m.task_version_id: m
            for m in TaskScheduleMetrics.objects.filter(
                task_version__revision=self.revision
            )
        }

        for node in self.nodes.values():
            if node.early_start is None or node.early_finish is None:
                continue

            # آپدیت planned_start/finish در TaskVersion
            tv_updates.append((node.tv_id, node.early_start, node.early_finish))

            # ذخیره metrics
            ls = node.late_start or node.early_start
            lf = node.late_finish or node.early_finish

            if node.tv_id in existing_metrics:
                m = existing_metrics[node.tv_id]
                m.early_start = node.early_start
                m.early_finish = node.early_finish
                m.late_start = ls
                m.late_finish = lf
                m.total_float_hours = int(node.total_float_hours)
                m.free_float_hours = int(node.free_float_hours)
                m.is_critical = node.is_critical
                metrics_to_update.append(m)
            else:
                metrics_to_create.append(TaskScheduleMetrics(
                    task_version_id=node.tv_id,
                    early_start=node.early_start,
                    early_finish=node.early_finish,
                    late_start=ls,
                    late_finish=lf,
                    total_float_hours=int(node.total_float_hours),
                    free_float_hours=int(node.free_float_hours),
                    is_critical=node.is_critical,
                ))

        # Bulk update TaskVersions
        if tv_updates:
            tv_objs = TaskVersion.objects.filter(
                id__in=[tv_id for tv_id, _, _ in tv_updates]
            )
            tv_map = {tv.id: tv for tv in tv_objs}
            for tv_id, es, ef in tv_updates:
                tv = tv_map.get(tv_id)
                if tv:
                    tv.planned_start = es
                    tv.planned_finish = ef
            TaskVersion.objects.bulk_update(
                [tv_map[tv_id] for tv_id, _, _ in tv_updates if tv_id in tv_map],
                ["planned_start", "planned_finish"]
            )

        # Bulk create/update metrics
        if metrics_to_create:
            TaskScheduleMetrics.objects.bulk_create(metrics_to_create)
        if metrics_to_update:
            TaskScheduleMetrics.objects.bulk_update(
                metrics_to_update,
                ["early_start", "early_finish", "late_start", "late_finish",
                 "total_float_hours", "free_float_hours", "is_critical"]
            )

    # ═══════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════

    def run(self) -> dict:
        """
        اجرای CPM:
        1. بارگذاری داده‌ها
        2. طبقه‌بندی فریز
        3. مرتب‌سازی توپولوژیک
        4. Forward pass (با در نظر گرفتن data_date)
        5. Backward pass
        6. محاسبه Float
        7. ذخیره نتایج در دیتابیس
        """
        self._load()

        if not self.nodes:
            return {"total_tasks": 0}

        order = self._topological_sort()

        self._forward_pass(order)
        self._backward_pass(order)
        self._compute_floats()
        self._save_results()

        frozen_count = sum(1 for n in self.nodes.values() if self._is_frozen(n))
        completed_count = sum(1 for n in self.nodes.values() if n.is_completed)
        in_progress_count = sum(1 for n in self.nodes.values() if n.is_in_progress)

        return {
            "total_tasks": len(self.nodes),
            "critical_tasks": sum(n.is_critical for n in self.nodes.values()),
            "frozen_tasks": frozen_count,
            "completed_tasks": completed_count,
            "in_progress_tasks": in_progress_count,
            "replanned_tasks": len(self.nodes) - frozen_count,
            "data_date": self._data_date,
            "project_start": self._project_start,
            "project_finish": self._project_finish,
        }

    @classmethod
    def replan_from_now(
        cls,
        revision: "Revision",
        data_date: datetime.datetime | None = None,
    ) -> "CPMEngine":
        """
        Convenience method برای اجرای replan:
        - تسک‌هایی که progress دارند فریز می‌شوند
        - بقیه تسک‌ها از data_date (یا الان) به بعد برنامه‌ریزی مجدد می‌شوند
        - اگر project_start بعد از data_date باشد، از project_start استفاده می‌شود

        Usage:
            engine = CPMEngine.replan_from_now(revision)
            # or with explicit data date:
            engine = CPMEngine.replan_from_now(revision, data_date=some_datetime)
        """
        engine = cls(revision=revision, data_date=data_date)
        engine.run()
        return engine
