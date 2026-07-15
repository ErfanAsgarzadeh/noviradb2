import logging
from decimal import Decimal
from django.db.models import Q, Sum
from django.utils import timezone
from django.db import transaction

from ktcPlanning.models import (
    Project, Revision, TaskVersion, VarianceReport, Calendar, Assignment, TaskReportLog
)
# فرض می‌کنیم CalendarEngine در مسیر زیر قرار دارد
from ktcPlanning.calendar import CalendarEngine
from ktcPlanning.revision_policy import (
    ROLE_BASELINE, ROLE_EXECUTION, get_official_revision,
)

logger = logging.getLogger(__name__)


class EVMEngine:
    def __init__(self, project_id, data_datetime=None):
        self.project_id = project_id
        self.project = Project.objects.select_related(
            'active_baseline_revision', 'current_execution_revision'
        ).get(pk=project_id, is_deleted=False)
        self.data_datetime = data_datetime or self.project.current_data_date or timezone.now()
        self.baseline_rev = get_official_revision(
            self.project, ROLE_BASELINE, required=False
        )
        self.current_rev = get_official_revision(
            self.project, ROLE_EXECUTION, required=True
        )
        self._cal_engines = {}
        self._default_cal_engine = None
        self._load_calendars()
    def _load_calendars(self):
        """لود کردن تقویم پیش‌فرض و تمامی تقویم‌های اختصاصی تسک‌ها در حافظه"""
        default_cal = Calendar.objects.filter(
            project_id=self.project_id,
            is_default=True
        ).prefetch_related("intervals", "exceptions").first()

        if default_cal:
            self._default_cal_engine = CalendarEngine(default_cal)
            self._cal_engines[default_cal.id] = self._default_cal_engine

        # لود کردن بقیه تقویم‌های مربوط به این پروژه
        other_cals = Calendar.objects.filter(
            project_id=self.project_id,
            is_default=False
        ).prefetch_related("intervals", "exceptions")

        for cal in other_cals:
            self._cal_engines[cal.id] = CalendarEngine(cal)

    def _get_engine(self, calendar_id):
        """دریافت تقویم اختصاصی تسک یا تقویم پیش‌فرض پروژه"""
        if calendar_id and calendar_id in self._cal_engines:
            return self._cal_engines[calendar_id]
        return self._default_cal_engine

    def _calculate_task_pv(self, baseline_tv):
        """
        محاسبه Planned Value بر اساس ساعات کاری دقیق در تقویم.
        """
        bac = Decimal(baseline_tv.duration_hours)

        if not baseline_tv.planned_start or not baseline_tv.planned_finish:
            return Decimal('0.00'), bac

        # بررسی مایل‌استون‌ها (تسک‌های بدون زمان)
        if bac == Decimal('0.00'):
            if self.data_datetime >= baseline_tv.planned_start:
                return Decimal('0.00'), bac  # برای مایل‌استون ارزش پولی/زمانی صفر است
            return Decimal('0.00'), bac

        if self.data_datetime >= baseline_tv.planned_finish:
            return bac, bac
        elif self.data_datetime <= baseline_tv.planned_start:
            return Decimal('0.00'), bac
        # استخراج ساعات کاری خالص از تقویم اختصاصی همین تسک
        cal_engine = self._get_engine(baseline_tv.calendar_id)

        if cal_engine:
            # محاسبه ساعت کاری بین شروع برنامه‌ریزی شده و لحظه الان
            passed_hours = cal_engine.working_hours_between(
                baseline_tv.planned_start,
                self.data_datetime
            )
        else:
            # فال‌بک سیستم در صورت نبود هیچ تقویمی (اختلاف زمانی خام)
            passed_hours = (self.data_datetime - baseline_tv.planned_start).total_seconds() / 3600.0

        # تبدیل به دسیمال و اطمینان از اینکه PV از BAC تجاوز نکند
        pv = Decimal(passed_hours)
        if pv > bac:
            pv = bac

        return round(pv, 2), bac

    def _approved_report_filter(self, queryset):
        """Only reports accepted into project history should affect EVM."""
        return queryset.filter(
            Q(approval_status__in=['reviewer_approved', 'final_approved'])
            | Q(is_approved=True)
        )

    def _progress_as_of(self, active_task_ids):
        """
        Latest approved progress for every task up to the current data date.

        This prevents historical EVM snapshots from using today's TaskActual
        progress for older report dates.
        """
        latest_progress = {}
        reports = self._approved_report_filter(
            TaskReportLog.objects.filter(
                task_id__in=active_task_ids,
                timestamp__lte=self.data_datetime,
            )
        ).order_by('task_id', '-timestamp')

        for report in reports:
            if report.task_id not in latest_progress:
                latest_progress[report.task_id] = (
                    Decimal(report.progress_percent or 0) / Decimal('100.0')
                )
        return latest_progress

    def _actual_hours_from_dates(self, task_version):
        """Fallback AC(H): derive consumed hours from actual dates and task calendar."""
        actual = getattr(task_version, 'actual', None)
        if not actual or not actual.actual_start:
            return Decimal('0.00')
        if actual.actual_start > self.data_datetime:
            return Decimal('0.00')

        actual_end = actual.actual_finish if actual.actual_finish and actual.actual_finish <= self.data_datetime else self.data_datetime
        if actual_end <= actual.actual_start:
            return Decimal('0.00')

        cal_engine = self._get_engine(task_version.calendar_id)
        if cal_engine:
            hours = cal_engine.working_hours_between(actual.actual_start, actual_end)
        else:
            hours = (actual_end - actual.actual_start).total_seconds() / 3600.0
        return Decimal(str(hours))
    def collect_evm_data_dates(self):
        """
        Build the daily timeline that should be recalculated for EVM charts:
        approved report dates, actual dates, selected data date, and today.
        """
        dates = {self.data_datetime}
        task_ids = list(TaskVersion.objects.filter(
            revision=self.current_rev,
            is_deleted=False
        ).values_list('task_id', flat=True))

        def add_history_date(value):
            if not value:
                return
            if timezone.is_naive(value):
                value = timezone.make_aware(value, timezone.get_current_timezone())
            if value <= self.data_datetime:
                dates.add(value)

        report_dates = self._approved_report_filter(
            TaskReportLog.objects.filter(task_id__in=task_ids)
        ).values_list('timestamp', flat=True)
        for value in report_dates:
            add_history_date(value)

        actual_dates = TaskVersion.objects.filter(
            revision=self.current_rev,
            is_deleted=False,
            actual__isnull=False,
        ).values_list(
            'actual__actual_start',
            'actual__actual_finish',
        )
        for actual_start, actual_finish in actual_dates:
            add_history_date(actual_start)
            add_history_date(actual_finish)

        aware_dates = []
        for value in dates:
            if timezone.is_naive(value):
                value = timezone.make_aware(value, timezone.get_current_timezone())
            aware_dates.append(value)

        normalized = []
        seen_days = set()
        for value in sorted(aware_dates):
            day = value.date()
            if day in seen_days:
                continue
            seen_days.add(day)
            normalized.append(value)
        return normalized

    def run_historical_task_level_variances(self):
        """
        Recalculate EVM snapshots for all known actual/report dates and today.
        """
        original_data_datetime = self.data_datetime
        data_dates = self.collect_evm_data_dates()
        target_report_dates = [value.date() for value in data_dates]
        VarianceReport.objects.filter(
            task__project_id=self.project_id,
            revision=self.current_rev,
        ).exclude(report_date__in=target_report_dates).delete()

        total_snapshots = 0
        try:
            for data_datetime in data_dates:
                self.data_datetime = data_datetime
                total_snapshots += self.run_task_level_variances() or 0
        finally:
            self.data_datetime = original_data_datetime

        return {
            'dates': [value.date().isoformat() for value in data_dates],
            'snapshots': total_snapshots,
        }
    @transaction.atomic
    def run_task_level_variances(self):
        """اجرای محاسبات EVM برای تک‌تک تسک‌های پروژه (نسخه بهینه‌شده)"""

        if not self.baseline_rev:
            logger.error("No Baseline found for project. EVM requires a baseline.")
            return 0
        # استخراج تسک‌های فعال در نسخه جاری
        active_tvs = TaskVersion.objects.filter(
            revision=self.current_rev,
            is_deleted=False
        ).select_related('task', 'actual')

        # استخراج تسک‌های بیس‌لاین به صورت دیکشنری (O(1) lookup)
        baseline_tvs = {
            tv.task_id: tv for tv in TaskVersion.objects.filter(
                revision=self.baseline_rev,
                is_deleted=False
            )
        }

        # AC(H) is actual consumed hours up to the engine data date.
        # Prefer approved task report logs because they represent accepted actual work.
        # Fall back to assignment.actual_hours when no report-log hours exist yet.
        active_task_ids = [tv.task_id for tv in active_tvs]
        progress_dict = self._progress_as_of(active_task_ids)
        approved_logs = self._approved_report_filter(
            TaskReportLog.objects.filter(
                task_id__in=active_task_ids,
                timestamp__lte=self.data_datetime,
            )
        ).values('task_id').annotate(total_ac=Sum('time_spent_hours'))
        ac_dict = {item['task_id']: (item['total_ac'] or Decimal('0.00')) for item in approved_logs}

        assignment_hours = Assignment.objects.filter(
            revision=self.current_rev,
            task_id__in=active_task_ids,
        ).values('task_id').annotate(total_ac=Sum('actual_hours'))
        for item in assignment_hours:
            task_id = item['task_id']
            if ac_dict.get(task_id, Decimal('0.00')) == Decimal('0.00'):
                ac_dict[task_id] = item['total_ac'] or Decimal('0.00')
        # واکشی اسنپ‌شات‌های موجود در این تاریخ برای آپدیت یا ایجاد (Upsert)
        existing_snapshots = {
            snap.task_id: snap for snap in VarianceReport.objects.filter(
                task__project_id=self.project_id,  # آپدیت شده بر اساس معماری جدید
                report_date=self.data_datetime.date(),
                revision=self.current_rev
            )
        }

        snapshots_to_create = []
        snapshots_to_update = []

        for current_tv in active_tvs:
            task = current_tv.task

            # --- 1. Actual Cost (AC) (بدون کوئری اضافه) ---
            ac = ac_dict.get(task.id, Decimal('0.00'))
            if ac == Decimal('0.00'):
                ac = self._actual_hours_from_dates(current_tv)
            # --- 2. Planned Value (PV) & Budget At Completion (BAC) ---
            baseline_tv = baseline_tvs.get(task.id)
            if not baseline_tv:
                continue  # تسک جدید است و در بیس‌لاین نیست

            pv, bac = self._calculate_task_pv(baseline_tv)

            # --- 3. Earned Value (EV) ---
            actual_progress = progress_dict.get(task.id, Decimal('0.00'))
            if actual_progress == Decimal('0.00') and hasattr(current_tv, 'actual') and current_tv.actual:
                actual_finish = current_tv.actual.actual_finish
                if actual_finish and actual_finish <= self.data_datetime:
                    actual_progress = Decimal(current_tv.actual.progress or 0) / Decimal('100.0')

            ev = bac * min(actual_progress, Decimal('1.00'))

            # --- 4. Performance Indices (SPI & CPI) ---
            spi = ev / pv if pv > Decimal('0.00') else Decimal('1.00')
            cpi = ev / ac if ac > Decimal('0.00') else Decimal('1.00')

            # --- 5. Variances (SV & CV) ---
            sv = ev - pv
            cv = ev - ac

            # --- 6. Forecasting (EAC, ETC, VAC) ---
            eac = bac / cpi if cpi > Decimal('0.00') else bac + ac
            etc = eac - ac
            vac = bac - eac

            # --- 7. Action Required Logic ---
            action_required = bool(spi < Decimal('0.85') or cpi < Decimal('0.85') or cv < 0 or sv < 0)

            # --- 8. Prepare Database Object ---
            snapshot_data = {
                'budget_at_completion': round(bac, 2),
                'planned_value': round(pv, 2),
                'earned_value': round(ev, 2),
                'actual_cost': round(ac, 2),
                'spi': round(spi, 2),
                'cpi': round(cpi, 2),
                'schedule_variance': round(sv, 2),
                'cost_variance': round(cv, 2),
                'estimate_at_completion': round(eac, 2),
                'estimate_to_complete': round(etc, 2),
                'variance_at_completion': round(vac, 2),
                'action_required': action_required
            }

            if task.id in existing_snapshots:
                snap = existing_snapshots[task.id]
                for key, value in snapshot_data.items():
                    setattr(snap, key, value)
                snapshots_to_update.append(snap)
            else:
                snapshots_to_create.append(
                    VarianceReport(
                        task=task,
                        revision=self.current_rev,
                        report_date=self.data_datetime.date(),
                        **snapshot_data
                    )
                )

        # اجرای عملیات روی دیتابیس
        if snapshots_to_create:
            VarianceReport.objects.bulk_create(snapshots_to_create)
        if snapshots_to_update:
            VarianceReport.objects.bulk_update(snapshots_to_update, [
                'budget_at_completion', 'planned_value', 'earned_value',
                'actual_cost', 'spi', 'cpi', 'schedule_variance', 'cost_variance',
                'estimate_at_completion', 'estimate_to_complete', 'variance_at_completion',
                'action_required'
            ])

        calculated_count = len(snapshots_to_create) + len(snapshots_to_update)
        logger.info(f"EVM Engine: Calculated variance for {calculated_count} tasks.")
        return calculated_count
