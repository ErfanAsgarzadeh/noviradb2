from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from auditlog.models import AuditEvent
from meeting_management.models import (
    ActionStatus,
    CompletionSubmissionStatus,
    MeetingMinutesVersion,
    MeetingStatus,
    MinuteVersionStatus,
)
from meeting_management.services import (
    accept_completion,
    approve_minutes,
    decide_deadline_change,
    request_completion_revision,
    request_deadline_change,
    submit_completion,
    submit_minutes,
    submit_progress,
    transition_meeting,
)
from tests.meeting_management.utils import make_action, make_meeting


@pytest.mark.django_db
def test_meeting_and_minutes_transitions_are_validated_and_audited():
    ctx = make_meeting()
    meeting = ctx["meeting"]

    transition_meeting(meeting, actor=ctx["organizer"], target_status=MeetingStatus.SCHEDULED)
    meeting.refresh_from_db()
    assert meeting.status == MeetingStatus.SCHEDULED

    minute = MeetingMinutesVersion.objects.create(meeting=meeting, version_number=1, author=ctx["secretary"])
    transition_meeting(meeting, actor=ctx["organizer"], target_status=MeetingStatus.HELD)
    transition_meeting(meeting, actor=ctx["organizer"], target_status=MeetingStatus.MINUTES_DRAFTING)
    submit_minutes(minute, actor=ctx["secretary"])
    minute.refresh_from_db()
    meeting.refresh_from_db()
    assert minute.status == MinuteVersionStatus.SUBMITTED
    assert meeting.status == MeetingStatus.IN_REVIEW

    approve_minutes(minute, actor=ctx["chair"])
    minute.refresh_from_db()
    meeting.refresh_from_db()
    assert minute.status == MinuteVersionStatus.APPROVED
    assert minute.locked_at is not None
    assert meeting.status == MeetingStatus.APPROVED
    assert AuditEvent.objects.filter(action="minutes_approved").exists()


@pytest.mark.django_db
def test_invalid_meeting_transition_is_rejected():
    ctx = make_meeting()

    with pytest.raises(ValidationError):
        transition_meeting(ctx["meeting"], actor=ctx["organizer"], target_status=MeetingStatus.APPROVED)


@pytest.mark.django_db
def test_progress_completion_and_acceptance_require_reviewer_not_responsible():
    ctx = make_action()
    action = ctx["action"]

    report = submit_progress(action, actor=ctx["responsible"], progress_percent=45, work_completed="Started")
    action.refresh_from_db()
    assert report.progress_percent == 45
    assert action.status == ActionStatus.IN_PROGRESS

    submission = submit_completion(action, actor=ctx["responsible"], summary="Done")
    action.refresh_from_db()
    assert action.status == ActionStatus.SUBMITTED_FOR_REVIEW

    with pytest.raises(ValidationError):
        accept_completion(submission, actor=ctx["responsible"])

    accept_completion(submission, actor=ctx["reviewer"], comment="Accepted")
    submission.refresh_from_db()
    action.refresh_from_db()
    assert submission.status == CompletionSubmissionStatus.ACCEPTED
    assert action.status == ActionStatus.CLOSED
    assert action.closed_by == ctx["reviewer"]


@pytest.mark.django_db
def test_completion_revision_request_returns_action_for_rework():
    ctx = make_action()
    submission = submit_completion(ctx["action"], actor=ctx["responsible"], summary="Done")

    request_completion_revision(submission, actor=ctx["reviewer"], comment="Need evidence")
    submission.refresh_from_db()
    ctx["action"].refresh_from_db()

    assert submission.status == CompletionSubmissionStatus.REVISION_REQUESTED
    assert ctx["action"].status == ActionStatus.REVISION_REQUESTED


@pytest.mark.django_db
def test_deadline_change_preserves_original_and_updates_current_only_on_approval():
    ctx = make_action()
    action = ctx["action"]
    original_due = action.original_due_date
    requested_due = original_due + timedelta(days=10)

    request = request_deadline_change(action, actor=ctx["responsible"], requested_due_date=requested_due, reason="Dependency")
    decide_deadline_change(request, actor=ctx["reviewer"], approved=True, reason="Approved")
    action.refresh_from_db()

    assert action.original_due_date == original_due
    assert action.current_due_date == requested_due
    assert action.delay_against_original_days == 10


@pytest.mark.django_db
def test_blocked_progress_sets_blocked_state():
    ctx = make_action()

    submit_progress(ctx["action"], actor=ctx["responsible"], progress_percent=20, blockers="Waiting for input")
    ctx["action"].refresh_from_db()

    assert ctx["action"].status == ActionStatus.BLOCKED
    assert ctx["action"].blocked_reason == "Waiting for input"
