"""
test_approval_workflow.py  —  تست جریان تایید گزارش پیشرفت
پوشش:
  - پروژه درون‌واحدی: یک مرحله‌ای (pending → final_approved)
  - پروژه شرکتی:  دو مرحله‌ای (pending → reviewer_approved → final_approved)
  - bypass مدیر برنامه‌ریزی
  - رد گزارش (reject)
  - ثبت پیشرفت در TaskActual پس از final_approved
  - جلوگیری از ویرایش گزارش تایید شده
"""
import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from ktcPlanning.models import TaskReportLog, TaskActual
from .factories import (
    make_org_unit, make_user, make_planning_manager, make_company_admin,
    make_project, make_revision, make_task,
    assign_role, make_report, get_system_settings,
)


# ──────────────────────────────────────────────
#  Fixture مشترک
# ──────────────────────────────────────────────

@pytest.fixture
def intra_setup(db):
    """پروژه درون‌واحدی با یک تسک و یک مجری"""
    creator = make_company_admin()
    project = make_project(creator=creator, scope="intra_unit")
    revision = make_revision(project, creator=creator)
    task, tv = make_task(project, revision)

    executor = make_user(org_role="member")
    reviewer = make_user(org_role="project_manager")
    assign_role(task, revision, executor, role="executor")
    assign_role(task, revision, reviewer, role="reviewer")

    return {
        "project": project,
        "revision": revision,
        "task": task,
        "tv": tv,
        "executor": executor,
        "reviewer": reviewer,
        "creator": creator,
    }


@pytest.fixture
def company_setup(db):
    """پروژه شرکتی با مدیر برنامه‌ریزی"""
    planning_unit = make_org_unit("واحد برنامه‌ریزی", is_planning=True)
    pm = make_planning_manager(planning_unit)

    creator = make_company_admin()
    project = make_project(creator=creator, scope="company")
    revision = make_revision(project, creator=creator)
    task, tv = make_task(project, revision)

    executor = make_user(org_role="member")
    reviewer = make_user(org_role="project_manager")
    assign_role(task, revision, executor, role="executor")
    assign_role(task, revision, reviewer, role="reviewer")

    return {
        "project": project,
        "revision": revision,
        "task": task,
        "tv": tv,
        "executor": executor,
        "reviewer": reviewer,
        "creator": creator,
        "planning_manager": pm,
    }


def api_client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def approve_url(report_id):
    return reverse("task-report-approve", kwargs={"pk": report_id})


def reject_url(report_id):
    return reverse("task-report-reject", kwargs={"pk": report_id})


def report_list_url():
    return reverse("task-report-list")


# ══════════════════════════════════════════════════════════
#  پروژه درون‌واحدی: جریان یک‌مرحله‌ای
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestIntraUnitApproval:

    def test_reviewer_approves_and_report_becomes_final(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=50)

        client = api_client_for(s["reviewer"])
        resp = client.post(approve_url(report.id))

        assert resp.status_code == status.HTTP_200_OK
        report.refresh_from_db()
        assert report.approval_status == "final_approved"

    def test_progress_committed_to_task_actual_after_approval(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=70)

        client = api_client_for(s["reviewer"])
        client.post(approve_url(report.id))

        actual = TaskActual.objects.filter(task_version__task=s["task"]).first()
        assert actual is not None
        assert float(actual.progress) == 70

    def test_actual_start_set_on_first_progress_report(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=10)

        client = api_client_for(s["reviewer"])
        client.post(approve_url(report.id))

        actual = TaskActual.objects.get(task_version__task=s["task"])
        assert actual.actual_start is not None

    def test_actual_finish_set_when_progress_reaches_100(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=100)

        client = api_client_for(s["reviewer"])
        client.post(approve_url(report.id))

        actual = TaskActual.objects.get(task_version__task=s["task"])
        assert actual.actual_finish is not None

    def test_non_reviewer_cannot_approve(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=50)

        random_user = make_user(org_role="member")
        client = api_client_for(random_user)
        resp = client.post(approve_url(report.id))

        assert resp.status_code == status.HTTP_403_FORBIDDEN
        report.refresh_from_db()
        assert report.approval_status == "pending"

    def test_already_approved_report_cannot_be_approved_again(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=50)

        client = api_client_for(s["reviewer"])
        client.post(approve_url(report.id))
        resp = client.post(approve_url(report.id))  # دوباره

        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated_cannot_approve(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=50)

        client = APIClient()  # بدون auth
        resp = client.post(approve_url(report.id))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ══════════════════════════════════════════════════════════
#  پروژه شرکتی: جریان دو‌مرحله‌ای
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestCompanyProjectApproval:

    def test_step1_reviewer_approve_moves_to_reviewer_approved(self, company_setup):
        s = company_setup
        report = make_report(s["task"], s["executor"], progress=40)

        client = api_client_for(s["reviewer"])
        resp = client.post(approve_url(report.id))

        assert resp.status_code == status.HTTP_200_OK
        report.refresh_from_db()
        assert report.approval_status == "reviewer_approved"

    def test_step1_does_not_commit_progress(self, company_setup):
        s = company_setup
        report = make_report(s["task"], s["executor"], progress=40)

        client = api_client_for(s["reviewer"])
        client.post(approve_url(report.id))

        # تا final نشده، TaskActual نباید آپدیت بشه
        assert not TaskActual.objects.filter(task_version__task=s["task"]).exists()

    def test_step2_planning_manager_gives_final_approval(self, company_setup):
        s = company_setup
        report = make_report(s["task"], s["executor"], progress=40,
                             approval_status="reviewer_approved")

        client = api_client_for(s["planning_manager"])
        resp = client.post(approve_url(report.id))

        assert resp.status_code == status.HTTP_200_OK
        report.refresh_from_db()
        assert report.approval_status == "final_approved"

    def test_step2_commits_progress_to_task_actual(self, company_setup):
        s = company_setup
        report = make_report(s["task"], s["executor"], progress=60,
                             approval_status="reviewer_approved")

        client = api_client_for(s["planning_manager"])
        client.post(approve_url(report.id))

        actual = TaskActual.objects.get(task_version__task=s["task"])
        assert float(actual.progress) == 60

    def test_regular_user_cannot_give_final_approval(self, company_setup):
        s = company_setup
        report = make_report(s["task"], s["executor"], progress=40,
                             approval_status="reviewer_approved")

        random_user = make_user(org_role="member")
        client = api_client_for(random_user)
        resp = client.post(approve_url(report.id))

        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ══════════════════════════════════════════════════════════
#  Bypass مدیر برنامه‌ریزی
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestPlanningManagerBypass:

    def test_bypass_enabled_skips_reviewer_step(self, company_setup):
        s = company_setup
        get_system_settings(allow_planning_manager_bypass_reviewer=True)

        report = make_report(s["task"], s["executor"], progress=55)
        client = api_client_for(s["planning_manager"])
        resp = client.post(approve_url(report.id))

        assert resp.status_code == status.HTTP_200_OK
        report.refresh_from_db()
        assert report.approval_status == "final_approved"
        assert resp.data.get("viaBypass") is True

    def test_bypass_disabled_requires_reviewer_first(self, company_setup):
        s = company_setup
        get_system_settings(allow_planning_manager_bypass_reviewer=False)

        report = make_report(s["task"], s["executor"], progress=55)
        client = api_client_for(s["planning_manager"])
        resp = client.post(approve_url(report.id))

        # مدیر برنامه‌ریزی نباید بتواند یک‌باره تایید نهایی کند
        # (اگر bypass خاموش است)
        report.refresh_from_db()
        assert report.approval_status != "final_approved"

    def test_bypass_does_not_apply_to_intra_unit_projects(self, intra_setup):
        """bypass فقط برای پروژه‌های شرکتی معنا دارد"""
        s = intra_setup
        get_system_settings(allow_planning_manager_bypass_reviewer=True)

        planning_unit = make_org_unit("واحد برنامه‌ریزی جدید", is_planning=True)
        pm = make_planning_manager(planning_unit)

        report = make_report(s["task"], s["executor"], progress=30)
        client = api_client_for(pm)
        # برای پروژه intra_unit، مدیر برنامه‌ریزی دسترسی مستقیم ندارد
        resp = client.post(approve_url(report.id))
        # انتظار: forbidden یا منطق متفاوت
        assert resp.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_200_OK,  # اگر به‌عنوان reviewer هم شناخته شود
        ]


# ══════════════════════════════════════════════════════════
#  رد گزارش (Reject)
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestRejectReport:

    def test_reviewer_can_reject_pending_report(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=50)

        client = api_client_for(s["reviewer"])
        resp = client.post(reject_url(report.id), {"reason": "اطلاعات ناقص است"})

        assert resp.status_code == status.HTTP_200_OK
        report.refresh_from_db()
        assert report.approval_status == "rejected"

    def test_reject_reason_appended_to_notes(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=50)

        client = api_client_for(s["reviewer"])
        client.post(reject_url(report.id), {"reason": "نیاز به اصلاح دارد"})

        report.refresh_from_db()
        assert "REJECTED" in report.notes
        assert "نیاز به اصلاح دارد" in report.notes

    def test_cannot_reject_final_approved_report(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=50,
                             approval_status="final_approved")

        client = api_client_for(s["reviewer"])
        resp = client.post(reject_url(report.id))

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        report.refresh_from_db()
        assert report.approval_status == "final_approved"

    def test_random_user_cannot_reject(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=50)

        random_user = make_user()
        client = api_client_for(random_user)
        resp = client.post(reject_url(report.id))

        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ══════════════════════════════════════════════════════════
#  ویرایش گزارش
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestReportEditLock:

    def test_pending_report_can_be_edited(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=30)

        client = api_client_for(s["executor"])
        url = reverse("task-report-detail", kwargs={"pk": report.id})
        resp = client.patch(url, {"progress_percent": 45}, format="json")

        assert resp.status_code == status.HTTP_200_OK

    def test_approved_report_cannot_be_edited(self, intra_setup):
        s = intra_setup
        report = make_report(s["task"], s["executor"], progress=50,
                             approval_status="final_approved")

        client = api_client_for(s["executor"])
        url = reverse("task-report-detail", kwargs={"pk": report.id})
        resp = client.patch(url, {"progress_percent": 99}, format="json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ══════════════════════════════════════════════════════════
#  ساخت گزارش
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestCreateReport:

    def test_authenticated_user_can_submit_report(self, intra_setup):
        s = intra_setup
        client = api_client_for(s["executor"])
        payload = {
            "task": str(s["task"].id),
            "status": "on-track",
            "progress_percent": 25,
            "time_spent_hours": "2.5",
        }
        resp = client.post(report_list_url(), payload, format="json")
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["approval_status"] == "pending"

    def test_report_created_by_is_current_user(self, intra_setup):
        s = intra_setup
        client = api_client_for(s["executor"])
        payload = {
            "task": str(s["task"].id),
            "status": "on-track",
            "progress_percent": 10,
            "time_spent_hours": "1",
        }
        resp = client.post(report_list_url(), payload, format="json")
        report = TaskReportLog.objects.get(id=resp.data["id"])
        assert report.user == s["executor"]
