import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from meeting_management.models import ActionStatus, CompletionSubmissionStatus
from tests.meeting_management.utils import make_action


@pytest.mark.django_db
def test_action_submit_progress_and_completion_api_flow():
    ctx = make_action()
    client = APIClient()
    client.force_authenticate(ctx["responsible"])

    progress = client.post(
        reverse("meeting-action-submit-progress", kwargs={"pk": ctx["action"].pk}),
        {"progress_percent": 40, "work_completed": "Drafted"},
        format="json",
    )
    assert progress.status_code == 201
    assert progress.data["progress_percent"] == 40

    completion = client.post(
        reverse("meeting-action-submit-completion", kwargs={"pk": ctx["action"].pk}),
        {"summary": "Complete"},
        format="json",
    )
    assert completion.status_code == 201
    ctx["action"].refresh_from_db()
    assert ctx["action"].status == ActionStatus.SUBMITTED_FOR_REVIEW


@pytest.mark.django_db
def test_completion_accept_api_closes_action_for_reviewer():
    ctx = make_action()
    client = APIClient()
    client.force_authenticate(ctx["responsible"])
    completion = client.post(
        reverse("meeting-action-submit-completion", kwargs={"pk": ctx["action"].pk}),
        {"summary": "Complete"},
        format="json",
    )

    client.force_authenticate(ctx["reviewer"])
    accepted = client.post(
        reverse("meeting-completion-submission-accept", kwargs={"pk": completion.data["id"]}),
        {"comment": "Accepted"},
        format="json",
    )

    assert accepted.status_code == 200
    assert accepted.data["status"] == CompletionSubmissionStatus.ACCEPTED
    ctx["action"].refresh_from_db()
    assert ctx["action"].status == ActionStatus.CLOSED


@pytest.mark.django_db
def test_unrelated_user_cannot_infer_action_detail():
    ctx = make_action()
    unrelated = make_action(action_number=9)["responsible"]
    client = APIClient()
    client.force_authenticate(unrelated)

    response = client.get(reverse("meeting-action-detail", kwargs={"pk": ctx["action"].pk}))

    assert response.status_code == 404
