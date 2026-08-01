from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from meeting_management.models import (
    AttendanceStatus,
    Committee,
    CommitteeMembership,
    ConfidentialityLevel,
    Meeting,
    MeetingAgendaItem,
    MeetingMinutesVersion,
    MeetingParticipant,
    MeetingStatus,
    MeetingType,
    MinuteVersionStatus,
)
from tests.factories import make_member, make_org_unit


@pytest.fixture
def meeting_context():
    unit = make_org_unit(name="Engineering PMO")
    organizer = make_member(unit=unit)
    chair = make_member(unit=unit)
    secretary = make_member(unit=unit)
    meeting_type = MeetingType.objects.create(code="board", name="Board meeting")
    meeting = Meeting.objects.create(
        title="Quarterly operating review",
        meeting_type=meeting_type,
        owning_unit=unit,
        organizer=organizer,
        chairperson=chair,
        secretary=secretary,
        planned_start=timezone.now() + timedelta(days=1),
        planned_end=timezone.now() + timedelta(days=1, hours=2),
    )
    return {
        "unit": unit,
        "organizer": organizer,
        "chair": chair,
        "secretary": secretary,
        "meeting_type": meeting_type,
        "meeting": meeting,
    }


@pytest.mark.django_db
def test_meeting_type_normalizes_code_and_defaults_confidentiality():
    meeting_type = MeetingType.objects.create(code=" ops ", name="Operations")

    assert meeting_type.code == "OPS"
    assert meeting_type.default_confidentiality == ConfidentialityLevel.INTERNAL
    assert meeting_type.requires_minutes_approval is True


@pytest.mark.django_db
def test_committee_membership_allows_only_one_active_user_membership():
    unit = make_org_unit(name="Secretariat")
    user = make_member(unit=unit)
    committee = Committee.objects.create(code="exec", name="Executive committee", owning_unit=unit)

    CommitteeMembership.objects.create(committee=committee, user=user, unit=unit)

    with pytest.raises(IntegrityError):
        CommitteeMembership.objects.create(committee=committee, user=user, unit=unit)


@pytest.mark.django_db
def test_meeting_validates_planned_and_actual_date_order(meeting_context):
    meeting = meeting_context["meeting"]
    meeting.planned_end = meeting.planned_start

    with pytest.raises(ValidationError):
        meeting.full_clean()

    meeting.planned_end = meeting.planned_start + timedelta(hours=1)
    meeting.actual_start = timezone.now()
    meeting.actual_end = meeting.actual_start - timedelta(minutes=1)

    with pytest.raises(ValidationError):
        meeting.full_clean()


@pytest.mark.django_db
def test_cancelled_meeting_requires_reason(meeting_context):
    meeting = meeting_context["meeting"]
    meeting.status = MeetingStatus.CANCELLED

    with pytest.raises(ValidationError):
        meeting.full_clean()

    meeting.cancellation_reason = "Duplicate meeting"
    meeting.full_clean()


@pytest.mark.django_db
def test_meeting_generates_unique_number(meeting_context):
    meeting = meeting_context["meeting"]

    assert meeting.meeting_number.startswith("MTG-")
    assert meeting.meeting_number.endswith("000001")


@pytest.mark.django_db
def test_agenda_sequence_is_unique_per_meeting(meeting_context):
    meeting = meeting_context["meeting"]
    MeetingAgendaItem.objects.create(meeting=meeting, sequence=1, title="Safety")

    with pytest.raises(IntegrityError):
        MeetingAgendaItem.objects.create(meeting=meeting, sequence=1, title="Finance")


@pytest.mark.django_db
def test_participant_requires_user_or_external_name_and_snapshots_unit(meeting_context):
    meeting = meeting_context["meeting"]
    participant = MeetingParticipant(meeting=meeting, attendance_status=AttendanceStatus.PRESENT)

    with pytest.raises(ValidationError):
        participant.full_clean()

    participant.external_name = "External auditor"
    participant.represented_unit = meeting_context["unit"]
    participant.save()

    assert participant.represented_unit_name == "Engineering PMO"


@pytest.mark.django_db
def test_only_one_current_minutes_version_per_meeting(meeting_context):
    meeting = meeting_context["meeting"]
    author = meeting_context["secretary"]
    MeetingMinutesVersion.objects.create(meeting=meeting, version_number=1, author=author, is_current=True)

    with pytest.raises(IntegrityError):
        MeetingMinutesVersion.objects.create(meeting=meeting, version_number=2, author=author, is_current=True)


@pytest.mark.django_db
def test_approved_minutes_are_locked_and_immutable(meeting_context):
    meeting = meeting_context["meeting"]
    author = meeting_context["secretary"]
    minute = MeetingMinutesVersion.objects.create(
        meeting=meeting,
        version_number=1,
        author=author,
        approved_by=meeting_context["chair"],
        approved_at=timezone.now(),
        status=MinuteVersionStatus.APPROVED,
        body="Approved text",
    )

    assert minute.locked_at is not None

    minute.body = "Edited text"
    with pytest.raises(ValidationError):
        minute.save()
