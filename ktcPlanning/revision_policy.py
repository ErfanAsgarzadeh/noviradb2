"""Single source of truth for official project schedule revisions."""

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Project, Revision


ROLE_BASELINE = "baseline"
ROLE_EXECUTION = "execution"
ROLE_FORECAST = "forecast"
ROLE_WORKING = "working"

ROLE_FIELDS = {
    ROLE_BASELINE: "active_baseline_revision",
    ROLE_EXECUTION: "current_execution_revision",
    ROLE_FORECAST: "current_forecast_revision",
    ROLE_WORKING: "working_revision",
}


class OfficialRevisionMissing(ValidationError):
    pass


def get_official_revision(project: Project, role: str, *, required: bool = True):
    field = ROLE_FIELDS.get(role)
    if field is None:
        raise ValueError(f"Unknown revision role: {role}")
    revision = getattr(project, field, None)
    if revision and revision.project_id == project.pk and not revision.is_deleted:
        return revision
    if required:
        raise OfficialRevisionMissing({
            field: f"Project has no valid official {role} revision."
        })
    return None


def official_revision_ids(project_ids, role: str):
    field = ROLE_FIELDS.get(role)
    if field is None:
        raise ValueError(f"Unknown revision role: {role}")
    return Project.objects.filter(pk__in=project_ids, is_deleted=False).exclude(
        **{f"{field}_id__isnull": True}
    ).values_list(f"{field}_id", flat=True)


def resolve_designated_approver(project: Project, requested_user=None):
    expected = project.get_default_approver()
    if expected is None:
        raise ValidationError({
            "designatedApproverId": (
                "No official approver is configured for this project. "
                "Configure the planning-unit manager for company projects or "
                "the owner-unit manager for intra-unit projects."
            )
        })
    if requested_user is not None and requested_user.pk != expected.pk:
        raise ValidationError({
            "designatedApproverId": "Selected user is not the official approver for this project."
        })
    return expected


@transaction.atomic
def assign_working_revision(project: Project, revision: Revision):
    locked_project = Project.objects.select_for_update().get(pk=project.pk)
    if revision.project_id != locked_project.pk or revision.is_deleted:
        raise ValidationError({"working_revision": "Revision does not belong to this project."})
    if revision.approved_at is not None:
        raise ValidationError({"working_revision": "Approved revisions cannot be working revisions."})
    existing = locked_project.working_revision
    if existing and existing.pk != revision.pk and not existing.is_deleted and existing.approved_at is None:
        raise ValidationError({
            "working_revision": f"Revision {existing.number} is already the official working revision."
        })
    locked_project.working_revision = revision
    if locked_project.lifecycle_status == Project.LIFECYCLE_DRAFT:
        locked_project.lifecycle_status = Project.LIFECYCLE_PLANNING
    locked_project.save(update_fields=["working_revision", "lifecycle_status"])
    return locked_project


@transaction.atomic
def promote_approved_revision(revision: Revision, *, data_date=None):
    if revision.approved_at is None:
        raise ValidationError({"revision": "Only approved revisions can be promoted."})
    project = Project.objects.select_for_update().get(pk=revision.project_id)
    project.current_execution_revision = revision
    project.current_forecast_revision = revision
    if revision.is_baseline:
        project.active_baseline_revision = revision
    if project.working_revision_id == revision.pk:
        project.working_revision = None
    if data_date is not None:
        project.current_data_date = data_date
    if project.active_baseline_revision_id and project.current_data_date:
        project.lifecycle_status = Project.LIFECYCLE_ACTIVE
    else:
        project.lifecycle_status = Project.LIFECYCLE_PLANNING
    project.save(update_fields=[
        "active_baseline_revision",
        "current_execution_revision",
        "current_forecast_revision",
        "working_revision",
        "current_data_date",
        "lifecycle_status",
    ])
    return project
