import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ktcPlanning.models import Project, Revision
from ktcPlanning.revision_policy import (
    ROLE_EXECUTION,
    assign_working_revision,
    get_official_revision,
    promote_approved_revision,
    resolve_designated_approver,
)
from ktcPlanning.variance_engine import EVMEngine

from .factories import (
    make_company_admin,
    make_org_unit,
    make_planning_manager,
    make_project,
    make_revision,
    make_user,
)


pytestmark = pytest.mark.django_db


def test_new_project_has_an_explicit_revision_spine():
    project = make_project()
    project.refresh_from_db()
    revision_zero = project.revisions.get(number=0)

    assert project.active_baseline_revision_id == revision_zero.id
    assert project.current_execution_revision_id == revision_zero.id
    assert project.current_forecast_revision_id == revision_zero.id
    assert project.working_revision_id == revision_zero.id
    assert project.current_data_date is not None
    assert project.lifecycle_status == Project.LIFECYCLE_PLANNING


def test_official_resolver_never_falls_back_to_latest_revision():
    project = make_project()
    make_revision(project)
    project.current_execution_revision = None
    project.save(update_fields=['current_execution_revision'])

    assert get_official_revision(project, ROLE_EXECUTION, required=False) is None


def test_only_one_official_working_revision_can_be_assigned():
    project = make_project()
    other = Revision.objects.create(
        project=project,
        number=1,
        created_by=project.created_by,
        designated_approver=project.created_by,
        project_start=timezone.now(),
    )

    with pytest.raises(ValidationError):
        assign_working_revision(project, other)


def test_approval_promotion_updates_all_execution_roles_atomically():
    project = make_project()
    revision = Revision.objects.create(
        project=project,
        number=1,
        is_baseline=True,
        created_by=project.created_by,
        designated_approver=project.created_by,
        approved_by=project.created_by,
        approved_at=timezone.now(),
        project_start=timezone.now(),
    )
    project.working_revision = revision
    project.save(update_fields=['working_revision'])

    promote_approved_revision(revision, data_date=timezone.now())
    project.refresh_from_db()

    assert project.active_baseline_revision_id == revision.id
    assert project.current_execution_revision_id == revision.id
    assert project.current_forecast_revision_id == revision.id
    assert project.working_revision_id is None
    assert project.lifecycle_status == Project.LIFECYCLE_ACTIVE


def test_company_revision_approver_is_planning_unit_manager():
    planning_unit = make_org_unit(is_planning=True)
    planning_manager = make_planning_manager(planning_unit)
    project = make_project(creator=make_company_admin(), scope='company')

    assert resolve_designated_approver(project) == planning_manager


def test_intra_unit_revision_approver_is_owner_unit_manager():
    owner_unit = make_org_unit()
    manager = make_user(org_role='unit_manager', unit=owner_unit)
    owner_unit.manager = manager
    owner_unit.save(update_fields=['manager'])
    project = make_project(creator=make_user(unit=owner_unit), scope='intra_unit')
    project.owner_unit = owner_unit
    project.save(update_fields=['owner_unit'])

    assert resolve_designated_approver(project) == manager


def test_evm_uses_execution_pointer_not_newest_revision():
    project = make_project()
    official = make_revision(project, is_baseline=True, approved=True)
    newer = make_revision(project, approved=True)
    project.current_execution_revision = official
    project.active_baseline_revision = official
    project.save(update_fields=['current_execution_revision', 'active_baseline_revision'])

    engine = EVMEngine(project.id)

    assert newer.number > official.number
    assert engine.current_rev.id == official.id
