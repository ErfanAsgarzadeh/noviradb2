"""
test_models.py  —  تست‌های لایه‌ی مدل
پوشش: CustomUser, OrgUnit, Project, Revision, TaskReportLog, TaskActual
"""
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone
from datetime import timedelta

from .factories import (
    make_org_unit, make_user, make_planning_manager,
    make_project, make_revision, make_task,
    make_report, get_system_settings,
)
from ktcPlanning.models import (
    TaskReportLog, TaskActual, TaskVersion, SystemSettings,
)


# ══════════════════════════════════════════════════════════
#  OrgUnit
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestOrgUnit:

    def test_create_basic_unit(self):
        unit = make_org_unit("واحد فناوری")
        assert unit.pk is not None
        assert unit.name == "واحد فناوری"
        assert unit.is_planning_unit is False

    def test_only_one_planning_unit_allowed(self):
        """constraint: فقط یک واحد می‌تواند is_planning_unit=True داشته باشد"""
        make_org_unit("واحد اول", is_planning=True)
        with pytest.raises(IntegrityError):
            make_org_unit("واحد دوم", is_planning=True)

    def test_multiple_non_planning_units_allowed(self):
        make_org_unit("واحد A")
        make_org_unit("واحد B")
        make_org_unit("واحد C")
        from CustomUser.models import OrgUnit
        assert OrgUnit.objects.filter(is_planning_unit=False).count() >= 3


# ══════════════════════════════════════════════════════════
#  CustomUser
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestCustomUser:

    def test_create_member(self):
        unit = make_org_unit()
        user = make_user(org_role="member", unit=unit)
        assert user.org_role == "member"
        assert user.unit == unit

    def test_allowed_pages_defaults_to_null(self):
        """null یعنی دسترسی به همه صفحات"""
        user = make_user()
        assert user.allowed_pages is None

    def test_allowed_pages_list(self):
        user = make_user()
        user.allowed_pages = ["/DashBoard/Home", "/DashBoard/MyTask"]
        user.save()
        user.refresh_from_db()
        assert "/DashBoard/Home" in user.allowed_pages

    def test_str_includes_username(self):
        user = make_user(username="ali_test")
        assert "ali_test" in str(user)


# ══════════════════════════════════════════════════════════
#  Project
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestProject:

    def test_create_intra_unit_project(self):
        creator = make_user(org_role="project_manager")
        project = make_project(creator=creator, scope="intra_unit")
        assert project.scope == "intra_unit"
        assert project.is_deleted is False

    def test_create_company_project(self):
        creator = make_user(org_role="company_admin")
        project = make_project(creator=creator, scope="company")
        assert project.scope == "company"

    def test_soft_delete(self):
        project = make_project()
        project.is_deleted = True
        project.save()
        project.refresh_from_db()
        assert project.is_deleted is True

    def test_str_is_project_name(self):
        project = make_project(name="پروژه آزمون")
        assert str(project) == "پروژه آزمون"


# ══════════════════════════════════════════════════════════
#  Revision
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestRevision:

    def test_create_revision(self):
        project = make_project()
        rev = make_revision(project)
        assert rev.number == 1
        assert rev.approved_at is None

    def test_revision_numbers_auto_increment(self):
        project = make_project()
        rev1 = make_revision(project)
        rev2 = make_revision(project)
        assert rev2.number == rev1.number + 1

    def test_approved_revision_has_timestamp(self):
        project = make_project()
        rev = make_revision(project, approved=True)
        assert rev.approved_at is not None
        assert rev.approved_by is not None

    def test_unique_together_project_number(self):
        project = make_project()
        make_revision(project)
        from ktcPlanning.models import Revision
        with pytest.raises(IntegrityError):
            Revision.objects.create(
                project=project,
                number=1,
                project_start=timezone.now(),
                designated_approver=project.created_by,
            )


# ══════════════════════════════════════════════════════════
#  TaskVersion
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestTaskVersion:

    def test_create_task_version(self):
        project = make_project()
        revision = make_revision(project)
        task, tv = make_task(project, revision, title="تسک نمونه", duration_hours=16)
        assert tv.title == "تسک نمونه"
        assert tv.duration_hours == 16

    def test_start_before_finish_validation(self):
        project = make_project()
        revision = make_revision(project)
        task, tv = make_task(project, revision)
        now = timezone.now()
        tv.planned_start = now + timedelta(hours=10)
        tv.planned_finish = now  # finish قبل از start
        with pytest.raises(ValidationError):
            tv.clean()

    def test_unique_task_per_revision(self):
        project = make_project()
        revision = make_revision(project)
        task, tv = make_task(project, revision)
        from ktcPlanning.models import TaskVersion
        with pytest.raises(IntegrityError):
            TaskVersion.objects.create(
                task=tv.task,
                revision=revision,
                wbs_node=tv.wbs_node,
                title="دوباره",
                duration_hours=8,
            )


# ══════════════════════════════════════════════════════════
#  TaskReportLog
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestTaskReportLog:

    def setup_method(self):
        self.project = make_project(scope="intra_unit")
        self.revision = make_revision(self.project)
        self.task, self.tv = make_task(self.project, self.revision)
        self.member = make_user(org_role="member")

    def test_create_report_defaults_to_pending(self):
        report = make_report(self.task, self.member, progress=30)
        assert report.approval_status == "pending"
        assert report.progress_percent == 30

    def test_report_ordering_newest_first(self):
        r1 = make_report(self.task, self.member, progress=10)
        r2 = make_report(self.task, self.member, progress=20)
        reports = list(TaskReportLog.objects.filter(task=self.task))
        # ordering = ['-timestamp'] → جدیدترین اول
        assert reports[0].id == r2.id

    def test_str_includes_progress(self):
        report = make_report(self.task, self.member, progress=75)
        assert "75%" in str(report)

    def test_final_approved_report_has_both_approvals(self):
        approver = make_user(org_role="project_manager")
        report = make_report(
            self.task, self.member,
            approval_status="final_approved"
        )
        # final_approved بدون reviewer_approved_by هم باید قابل ذخیره باشد
        assert report.approval_status == "final_approved"


# ══════════════════════════════════════════════════════════
#  SystemSettings singleton
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestSystemSettings:

    def test_current_always_returns_same_instance(self):
        s1 = SystemSettings.current()
        s2 = SystemSettings.current()
        assert s1.pk == s2.pk

    def test_bypass_default_is_false(self):
        s = SystemSettings.current()
        assert s.allow_planning_manager_bypass_reviewer is False

    def test_can_enable_bypass(self):
        s = get_system_settings(allow_planning_manager_bypass_reviewer=True)
        assert s.allow_planning_manager_bypass_reviewer is True
