from decimal import Decimal
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ktcPlanning.models import TaskActual, TaskVersion
from .factories import make_company_admin, make_org_unit, make_project, make_revision, make_task


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_create_draft_preserves_task_actual_progress():
    owner_unit = make_org_unit()
    admin = make_company_admin(unit=owner_unit)
    owner_unit.manager = admin
    owner_unit.save(update_fields=["manager"])
    project = make_project(creator=admin, name="Turbine Disassembly , Inspection , and Troubleshooting")
    base_revision = make_revision(project, creator=admin, approved=True, is_baseline=True)
    task, task_version = make_task(project, base_revision, title="Inspect turbine casing")
    actual_start = timezone.now() - timedelta(hours=3)
    actual_finish = timezone.now() - timedelta(hours=1)
    TaskActual.objects.create(
        task_version=task_version,
        actual_start=actual_start,
        actual_finish=actual_finish,
        progress=Decimal("45.00"),
        updated_by=admin,
    )
    project.working_revision = None
    project.save(update_fields=["working_revision"])

    response = api(admin).post(
        f"/api/planning/revisions/{base_revision.pk}/create-draft/",
        {"description": "Next planning version"},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    new_task_version = TaskVersion.objects.get(revision_id=response.data["id"], task=task)
    new_actual = TaskActual.objects.get(task_version=new_task_version)
    assert new_actual.progress == Decimal("45.00")
    assert new_actual.actual_start == actual_start
    assert new_actual.actual_finish == actual_finish

    gantt_response = api(admin).get(
        f"/api/planning/revisions/{response.data['id']}/gantt-data/"
    )
    assert gantt_response.status_code == status.HTTP_200_OK, gantt_response.data
    copied_task = next(
        node for node in gantt_response.data["nodes"] if str(node["id"]) == str(task.id)
    )
    assert copied_task["progress"] == 45.0
    assert copied_task["actual"]["progress"] == 45.0
