"""
factories.py  —  ابزارهای کمکی برای ساختن داده‌های تست
"""
import uuid
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model

from CustomUser.models import OrgUnit
from ktcPlanning.models import (
    Project, Revision, WBSNode, WBSNodeVersion,
    Task, TaskVersion, TaskRole, TaskReportLog,
    TaskActual, Calendar, SystemSettings,
)

User = get_user_model()


# ──────────────────────────────────────────────
#  کاربران
# ──────────────────────────────────────────────

def make_org_unit(name="واحد تست", is_planning=False):
    return OrgUnit.objects.create(name=name, is_planning_unit=is_planning)


def make_user(username=None, org_role="member", unit=None, is_superuser=False):
    username = username or f"user_{uuid.uuid4().hex[:8]}"
    u = User.objects.create_user(
        username=username,
        password="Test@1234",
        org_role=org_role,
        unit=unit,
    )
    u.is_superuser = is_superuser
    u.save()
    return u


def make_company_admin(unit=None):
    return make_user(org_role="company_admin", unit=unit)


def make_planning_manager(planning_unit=None):
    """کاربری که مدیر واحد برنامه‌ریزی است → is_planning_manager() == True"""
    if planning_unit is None:
        planning_unit = make_org_unit(name="واحد برنامه‌ریزی", is_planning=True)
    user = make_user(org_role="unit_manager", unit=planning_unit)
    planning_unit.manager = user
    planning_unit.save()
    return user


def make_project_manager(unit=None):
    return make_user(org_role="project_manager", unit=unit)


def make_member(unit=None):
    return make_user(org_role="member", unit=unit)


# ──────────────────────────────────────────────
#  پروژه و ریویژن
# ──────────────────────────────────────────────

def make_calendar(project=None, is_default=True):
    cal = Calendar.objects.create(
        project=project,
        name="تقویم پیش‌فرض",
        is_default=is_default,
    )
    return cal


def make_project(creator=None, scope="intra_unit", name=None):
    if creator is None:
        creator = make_company_admin()
    name = name or f"پروژه_{uuid.uuid4().hex[:6]}"
    project = Project.objects.create(
        name=name,
        created_by=creator,
        scope=scope,
        start_date=timezone.now(),
        end_date=timezone.now() + timedelta(days=90),
    )
    make_calendar(project=project)
    return project


def make_revision(project, creator=None, is_baseline=False, approved=False):
    creator = creator or project.created_by
    rev_count = Revision.objects.filter(project=project).count()
    approver = creator
    rev = Revision.objects.create(
        project=project,
        number=rev_count + 1,
        description="ریویژن تست",
        is_baseline=is_baseline,
        created_by=creator,
        designated_approver=approver,
        project_start=timezone.now(),
        project_end=timezone.now() + timedelta(days=60),
    )
    if approved:
        rev.approved_by = creator
        rev.approved_at = timezone.now()
        rev.save()
    return rev


# ──────────────────────────────────────────────
#  WBS و تسک
# ──────────────────────────────────────────────

def make_wbs_node(project, revision, title="گره WBS"):
    node = WBSNode.objects.create(project=project)
    node_version = WBSNodeVersion.objects.create(
        node=node,
        project=project,
        revision=revision,
        title=title,
        is_deleted=False,
    )
    return node, node_version


def make_task(project, revision, title="تسک تست", duration_hours=8):
    _, wbs_version = make_wbs_node(project, revision)
    task = Task.objects.create(project=project, created_by=project.created_by)
    now = timezone.now()
    task_version = TaskVersion.objects.create(
        task=task,
        revision=revision,
        wbs_node=wbs_version,
        title=title,
        duration_hours=duration_hours,
        planned_start=now,
        planned_finish=now + timedelta(hours=duration_hours),
        sequence=1,
    )
    return task, task_version


# ──────────────────────────────────────────────
#  نقش‌های تسک
# ──────────────────────────────────────────────

def assign_role(task, revision, user, role="executor"):
    return TaskRole.objects.create(
        revision=revision,
        task=task,
        user=user,
        role=role,
    )


# ──────────────────────────────────────────────
#  گزارش تسک
# ──────────────────────────────────────────────

def make_report(task, user, progress=50, status_val="on-track", approval_status="pending"):
    return TaskReportLog.objects.create(
        task=task,
        user=user,
        status=status_val,
        progress_percent=progress,
        time_spent_hours=4,
        approval_status=approval_status,
    )


# ──────────────────────────────────────────────
#  تنظیمات سیستم
# ──────────────────────────────────────────────

def get_system_settings(**kwargs):
    s = SystemSettings.current()
    for k, v in kwargs.items():
        setattr(s, k, v)
    s.save()
    return s
