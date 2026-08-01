import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from meeting_management.models import MeetingDecision, MeetingMinutesVersion, MeetingStatus, MinuteVersionStatus, Resolution
from tests.meeting_management.utils import make_meeting


@pytest.mark.django_db
def test_meeting_list_filters_to_authorized_user_and_supports_search():
    ctx = make_meeting()
    other = make_meeting()
    client = APIClient()
    client.force_authenticate(ctx["organizer"])

    response = client.get(reverse("meeting-list"), {"search": ctx["meeting"].title})

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert str(ctx["meeting"].id) in ids
    assert str(other["meeting"].id) not in ids


@pytest.mark.django_db
def test_meeting_schedule_endpoint_uses_transition_service():
    ctx = make_meeting()
    client = APIClient()
    client.force_authenticate(ctx["organizer"])

    response = client.post(reverse("meeting-schedule", kwargs={"pk": ctx["meeting"].pk}))

    assert response.status_code == 200
    ctx["meeting"].refresh_from_db()
    assert ctx["meeting"].status == MeetingStatus.SCHEDULED


@pytest.mark.django_db
def test_minute_version_submit_endpoint_uses_transition_service():
    ctx = make_meeting()
    ctx["meeting"].status = MeetingStatus.MINUTES_DRAFTING
    ctx["meeting"].save(update_fields=["status"])
    minute = MeetingMinutesVersion.objects.create(
        meeting=ctx["meeting"],
        version_number=1,
        title="Draft minutes",
        body="Minutes body",
        author=ctx["organizer"],
    )
    client = APIClient()
    client.force_authenticate(ctx["organizer"])

    response = client.post(reverse("meeting-minute-version-submit", kwargs={"pk": minute.pk}))

    assert response.status_code == 200
    minute.refresh_from_db()
    ctx["meeting"].refresh_from_db()
    assert minute.status == MinuteVersionStatus.SUBMITTED
    assert ctx["meeting"].status == MeetingStatus.IN_REVIEW


@pytest.mark.django_db
def test_unrelated_user_gets_404_for_meeting_detail():
    ctx = make_meeting()
    unrelated = make_meeting()["organizer"]
    client = APIClient()
    client.force_authenticate(unrelated)

    response = client.get(reverse("meeting-detail", kwargs={"pk": ctx["meeting"].pk}))

    assert response.status_code == 404


@pytest.mark.django_db
def test_meeting_record_is_locked_after_minutes_submission():
    ctx = make_meeting()
    meeting = ctx["meeting"]
    meeting.status = MeetingStatus.MINUTES_DRAFTING
    meeting.save(update_fields=["status"])
    minute = MeetingMinutesVersion.objects.create(
        meeting=meeting,
        version_number=1,
        title="Draft minutes",
        body="Minutes body",
        author=ctx["organizer"],
    )
    decision = MeetingDecision.objects.create(meeting=meeting, decision_number=1, title="Decision")
    resolution = Resolution.objects.create(meeting=meeting, decision=decision, resolution_number=1, title="Resolution")
    client = APIClient()
    client.force_authenticate(ctx["organizer"])

    submitted = client.post(reverse("meeting-minute-version-submit", kwargs={"pk": minute.pk}))
    assert submitted.status_code == 200
    meeting.refresh_from_db()
    assert meeting.status == MeetingStatus.IN_REVIEW

    agenda_response = client.post(
        reverse("meeting-agenda-item-list"),
        {"meeting": meeting.pk, "sequence": 1, "title": "Late agenda", "planned_duration_minutes": 10},
        format="json",
    )
    minute_response = client.post(
        reverse("meeting-minute-version-list"),
        {"meeting": meeting.pk, "version_number": 2, "title": "Edited", "body": "Edited"},
        format="json",
    )
    action_response = client.post(
        reverse("meeting-action-list"),
        {
            "resolution": resolution.pk,
            "action_number": 1,
            "title": "Late action",
            "accountable_unit": ctx["unit"].pk,
            "accountable_user": ctx["organizer"].pk,
            "responsible_user": ctx["organizer"].pk,
            "original_due_date": "2026-08-10",
            "current_due_date": "2026-08-10",
        },
        format="json",
    )

    assert agenda_response.status_code == 400
    assert minute_response.status_code == 400
    assert action_response.status_code == 400
