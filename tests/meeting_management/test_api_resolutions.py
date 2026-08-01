import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from meeting_management.models import ActionStatus, CompletionSubmissionStatus
from tests.meeting_management.utils import make_action
from tests.factories import make_member


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


@pytest.mark.django_db
def test_create_unit_assigned_action_links_project_task_and_task_reports():
    ctx = make_action()
    unit = ctx["unit"]
    manager = make_member(unit=unit)
    unit.manager = manager
    unit.save()
    owner = make_member(unit=unit)
    ctx["resolution"].owner = owner
    ctx["resolution"].save()

    client = APIClient()
    client.force_authenticate(ctx["organizer"])
    response = client.post(
        reverse("meeting-action-list"),
        {
            "resolution": ctx["resolution"].pk,
            "action_number": 2,
            "title": "Prepare recovery plan",
            "description": "Build a recovery plan for the delayed package.",
            "assignment_target_type": "unit",
            "target_unit": unit.pk,
            "original_due_date": ctx["action"].current_due_date,
            "current_due_date": ctx["action"].current_due_date,
            "completion_criteria": "Plan submitted",
            "create_project_task": True,
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["responsible_user"] == manager.pk
    assert response.data["reviewer"] == manager.pk
    assert response.data["task"]

    from ktcPlanning.models import TaskReportLog, TaskRole

    assert not TaskRole.objects.filter(task_id=response.data["task"], user=manager, role="executor").exists()
    assert TaskRole.objects.filter(task_id=response.data["task"], user=manager, role="reviewer").exists()
    TaskReportLog.objects.create(task_id=response.data["task"], user=manager, progress_percent=35, notes="Started")

    reports = client.get(reverse("meeting-action-task-reports", kwargs={"pk": response.data["id"]}))
    assert reports.status_code == 200
    assert reports.data[0]["progress_percent"] == 35
