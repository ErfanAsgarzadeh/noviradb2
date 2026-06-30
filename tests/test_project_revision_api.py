"""
test_project_revision_api.py  —  تست API پروژه و ریویژن
پوشش:
  - CRUD پروژه
  - کنترل دسترسی ساخت/ویرایش/حذف
  - قفل کردن ریویژن (approve)
  - جلوگیری از ویرایش ریویژن قفل‌شده
  - gantt-data endpoint
"""
import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from ktcPlanning.models import Project, Revision
from .factories import (
    make_user, make_company_admin, make_project_manager, make_member,
    make_project, make_revision, make_task, assign_role,
    make_org_unit,
)


def api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# ══════════════════════════════════════════════════════════
#  Project CRUD
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestProjectCRUD:

    def test_company_admin_can_create_project(self):
        admin = make_company_admin()
        resp = api(admin).post(reverse("project-list"), {
            "name": "پروژه جدید",
            "scope": "intra_unit",
        }, format="json")
        assert resp.status_code == status.HTTP_201_CREATED
        assert Project.objects.filter(name="پروژه جدید").exists()

    def test_project_manager_can_create_project(self):
        pm = make_project_manager()
        resp = api(pm).post(reverse("project-list"), {
            "name": "پروژه مدیر",
            "scope": "intra_unit",
        }, format="json")
        assert resp.status_code == status.HTTP_201_CREATED

    def test_member_cannot_create_project(self):
        member = make_member()
        resp = api(member).post(reverse("project-list"), {
            "name": "پروژه غیرمجاز",
            "scope": "intra_unit",
        }, format="json")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_list_projects(self):
        resp = APIClient().get(reverse("project-list"))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_user_can_only_see_accessible_projects(self):
        admin = make_company_admin()
        project1 = make_project(creator=admin, name="پروژه عمومی")

        # کاربری که دسترسی به پروژه ندارد
        member = make_member()
        resp = api(member).get(reverse("project-list"))
        ids = [p["id"] for p in resp.data]
        assert str(project1.id) not in ids

    def test_creator_can_edit_project(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        resp = api(admin).patch(
            reverse("project-detail", kwargs={"pk": project.id}),
            {"name": "نام جدید"},
            format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        project.refresh_from_db()
        assert project.name == "نام جدید"

    def test_delete_is_soft_delete(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        resp = api(admin).delete(
            reverse("project-detail", kwargs={"pk": project.id})
        )
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        project.refresh_from_db()
        assert project.is_deleted is True

    def test_deleted_project_not_in_list(self):
        admin = make_company_admin()
        project = make_project(creator=admin, name="پروژه حذف‌شده")
        project.is_deleted = True
        project.save()

        resp = api(admin).get(reverse("project-list"))
        ids = [p["id"] for p in resp.data]
        assert str(project.id) not in ids


# ══════════════════════════════════════════════════════════
#  Revision
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestRevisionAPI:

    def test_create_revision_for_project(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        resp = api(admin).post(reverse("revision-list"), {
            "project": str(project.id),
            "number": 1,
            "description": "ریویژن اول",
            "project_start": "2025-01-01T08:00:00Z",
            "designated_approver": admin.id,
        }, format="json")
        assert resp.status_code == status.HTTP_201_CREATED

    def test_filter_revisions_by_project(self):
        admin = make_company_admin()
        project1 = make_project(creator=admin)
        project2 = make_project(creator=admin)
        rev1 = make_revision(project1)
        rev2 = make_revision(project2)

        resp = api(admin).get(
            reverse("revision-list"),
            {"project_id": str(project1.id)}
        )
        ids = [r["id"] for r in resp.data]
        assert str(rev1.id) in ids
        assert str(rev2.id) not in ids

    def test_approve_revision_locks_it(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        rev = make_revision(project, creator=admin)

        resp = api(admin).post(
            reverse("revision-approve", kwargs={"pk": rev.id})
        )
        assert resp.status_code == status.HTTP_200_OK
        rev.refresh_from_db()
        assert rev.approved_at is not None

    def test_cannot_approve_already_locked_revision(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        rev = make_revision(project, creator=admin, approved=True)

        resp = api(admin).post(
            reverse("revision-approve", kwargs={"pk": rev.id})
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_non_designated_approver_cannot_lock_revision(self):
        admin = make_company_admin()
        other_user = make_user(org_role="project_manager")
        project = make_project(creator=admin)
        rev = make_revision(project, creator=admin)
        # designated_approver = admin، اما other_user تلاش می‌کند

        resp = api(other_user).post(
            reverse("revision-approve", kwargs={"pk": rev.id})
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_soft_delete_revision(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        rev = make_revision(project)

        resp = api(admin).delete(
            reverse("revision-detail", kwargs={"pk": rev.id})
        )
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        rev.refresh_from_db()
        assert rev.is_deleted is True


# ══════════════════════════════════════════════════════════
#  Gantt Data
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestGanttData:

    def test_gantt_data_returns_nodes_and_dependencies(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin)
        make_task(project, revision, title="تسک گانت")

        resp = api(admin).get(
            reverse("revision-gantt-data", kwargs={"pk": revision.id})
        )
        assert resp.status_code == status.HTTP_200_OK
        assert "nodes" in resp.data
        assert "dependencies" in resp.data
        assert len(resp.data["nodes"]) >= 1

    def test_gantt_data_requires_authentication(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project)

        resp = APIClient().get(
            reverse("revision-gantt-data", kwargs={"pk": revision.id})
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


# ══════════════════════════════════════════════════════════
#  جلوگیری از ویرایش ریویژن قفل‌شده
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestLockedRevisionGuard:

    def test_cannot_add_task_to_locked_revision(self):
        """تسک نباید به revision قفل‌شده اضافه شود"""
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, approved=True)

        _, wbs_version = __import__(
            "tests.factories", fromlist=["make_wbs_node"]
        ).make_wbs_node(project, revision) if False else (None, None)

        # مستقیم از API تست می‌کنیم
        from .factories import make_wbs_node
        _, wbs_version = make_wbs_node(project, revision)

        resp = api(admin).post(reverse("activity-list"), {
            "revision": str(revision.id),
            "wbs_node": str(wbs_version.node.id),
            "title": "تسک غیرمجاز",
            "duration_hours": 8,
        }, format="json")
        # باید forbidden یا bad request برگرده
        assert resp.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_400_BAD_REQUEST,
        ]
