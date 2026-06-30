"""
test_evm_engine.py  —  تست موتور EVM (Earned Value Management)
پوشش:
  - محاسبه PV (Planned Value)
  - محاسبه EV (Earned Value) از TaskActual
  - SPI و CPI
  - variance/calculate endpoint
"""
import pytest
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from datetime import timedelta
from decimal import Decimal

from ktcPlanning.variance_engine import EVMEngine
from ktcPlanning.models import (
    VarianceReport, TaskActual, TaskVersion, Revision,
)
from .factories import (
    make_company_admin, make_project, make_revision,
    make_task, make_report,
)


def api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def set_task_actual(task_version, progress, actual_start=None):
    from ktcPlanning.models import TaskActual
    ta, _ = TaskActual.objects.get_or_create(
        task_version=task_version,
        defaults={"updated_by": task_version.task.created_by}
    )
    ta.progress = progress
    ta.actual_start = actual_start or timezone.now() - timedelta(hours=4)
    ta.updated_by = task_version.task.created_by
    ta.save()
    return ta


# ══════════════════════════════════════════════════════════
#  PV محاسبات
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestPlannedValue:

    def test_pv_is_full_bac_when_past_finish(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, is_baseline=True)
        task, tv = make_task(project, revision, duration_hours=8)

        # planned_finish را در گذشته قرار می‌دهیم
        tv.planned_start = timezone.now() - timedelta(hours=10)
        tv.planned_finish = timezone.now() - timedelta(hours=2)
        tv.save()

        engine = EVMEngine(project_id=project.id, data_datetime=timezone.now())
        pv, bac = engine._calculate_task_pv(tv)
        assert pv == bac

    def test_pv_is_zero_before_start(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, is_baseline=True)
        task, tv = make_task(project, revision, duration_hours=8)

        tv.planned_start = timezone.now() + timedelta(hours=10)
        tv.planned_finish = timezone.now() + timedelta(hours=18)
        tv.save()

        # data_datetime قبل از شروع تسک
        engine = EVMEngine(
            project_id=project.id,
            data_datetime=timezone.now()
        )
        pv, bac = engine._calculate_task_pv(tv)
        assert pv == Decimal("0.00")

    def test_pv_partial_when_in_progress(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, is_baseline=True)
        task, tv = make_task(project, revision, duration_hours=8)

        now = timezone.now()
        tv.planned_start = now - timedelta(hours=4)
        tv.planned_finish = now + timedelta(hours=4)
        tv.save()

        engine = EVMEngine(project_id=project.id, data_datetime=now)
        pv, bac = engine._calculate_task_pv(tv)
        # باید بین 0 و BAC باشد
        assert Decimal("0") < pv < bac


# ══════════════════════════════════════════════════════════
#  SPI و CPI محاسبه
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestEVMCalculation:

    def setup_method(self):
        self.admin = make_company_admin()
        self.project = make_project(creator=self.admin)
        self.revision = make_revision(self.project, creator=self.admin, is_baseline=True)
        self.task, self.tv = make_task(
            self.project, self.revision, duration_hours=8
        )
        # تسک در گذشته برنامه‌ریزی شده
        now = timezone.now()
        self.tv.planned_start = now - timedelta(hours=10)
        self.tv.planned_finish = now - timedelta(hours=2)
        self.tv.save()

    def test_spi_equals_one_when_on_schedule(self):
        """EV == PV → SPI = 1"""
        set_task_actual(self.tv, progress=100)

        engine = EVMEngine(project_id=self.project.id)
        engine.run_task_level_variances()

        report = VarianceReport.objects.filter(
            task=self.task, revision=self.revision
        ).first()
        assert report is not None
        assert float(report.spi) == pytest.approx(1.0, abs=0.05)

    def test_spi_less_than_one_when_behind_schedule(self):
        """EV < PV → SPI < 1 → تأخیر"""
        set_task_actual(self.tv, progress=50)

        engine = EVMEngine(project_id=self.project.id)
        engine.run_task_level_variances()

        report = VarianceReport.objects.filter(
            task=self.task, revision=self.revision
        ).first()
        assert report is not None
        assert float(report.spi) < 1.0

    def test_variance_report_stored_in_db(self):
        set_task_actual(self.tv, progress=80)

        engine = EVMEngine(project_id=self.project.id)
        engine.run_task_level_variances()

        assert VarianceReport.objects.filter(
            task=self.task, revision=self.revision
        ).exists()

    def test_bac_matches_task_duration_hours(self):
        set_task_actual(self.tv, progress=100)

        engine = EVMEngine(project_id=self.project.id)
        engine.run_task_level_variances()

        report = VarianceReport.objects.get(
            task=self.task, revision=self.revision
        )
        assert float(report.budget_at_completion) == pytest.approx(
            float(self.tv.duration_hours), abs=0.01
        )


# ══════════════════════════════════════════════════════════
#  calculate endpoint
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestVarianceCalculateEndpoint:

    def test_calculate_returns_success(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, is_baseline=True)
        task, tv = make_task(project, revision, duration_hours=8)
        now = timezone.now()
        tv.planned_start = now - timedelta(hours=10)
        tv.planned_finish = now - timedelta(hours=2)
        tv.save()
        set_task_actual(tv, progress=80)

        resp = api(admin).post(
            reverse("variance-report-calculate"),
            {"project_id": str(project.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert "محاسبات" in resp.data.get("status", "")

    def test_calculate_without_project_id_returns_400(self):
        admin = make_company_admin()
        resp = api(admin).post(
            reverse("variance-report-calculate"),
            {},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_calculate_requires_authentication(self):
        admin = make_company_admin()
        project = make_project(creator=admin)

        resp = APIClient().post(
            reverse("variance-report-calculate"),
            {"project_id": str(project.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
