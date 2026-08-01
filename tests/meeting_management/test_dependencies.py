import pytest
from django.core.exceptions import ValidationError

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
