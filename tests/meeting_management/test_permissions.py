import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from tests.factories import make_company_admin
from tests.meeting_management.utils import make_action, make_meeting


@pytest.mark.django_db
def test_company_admin_can_see_all_meetings():
    first = make_meeting()
    second = make_meeting()
    admin = make_company_admin()
    client = APIClient()
    client.force_authenticate(admin)

    response = client.get(reverse("meeting-list"))

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert str(first["meeting"].id) in ids
    assert str(second["meeting"].id) in ids


@pytest.mark.django_db
def test_dashboard_aggregate_excludes_unauthorized_actions():
    visible = make_action()
    hidden = make_action(action_number=5)
    client = APIClient()
    client.force_authenticate(visible["responsible"])

    response = client.get(reverse("meeting-report-individual"))

    assert response.status_code == 200
    assert response.data["active_commitments"] == 1
