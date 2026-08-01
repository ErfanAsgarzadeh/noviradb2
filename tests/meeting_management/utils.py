from datetime import timedelta

from django.utils import timezone

from meeting_management.models import (
    Meeting,
    MeetingDecision,
    MeetingType,
    Resolution,
    ResolutionAction,
)
from tests.factories import make_member, make_org_unit


def make_meeting():
    unit = make_org_unit(name=f"Meeting unit {timezone.now().timestamp()}")
    organizer = make_member(unit=unit)
    chair = make_member(unit=unit)
    secretary = make_member(unit=unit)
    meeting_type = MeetingType.objects.create(code=f"MT{int(timezone.now().timestamp() * 1000000)}", name="Operating review")
    meeting = Meeting.objects.create(
        title="Operating review",
        meeting_type=meeting_type,
        owning_unit=unit,
        organizer=organizer,
        chairperson=chair,
        secretary=secretary,
        planned_start=timezone.now() + timedelta(days=1),
        planned_end=timezone.now() + timedelta(days=1, hours=1),
    )
    return {
        "unit": unit,
        "organizer": organizer,
        "chair": chair,
        "secretary": secretary,
        "meeting_type": meeting_type,
        "meeting": meeting,
    }


def make_action(action_number=1, resolution=None, due_date=None):
    meeting_ctx = make_meeting()
    meeting = meeting_ctx["meeting"]
    decision = MeetingDecision.objects.create(meeting=meeting, decision_number=action_number, title=f"Decision {action_number}")
    resolution = resolution or Resolution.objects.create(
        meeting=meeting,
        decision=decision,
        resolution_number=1,
        title="Resolution",
        owner_unit=meeting_ctx["unit"],
        owner=meeting_ctx["chair"],
    )
    accountable = make_member(unit=meeting_ctx["unit"])
    responsible = make_member(unit=meeting_ctx["unit"])
    reviewer = make_member(unit=meeting_ctx["unit"])
    due_date = due_date or timezone.localdate() + timedelta(days=7)
    planned_start = min(timezone.localdate(), due_date - timedelta(days=1))
    action = ResolutionAction.objects.create(
        resolution=resolution,
        action_number=action_number,
        title=f"Action {action_number}",
        accountable_unit=meeting_ctx["unit"],
        accountable_user=accountable,
        responsible_user=responsible,
        reviewer=reviewer,
        approver=reviewer,
        planned_start=planned_start,
        original_due_date=due_date,
        current_due_date=due_date,
    )
    return {
        **meeting_ctx,
        "decision": decision,
        "resolution": resolution,
        "action": action,
        "accountable": accountable,
        "responsible": responsible,
        "reviewer": reviewer,
    }
