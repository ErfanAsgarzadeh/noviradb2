"""
test_cpm_engine.py  —  تست موتور CPM (Critical Path Method)
پوشش:
  - محاسبه ES/EF/LS/LF برای زنجیره ساده
  - تشخیص مسیر بحرانی
  - شناسایی حلقه (cycle detection)
  - run-cpm endpoint
"""
import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from datetime import timedelta

from ktcPlanning.cpm import CPMEngine
from ktcPlanning.models import Dependency, TaskScheduleMetrics
from .factories import (
    make_company_admin, make_project, make_revision,
    make_task, make_wbs_node,
)


def api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def make_dependency(revision, predecessor, successor, dep_type="FS", lag=0):
    return Dependency.objects.create(
        revision=revision,
        predecessor=predecessor,
        successor=successor,
        dependency_type=dep_type,
        lag_hours=lag,
    )


# ══════════════════════════════════════════════════════════
#  موتور CPM مستقیم
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestCPMEngineUnit:

    def setup_method(self):
        self.admin = make_company_admin()
        self.project = make_project(creator=self.admin)
        self.revision = make_revision(self.project)

    def test_single_task_has_zero_float(self):
        """یک تسک تنها → total float = 0"""
        task, tv = make_task(self.project, self.revision, duration_hours=8)
        engine = CPMEngine(self.revision)
        engine.run()

        metrics = TaskScheduleMetrics.objects.get(task_version=tv)
        assert metrics.total_float_hours == 0
        assert metrics.is_critical is True

    def test_sequential_chain_first_task_is_critical(self):
        """A → B → C: همه روی مسیر بحرانی"""
        task_a, tv_a = make_task(self.project, self.revision, "تسک A", duration_hours=8)
        task_b, tv_b = make_task(self.project, self.revision, "تسک B", duration_hours=4)
        task_c, tv_c = make_task(self.project, self.revision, "تسک C", duration_hours=6)

        make_dependency(self.revision, task_a, task_b)
        make_dependency(self.revision, task_b, task_c)

        engine = CPMEngine(self.revision)
        engine.run()

        for tv in [tv_a, tv_b, tv_c]:
            m = TaskScheduleMetrics.objects.get(task_version=tv)
            assert m.is_critical is True

    def test_parallel_tasks_shorter_path_has_float(self):
        """
        A(8h) → C(4h)
        B(2h) → C(4h)
        تسک B باید float مثبت داشته باشد
        """
        task_a, tv_a = make_task(self.project, self.revision, "A", duration_hours=8)
        task_b, tv_b = make_task(self.project, self.revision, "B", duration_hours=2)
        task_c, tv_c = make_task(self.project, self.revision, "C", duration_hours=4)

        make_dependency(self.revision, task_a, task_c)
        make_dependency(self.revision, task_b, task_c)

        engine = CPMEngine(self.revision)
        engine.run()

        metrics_b = TaskScheduleMetrics.objects.get(task_version=tv_b)
        assert metrics_b.total_float_hours > 0
        assert metrics_b.is_critical is False

        metrics_a = TaskScheduleMetrics.objects.get(task_version=tv_a)
        assert metrics_a.is_critical is True

    def test_cycle_raises_value_error(self):
        """حلقه: A → B → A باید خطا بدهد"""
        task_a, _ = make_task(self.project, self.revision, "A", duration_hours=4)
        task_b, _ = make_task(self.project, self.revision, "B", duration_hours=4)

        make_dependency(self.revision, task_a, task_b)
        make_dependency(self.revision, task_b, task_a)

        engine = CPMEngine(self.revision)
        with pytest.raises(ValueError, match=r"[Cc]ycle|حلقه"):
            engine.run()

    def test_lag_pushes_successor_start(self):
        """A(8h) -[FS+4h lag]→ B(4h): شروع B باید 12h بعد از شروع A باشد"""
        task_a, tv_a = make_task(self.project, self.revision, "A", duration_hours=8)
        task_b, tv_b = make_task(self.project, self.revision, "B", duration_hours=4)
        make_dependency(self.revision, task_a, task_b, dep_type="FS", lag=4)

        engine = CPMEngine(self.revision)
        engine.run()

        m_a = TaskScheduleMetrics.objects.get(task_version=tv_a)
        m_b = TaskScheduleMetrics.objects.get(task_version=tv_b)
        diff = (m_b.early_start - m_a.early_start).total_seconds() / 3600
        assert diff >= 12  # 8h duration + 4h lag

    def test_metrics_stored_in_db(self):
        task, tv = make_task(self.project, self.revision, duration_hours=8)
        engine = CPMEngine(self.revision)
        engine.run()

        assert TaskScheduleMetrics.objects.filter(task_version=tv).exists()

    def test_ef_equals_es_plus_duration(self):
        task, tv = make_task(self.project, self.revision, duration_hours=10)
        engine = CPMEngine(self.revision)
        engine.run()

        m = TaskScheduleMetrics.objects.get(task_version=tv)
        diff_hours = (m.early_finish - m.early_start).total_seconds() / 3600
        assert abs(diff_hours - 10) < 0.1


# ══════════════════════════════════════════════════════════
#  run-cpm API endpoint
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestRunCPMEndpoint:

    def test_run_cpm_returns_gantt_data(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin)
        make_task(project, revision, "تسک A", duration_hours=8)

        resp = api(admin).post(
            reverse("revision-run-cpm", kwargs={"pk": revision.id})
        )
        assert resp.status_code == status.HTTP_200_OK
        assert "nodes" in resp.data
        assert "dependencies" in resp.data

    def test_run_cpm_on_locked_revision_is_forbidden(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, approved=True)
        make_task(project, revision, "تسک", duration_hours=4)

        resp = api(admin).post(
            reverse("revision-run-cpm", kwargs={"pk": revision.id})
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_run_cpm_with_cycle_returns_400(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin)
        task_a, _ = make_task(project, revision, "A", duration_hours=4)
        task_b, _ = make_task(project, revision, "B", duration_hours=4)
        make_dependency(revision, task_a, task_b)
        make_dependency(revision, task_b, task_a)

        resp = api(admin).post(
            reverse("revision-run-cpm", kwargs={"pk": revision.id})
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_run_cpm_requires_authentication(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project)

        resp = APIClient().post(
            reverse("revision-run-cpm", kwargs={"pk": revision.id})
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
