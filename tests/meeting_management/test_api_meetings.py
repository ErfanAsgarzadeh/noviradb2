import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from meeting_management.models import MeetingStatus
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
def test_unrelated_user_gets_404_for_meeting_detail():
    ctx = make_meeting()
    unrelated = make_meeting()["organizer"]
    client = APIClient()
    client.force_authenticate(unrelated)

    response = client.get(reverse("meeting-detail", kwargs={"pk": ctx["meeting"].pk}))

    assert response.status_code == 404
