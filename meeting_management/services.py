from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from auditlog.services import log_event

from .models import (
    ActionCompletionSubmission,
    ActionDeadlineChangeRequest,
    ActionDependency,
    ActionProgressReport,
    ActionStatus,
    CompletionSubmissionStatus,
    DeadlineRequestStatus,
    Meeting,
    MeetingMinutesVersion,
    MeetingNotification,
    MeetingStatus,
    MinuteVersionStatus,
    ResolutionAction,
)


class WorkflowError(ValidationError):
    pass


def _lock(model, pk):
    return model.objects.select_for_update().get(pk=pk)


def _require(condition, message):
    if not condition:
        raise WorkflowError(message)


def _is_admin(user):
    return bool(user and user.is_authenticated and getattr(user, "org_role", "") == "company_admin")


def _can_manage_meeting(user, meeting):
    return _is_admin(user) or user in {meeting.organizer, meeting.chairperson, meeting.secretary}


def _can_review_action(user, action):
    return _is_admin(user) or user in {action.reviewer, action.approver, action.accountable_user}


def _meeting_action_revision(actor):
    from ktcPlanning.models import Project, Revision, WBSNode, WBSNodeVersion
    from ktcPlanning.revision_policy import ROLE_WORKING, get_official_revision

    project, _ = Project.objects.get_or_create(
        name="System-Meeting-Actions",
        defaults={
            "created_by": actor,
            "owner_unit_id": getattr(actor, "unit_id", None),
            "scope": "intra_unit",
            "project_type": Project.TYPE_INTERNAL,
            "start_date": timezone.now(),
            "end_date": timezone.now() + timedelta(days=365),
            "lifecycle_status": Project.LIFECYCLE_PLANNING,
        },
    )
    revision = get_official_revision(project, ROLE_WORKING, required=False)
    if revision is None:
        revision = Revision.objects.filter(project=project, is_deleted=False, approved_at__isnull=True).order_by("number").first()
    if revision is None:
        revision = Revision.objects.create(
            project=project,
            number=(Revision.objects.filter(project=project).order_by("-number").values_list("number", flat=True).first() or -1) + 1,
            description="System meeting action task revision",
            created_by=actor,
            project_start=project.start_date or timezone.now(),
            project_end=project.end_date,
        )
    updates = []
    if project.working_revision_id != revision.pk:
        project.working_revision = revision
        updates.append("working_revision")
    if not project.current_execution_revision_id:
        project.current_execution_revision = revision
        updates.append("current_execution_revision")
    if not project.current_forecast_revision_id:
        project.current_forecast_revision = revision
        updates.append("current_forecast_revision")
    if updates:
        project.save(update_fields=updates)
    root = WBSNodeVersion.objects.filter(revision=revision, parent__isnull=True, is_deleted=False).first()
    if root is None:
        root_node = WBSNode.objects.create(project=project)
        root = WBSNodeVersion.objects.create(node=root_node, revision=revision, title=f"Root: {project.name}", sequence=1)
    node = WBSNodeVersion.objects.filter(revision=revision, title="Meeting Actions", is_deleted=False).first()
    if not node:
        base_node = WBSNode.objects.create(project=project)
        node = WBSNodeVersion.objects.create(
            node=base_node,
            revision=revision,
            parent=root,
            title="Meeting Actions",
            sequence=(root.children.count() + 1) if root else 1,
        )
    return project, revision, node


@transaction.atomic
def ensure_project_task_for_action(action, *, actor, assign_executor=True):
    from ktcPlanning.models import Task, TaskRole, TaskVersion

    action = _lock(ResolutionAction, action.pk)
    if action.task_id:
        return action.task

    project, revision, wbs_node = _meeting_action_revision(actor)
    due_finish = datetime.combine(action.current_due_date, time(hour=17))
    due_finish = timezone.make_aware(due_finish, timezone.get_current_timezone())
    planned_start_date = action.planned_start or timezone.localdate()
    planned_start = datetime.combine(planned_start_date, time(hour=8))
    planned_start = timezone.make_aware(planned_start, timezone.get_current_timezone())
    if planned_start > due_finish:
        planned_start = due_finish - timedelta(hours=8)

    task = Task.objects.create(project=project, created_by=actor)
    TaskVersion.objects.create(
        task=task,
        revision=revision,
        wbs_node=wbs_node,
        title=action.title,
        planned_start=planned_start,
        planned_finish=due_finish,
        duration_hours=Decimal("8.00"),
        description=action.description,
        sequence=TaskVersion.objects.filter(revision=revision, is_deleted=False).count() + 1,
    )
    if assign_executor:
        TaskRole.objects.get_or_create(revision=revision, task=task, user=action.responsible_user, role="executor")
    reviewer = action.reviewer or action.accountable_user
    if reviewer:
        TaskRole.objects.get_or_create(revision=revision, task=task, user=reviewer, role="reviewer")

    action.project = project
    action.task = task
    action.execution_mode = "project_task"
    action.save(update_fields=["project", "task", "execution_mode", "updated_at"])
    return task


@transaction.atomic
def transition_meeting(meeting, *, actor, target_status, reason=""):
    meeting = _lock(Meeting, meeting.pk)
    _require(_can_manage_meeting(actor, meeting), "You cannot manage this meeting.")
    transitions = {
        MeetingStatus.DRAFT: {MeetingStatus.SCHEDULED, MeetingStatus.CANCELLED},
        MeetingStatus.SCHEDULED: {MeetingStatus.HELD, MeetingStatus.CANCELLED},
        MeetingStatus.HELD: {MeetingStatus.MINUTES_DRAFTING, MeetingStatus.CANCELLED},
        MeetingStatus.MINUTES_DRAFTING: {MeetingStatus.IN_REVIEW, MeetingStatus.CANCELLED},
        MeetingStatus.IN_REVIEW: {MeetingStatus.APPROVED, MeetingStatus.MINUTES_DRAFTING, MeetingStatus.CANCELLED},
        MeetingStatus.APPROVED: {MeetingStatus.ARCHIVED},
    }
    _require(target_status in transitions.get(meeting.status, set()), f"Cannot transition meeting from {meeting.status} to {target_status}.")
    if target_status == MeetingStatus.CANCELLED:
        _require(reason, "Cancellation reason is required.")
        meeting.cancellation_reason = reason
    meeting.status = target_status
    meeting.full_clean()
    meeting.save()
    log_event("meeting_status_transition", target=meeting, category="business", extra={"to": target_status, "reason": reason})
    return meeting


@transaction.atomic
def submit_minutes(minute_version, *, actor):
    minute_version = _lock(MeetingMinutesVersion, minute_version.pk)
    _require(_can_manage_meeting(actor, minute_version.meeting), "You cannot submit these minutes.")
    _require(minute_version.status in {MinuteVersionStatus.DRAFT, MinuteVersionStatus.REVISION_REQUESTED}, "Only draft or revision-requested minutes can be submitted.")
    minute_version.status = MinuteVersionStatus.SUBMITTED
    minute_version.submitted_by = actor
    minute_version.submitted_at = timezone.now()
    minute_version.save()
    transition_meeting(minute_version.meeting, actor=actor, target_status=MeetingStatus.IN_REVIEW)
    log_event("minutes_submitted", target=minute_version, category="business")
    return minute_version


@transaction.atomic
def approve_minutes(minute_version, *, actor):
    minute_version = _lock(MeetingMinutesVersion, minute_version.pk)
    meeting = minute_version.meeting
    _require(_is_admin(actor) or actor == meeting.chairperson, "Only the chairperson or company admin can approve minutes.")
    _require(minute_version.status == MinuteVersionStatus.SUBMITTED, "Only submitted minutes can be approved.")
    minute_version.status = MinuteVersionStatus.APPROVED
    minute_version.approved_by = actor
    minute_version.approved_at = timezone.now()
    minute_version.locked_at = minute_version.approved_at
    minute_version.save()
    transition_meeting(meeting, actor=actor, target_status=MeetingStatus.APPROVED)
    log_event("minutes_approved", target=minute_version, category="business")
    return minute_version


@transaction.atomic
def request_minutes_revision(minute_version, *, actor, reason):
    minute_version = _lock(MeetingMinutesVersion, minute_version.pk)
    meeting = minute_version.meeting
    _require(_is_admin(actor) or actor == meeting.chairperson, "Only the chairperson or company admin can request revision.")
    _require(reason, "Revision reason is required.")
    _require(minute_version.status == MinuteVersionStatus.SUBMITTED, "Only submitted minutes can be returned.")
    minute_version.status = MinuteVersionStatus.REVISION_REQUESTED
    minute_version.save()
    transition_meeting(meeting, actor=actor, target_status=MeetingStatus.MINUTES_DRAFTING)
    log_event("minutes_revision_requested", target=minute_version, category="business", extra={"reason": reason})
    return minute_version


@transaction.atomic
def submit_progress(action, *, actor, progress_percent, work_completed="", next_steps="", blockers="", support_required="", forecast_completion_date=None):
    action = _lock(ResolutionAction, action.pk)
    _require(_is_admin(actor) or actor in {action.responsible_user, action.accountable_user}, "You cannot report progress for this action.")
    _require(action.status not in {ActionStatus.CLOSED, ActionStatus.CANCELLED}, "Closed or cancelled actions cannot receive progress.")
    report = ActionProgressReport.objects.create(
        action=action,
        reporter=actor,
        progress_percent=progress_percent,
        work_completed=work_completed,
        next_steps=next_steps,
        blockers=blockers,
        support_required=support_required,
        forecast_completion_date=forecast_completion_date,
    )
    action.progress_percent = progress_percent
    if blockers:
        action.status = ActionStatus.BLOCKED
        action.blocked_reason = blockers
    elif progress_percent >= 100:
        action.status = ActionStatus.SUBMITTED_FOR_REVIEW
    elif action.status in {ActionStatus.DRAFT, ActionStatus.NOT_STARTED, ActionStatus.BLOCKED}:
        action.status = ActionStatus.IN_PROGRESS
        action.blocked_reason = ""
    action.full_clean()
    action.save()
    log_event("action_progress_submitted", target=action, category="business", extra={"progress": progress_percent})
    return report


@transaction.atomic
def submit_completion(action, *, actor, summary):
    action = _lock(ResolutionAction, action.pk)
    _require(actor == action.responsible_user or _is_admin(actor), "Only the responsible user can submit completion.")
    _require(action.status not in {ActionStatus.CLOSED, ActionStatus.CANCELLED}, "Closed or cancelled actions cannot be submitted.")
    submission = ActionCompletionSubmission.objects.create(action=action, submitted_by=actor, summary=summary)
    action.status = ActionStatus.SUBMITTED_FOR_REVIEW
    action.progress_percent = max(action.progress_percent, 100)
    action.save()
    log_event("action_completion_submitted", target=submission, category="business")
    return submission


@transaction.atomic
def accept_completion(submission, *, actor, comment=""):
    submission = _lock(ActionCompletionSubmission, submission.pk)
    action = _lock(ResolutionAction, submission.action_id)
    _require(_can_review_action(actor, action), "You cannot accept this completion.")
    _require(actor != action.responsible_user or _is_admin(actor), "Responsible user cannot finally close their own action.")
    _require(submission.status == CompletionSubmissionStatus.SUBMITTED, "Only submitted completions can be accepted.")
    submission.status = CompletionSubmissionStatus.ACCEPTED
    submission.reviewed_by = actor
    submission.reviewed_at = timezone.now()
    submission.review_comment = comment
    submission.save()
    action.status = ActionStatus.CLOSED
    action.closed_at = timezone.now()
    action.closed_by = actor
    action.progress_percent = 100
    action.save()
    log_event("action_completion_accepted", target=submission, category="business")
    return submission


@transaction.atomic
def request_completion_revision(submission, *, actor, comment):
    submission = _lock(ActionCompletionSubmission, submission.pk)
    action = _lock(ResolutionAction, submission.action_id)
    _require(_can_review_action(actor, action), "You cannot review this completion.")
    _require(comment, "Revision comment is required.")
    submission.status = CompletionSubmissionStatus.REVISION_REQUESTED
    submission.reviewed_by = actor
    submission.reviewed_at = timezone.now()
    submission.review_comment = comment
    submission.save()
    action.status = ActionStatus.REVISION_REQUESTED
    action.save()
    log_event("action_completion_revision_requested", target=submission, category="business")
    return submission


@transaction.atomic
def request_deadline_change(action, *, actor, requested_due_date, reason):
    action = _lock(ResolutionAction, action.pk)
    _require(actor in {action.responsible_user, action.accountable_user} or _is_admin(actor), "You cannot request a deadline change.")
    _require(reason, "Deadline change reason is required.")
    request = ActionDeadlineChangeRequest.objects.create(
        action=action,
        requested_by=actor,
        current_due_date_at_request=action.current_due_date,
        requested_due_date=requested_due_date,
        reason=reason,
    )
    log_event("action_deadline_change_requested", target=request, category="business")
    return request


@transaction.atomic
def decide_deadline_change(request, *, actor, approved, reason=""):
    request = _lock(ActionDeadlineChangeRequest, request.pk)
    action = _lock(ResolutionAction, request.action_id)
    _require(_can_review_action(actor, action), "You cannot decide this deadline request.")
    _require(request.status == DeadlineRequestStatus.PENDING, "Deadline request has already been decided.")
    request.status = DeadlineRequestStatus.APPROVED if approved else DeadlineRequestStatus.REJECTED
    request.decided_by = actor
    request.decided_at = timezone.now()
    request.decision_reason = reason
    request.save()
    if approved:
        action.current_due_date = request.requested_due_date
        action.full_clean()
        action.save()
    log_event("action_deadline_change_decided", target=request, category="business", extra={"approved": approved})
    return request


def _reachable(start_id, target_id):
    stack = [start_id]
    seen = set()
    while stack:
        current = stack.pop()
        if current == target_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(ActionDependency.objects.filter(predecessor_id=current).values_list("successor_id", flat=True))
    return False


@transaction.atomic
def add_dependency(*, predecessor, successor, dependency_type, actor=None, description=""):
    _require(predecessor.pk != successor.pk, "An action cannot depend on itself.")
    _require(not _reachable(successor.pk, predecessor.pk), "Action dependency would create a cycle.")
    dependency = ActionDependency.objects.create(
        predecessor=predecessor,
        successor=successor,
        dependency_type=dependency_type,
        description=description,
    )
    log_event("action_dependency_added", target=dependency, category="business")
    return dependency


def create_notification(*, recipient, notification_type, title, idempotency_key, body="", meeting=None, action=None, due_at=None):
    notification, _ = MeetingNotification.objects.get_or_create(
        recipient=recipient,
        idempotency_key=idempotency_key,
        defaults={
            "notification_type": notification_type,
            "title": title,
            "body": body,
            "meeting": meeting,
            "action": action,
            "due_at": due_at,
        },
    )
    return notification


def generate_action_deadline_notifications(*, today=None):
    today = today or timezone.localdate()
    created = []
    actions = ResolutionAction.objects.filter(status__in=[ActionStatus.NOT_STARTED, ActionStatus.IN_PROGRESS, ActionStatus.BLOCKED])
    for action in actions.select_related("responsible_user", "accountable_user"):
        if action.current_due_date < today:
            created.append(create_notification(
                recipient=action.responsible_user,
                notification_type="overdue_action",
                title=f"Action overdue: {action.title}",
                idempotency_key=f"action:{action.pk}:overdue:{action.current_due_date}",
                action=action,
            ))
        elif (action.current_due_date - today).days <= 3:
            created.append(create_notification(
                recipient=action.responsible_user,
                notification_type="upcoming_action_deadline",
                title=f"Action due soon: {action.title}",
                idempotency_key=f"action:{action.pk}:upcoming:{action.current_due_date}",
                action=action,
            ))
    return created
