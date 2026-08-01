from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from meeting_management.models import ActionStatus
from tests.meeting_management.utils import make_action


@pytest.mark.django_db
def test_individual_report_counts_overdue_and_blocked_actions():
    ctx = make_action(due_date=timezone.localdate() - timedelta(days=2))
    ctx["action"].status = ActionStatus.BLOCKED
    ctx["action"].blocked_reason = "Dependency"
    ctx["action"].save()
    client = APIClient()
    client.force_authenticate(ctx["responsible"])

    response = client.get(reverse("meeting-report-individual"))

    assert response.status_code == 200
    assert response.data["active_commitments"] == 1
    assert response.data["overdue_actions"] == 1
    assert response.data["blocked_action_ratio"] == 100


@pytest.mark.django_db
def test_unit_report_member_workload_is_source_backed():
    ctx = make_action()
    client = APIClient()
    client.force_authenticate(ctx["accountable"])

    response = client.get(reverse("meeting-report-unit"), {"unit": ctx["unit"].id})

    assert response.status_code == 200
    assert response.data["active_commitments"] == 1
    assert response.data["member_workload"][0]["count"] == 1
