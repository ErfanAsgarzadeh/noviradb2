from datetime import datetime, time
from decimal import Decimal
import re

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from .models import Project, Task, VarianceReport
from .variance_engine import EVMEngine


DECIMAL_FIELDS = [
    "budget_at_completion",
    "planned_value",
    "earned_value",
    "actual_cost",
    "spi",
    "cpi",
    "schedule_variance",
    "cost_variance",
    "estimate_at_completion",
    "estimate_to_complete",
    "variance_at_completion",
]
UPDATE_FIELDS = [*DECIMAL_FIELDS, "action_required"]


def _status_datetime(status_date):
    parsed = parse_date(str(status_date or ""))
    if parsed is None:
        raise ValidationError({"status_date": "Invalid status_date. Use YYYY-MM-DD."})
    return timezone.make_aware(datetime.combine(parsed, time.max), timezone.get_current_timezone())


def _decimal_or_default(value, default=Decimal("0.00")):
    if value in (None, ""):
        return default
    return Decimal(str(value))


def _ratio_or_default(value):
    if value in (None, ""):
        return Decimal("1.00")
    return Decimal(str(value))


def _changed(snapshot, data):
    for field, value in data.items():
        if getattr(snapshot, field) != value:
            return True
    return False


def generate_cost_variance_reports(*, project, status_date, currency, task_ids=None, actor=None):
    if not isinstance(project, Project):
        project = Project.objects.get(pk=project)
    currency = str(currency or "").upper().strip()
    if not currency:
        raise ValidationError({"currency": "Currency is required."})
    if not re.fullmatch(r"[A-Z0-9]{3,8}", currency):
        raise ValidationError({"currency": "Invalid currency code."})
    data_datetime = _status_datetime(status_date)
    report_date = data_datetime.date()

    task_id_values = None
    if task_ids:
        task_id_values = [str(item) for item in task_ids]
        mismatches = Task.objects.filter(id__in=task_id_values).exclude(project=project).values_list("id", flat=True)
        if mismatches:
            raise ValidationError({"task_ids": "All task_ids must belong to the selected project."})

    engine = EVMEngine(project.id, data_datetime=data_datetime)
    rows = engine.run_cost_task_variances(currency=currency, task_ids=task_id_values)
    existing = {
        item.task_id: item
        for item in VarianceReport.objects.filter(
            task__project=project,
            revision=engine.current_rev,
            report_date=report_date,
            dimension=VarianceReport.DIMENSION_COST,
            currency=currency,
        )
    }

    created = 0
    updated = 0
    skipped = 0
    failed = 0
    errors = []
    to_create = []
    to_update = []

    for row in rows:
        task_id = row["task"]
        has_budget = bool(row.get("has_approved_budget"))
        has_actual = bool(row.get("has_actual_cost"))
        if not has_budget and not has_actual:
            skipped += 1
            continue
        try:
            data = {
                "budget_at_completion": _decimal_or_default(row.get("budget_at_completion")),
                "planned_value": _decimal_or_default(row.get("planned_value")),
                "earned_value": _decimal_or_default(row.get("earned_value")),
                "actual_cost": _decimal_or_default(row.get("actual_cost")),
                "spi": _ratio_or_default(row.get("spi")),
                "cpi": _ratio_or_default(row.get("cpi")),
                "schedule_variance": _decimal_or_default(row.get("schedule_variance")),
                "cost_variance": _decimal_or_default(row.get("cost_variance")),
                "estimate_at_completion": _decimal_or_default(row.get("estimate_at_completion")),
                "estimate_to_complete": _decimal_or_default(row.get("estimate_to_complete")),
                "variance_at_completion": _decimal_or_default(row.get("variance_at_completion")),
                "action_required": bool(row.get("action_required")),
            }
            snapshot = existing.get(task_id)
            if snapshot:
                if _changed(snapshot, data):
                    for field, value in data.items():
                        setattr(snapshot, field, value)
                    to_update.append(snapshot)
                    updated += 1
                else:
                    skipped += 1
            else:
                to_create.append(
                    VarianceReport(
                        task_id=task_id,
                        revision=engine.current_rev,
                        report_date=report_date,
                        dimension=VarianceReport.DIMENSION_COST,
                        currency=currency,
                        **data,
                    )
                )
                created += 1
        except Exception as exc:
            failed += 1
            errors.append({"task_id": str(task_id), "error": str(exc)})

    with transaction.atomic():
        if to_create:
            VarianceReport.objects.bulk_create(to_create)
        if to_update:
            VarianceReport.objects.bulk_update(to_update, UPDATE_FIELDS)

    return {
        "project_id": project.id,
        "status_date": report_date.isoformat(),
        "currency": currency,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }
