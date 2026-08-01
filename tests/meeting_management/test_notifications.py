from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from meeting_management.models import ActionStatus, MeetingNotification
from meeting_management.services import generate_action_deadline_notifications
from tests.meeting_management.utils import make_action


@pytest.mark.django_db
def test_deadline_notifications_are_idempotent():
    ctx = make_action(due_date=timezone.localdate() - timedelta(days=1))
    ctx["action"].status = ActionStatus.IN_PROGRESS
    ctx["action"].save()

    first = generate_action_deadline_notifications(today=timezone.localdate())
    second = generate_action_deadline_notifications(today=timezone.localdate())

    assert len(first) == 1
    assert len(second) == 1
    assert MeetingNotification.objects.count() == 1
    assert MeetingNotification.objects.get().notification_type == "overdue_action"


@pytest.mark.django_db
def test_notification_management_command_runs_idempotently(capsys):
    ctx = make_action(due_date=timezone.localdate() + timedelta(days=1))
    ctx["action"].status = ActionStatus.NOT_STARTED
    ctx["action"].save()

    call_command("generate_meeting_notifications")
    call_command("generate_meeting_notifications")

    assert MeetingNotification.objects.count() == 1
