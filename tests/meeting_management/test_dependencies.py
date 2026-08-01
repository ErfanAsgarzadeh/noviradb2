import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from meeting_management.models import DependencyType
from meeting_management.services import add_dependency
from tests.meeting_management.utils import make_action


@pytest.mark.django_db
def test_action_dependency_cycle_is_rejected():
    first = make_action(action_number=1)["action"]
    second = make_action(action_number=2, resolution=first.resolution)["action"]
    third = make_action(action_number=3, resolution=first.resolution)["action"]

    add_dependency(predecessor=first, successor=second, dependency_type=DependencyType.FINISH_TO_START)
    add_dependency(predecessor=second, successor=third, dependency_type=DependencyType.APPROVAL_REQUIRED)

    with pytest.raises(ValidationError):
        add_dependency(predecessor=third, successor=first, dependency_type=DependencyType.INFORMATION_REQUIRED)


@pytest.mark.django_db
def test_action_self_dependency_is_rejected():
    action = make_action()["action"]

    with pytest.raises(ValidationError):
        add_dependency(predecessor=action, successor=action, dependency_type=DependencyType.FINISH_TO_START)


@pytest.mark.django_db
def test_dependency_api_create_returns_created_dependency():
    first = make_action(action_number=1)["action"]
    second = make_action(action_number=2, resolution=first.resolution)["action"]
    client = APIClient()
    client.force_authenticate(first.resolution.meeting.organizer)

    response = client.post(
        reverse("meeting-action-dependency-list"),
        {
            "predecessor": first.pk,
            "successor": second.pk,
            "dependency_type": DependencyType.FINISH_TO_START,
            "description": "Finish before launch.",
        },
    )

    assert response.status_code == 201
    assert response.data["predecessor"] == first.pk
    assert response.data["successor"] == second.pk
