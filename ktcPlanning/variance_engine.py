import logging
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from django.db import transaction

from ktcPlanning.models import (
    Project, Revision, TaskVersion, TaskReportLog, VarianceReport, Calendar
)
# فرض می‌کنیم CalendarEngine در مسیر زیر قرار دارد
from ktcPlanning.calendar import CalendarEngine

logger = logging.getLogger(__name__)


class EVMEngine:
    def __init__(self, project_id, data_datetime=None):
        self.project_id = project_id
        # برای محاسبات ساعتی، حتماً به datetime نیاز داریم نه فقط date
        self.data_datetime = data_datetime or timezone.now()

        self.baseline_rev = Revision.objects.filter(
            project_id=self.project_id,
            is_baseline=True,
            is_deleted=False
        ).order_by('-created_at').first()

        self.current_rev = Revision.objects.filter(
            project_id=self.project_id,
            is_deleted=False
        ).latest('created_at')

        # کش کردن موتورهای تقویم برای پرفورمنس بالا
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

    @transaction.atomic
    def run_task_level_variances(self):
        """اجرای محاسبات EVM برای تک‌تک تسک‌های پروژه (نسخه بهینه‌شده)"""

        if not self.baseline_rev:
            logger.error("No Baseline found for project. EVM requires a baseline.")
            return

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

        # حل مشکل N+1 Query برای محاسبه AC
        # استخراج مجموع ساعات کاری تایید شده برای تمام تسک‌های این پروژه در یک کوئری
        active_task_ids = [tv.task_id for tv in active_tvs]
        ac_aggregates = TaskReportLog.objects.filter(
            task_id__in=active_task_ids,
            is_approved=True
        ).values('task_id').annotate(total_ac=Sum('time_spent_hours'))

        # تبدیل به دیکشنری برای دسترسی سریع: {task_id: total_ac}
        ac_dict = {item['task_id']: (item['total_ac'] or Decimal('0.00')) for item in ac_aggregates}

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

            # --- 2. Planned Value (PV) & Budget At Completion (BAC) ---
            baseline_tv = baseline_tvs.get(task.id)
            if not baseline_tv:
                continue  # تسک جدید است و در بیس‌لاین نیست

            pv, bac = self._calculate_task_pv(baseline_tv)

            # --- 3. Earned Value (EV) ---
            actual_progress = Decimal('0.00')
            if hasattr(current_tv, 'actual') and current_tv.actual:
                actual_progress = Decimal(current_tv.actual.progress) / Decimal('100.0')

            ev = bac * actual_progress

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

        logger.info(f"EVM Engine: Calculated variance for {len(snapshots_to_create) + len(snapshots_to_update)} tasks.")