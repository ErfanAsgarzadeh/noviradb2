import pytest
from datetime import time
from decimal import Decimal

from django.utils.dateparse import parse_datetime
from django.urls import reverse
from rest_framework.test import APIClient

from ktcPlanning.models import WorkingInterval
from ktcPlanning.models import TaskActual, VarianceReport
from ktcPlanning.variance_engine import EVMEngine
from tests.factories import make_project, make_revision, make_task


@pytest.mark.django_db
def test_activity_actual_execution_uses_calendar_working_hours():
    project = make_project()
    calendar = project.calendars.get(is_default=True)
    calendar.intervals.all().delete()
    WorkingInterval.objects.bulk_create([
        WorkingInterval(calendar=calendar, weekday=weekday, start_time=time(8, 0), end_time=time(17, 0))
        for weekday in range(7)
    ])
    revision = make_revision(project)
    task, task_version = make_task(project, revision, duration_hours=40)

    client = APIClient()
    client.force_authenticate(user=project.created_by)
    response = client.patch(
        f"/api/planning/activities/{task.id}/?revision_id={revision.id}",
        {
            "actual_start": "2026-07-20T08:00:00",
            "actual_finish": "2026-07-21T10:00:00",
            "progress": 50,
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["actual"]["actualWorkHours"] == pytest.approx(11.0)
    task_version.refresh_from_db()
    assert float(task_version.duration_hours) == 40.0


@pytest.mark.django_db
def test_evm_actual_cost_uses_calendar_hours_from_actual_dates():
    project = make_project()
    calendar = project.calendars.get(is_default=True)
    calendar.intervals.all().delete()
    WorkingInterval.objects.bulk_create([
        WorkingInterval(calendar=calendar, weekday=weekday, start_time=time(8, 0), end_time=time(17, 0))
        for weekday in range(7)
    ])
    revision = make_revision(project, is_baseline=True)
    task, task_version = make_task(project, revision, duration_hours=40)
    task_version.planned_start = parse_datetime("2026-07-20T08:00:00Z")
    task_version.planned_finish = parse_datetime("2026-07-24T17:00:00Z")
    task_version.save()
    TaskActual.objects.create(
        task_version=task_version,
        actual_start=parse_datetime("2026-07-20T08:00:00Z"),
        actual_finish=parse_datetime("2026-07-21T10:00:00Z"),
        progress=50,
        updated_by=project.created_by,
    )

    EVMEngine(project_id=project.id, data_datetime=parse_datetime("2026-07-21T10:00:00Z")).run_task_level_variances()

    report = VarianceReport.objects.get(task=task, revision=revision)
    assert report.actual_cost == Decimal("11.00")
    assert report.earned_value == Decimal("20.00")
