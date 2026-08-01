from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status

from ktcPlanning.models import Assignment, Dependency, Project, ProjectOPCImport, TaskVersion, WBSNodeVersion
from ktcPlanning.opc_project_services import build_opc_wbs_preview, import_opc_to_wbs
from tests.factories import make_company_admin
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase6_bom_documents_change import make_phase6_ready
from tests.test_opc_phase7_production_orders import released_source


pytestmark = pytest.mark.django_db


def project_context(code='OPCIMP'):
    admin = make_company_admin()
    project = Project.objects.create(
        project_code=f'PRJ-{code}',
        name=f'Project {code}',
        project_type=Project.TYPE_MANUFACTURING,
        created_by=admin,
        sponsor=admin,
    )
    project.refresh_from_db()
    revision = project.working_revision
    root = revision.wbs_versions.get(parent__isnull=True)
    return admin, project, revision, root


def test_preview_is_dry_run_and_released_opc_import_creates_wbs_tasks_assignments_and_dependencies():
    admin, project, revision, root = project_context('SVC')
    _opc_admin, diagram, _nodes, _edges, _refs = released_source('P-OPC-WBS-SVC')

    preview = build_opc_wbs_preview(revision=revision, parent_wbs_node=root, opc_diagram=diagram, quantity=Decimal('2'))

    assert preview['taskCount'] > 0
    assert preview['dependencyCount'] >= preview['taskCount'] - 1
    assert TaskVersion.objects.filter(revision=revision).count() == 0

    imported = import_opc_to_wbs(
        revision=revision,
        parent_wbs_node=root,
        opc_diagram=diagram,
        actor=admin,
        quantity=Decimal('2'),
        expected_project_version=0,
        idempotency_key='opc-wbs-svc',
    )

    assert imported.created_wbs_node.parent_id == root.pk
    assert imported.opc_graph_version == diagram.graph_version
    assert TaskVersion.objects.filter(revision=revision, wbs_node=imported.created_wbs_node).count() == preview['taskCount']
    assert Assignment.objects.filter(revision=revision, task_id__in=[row['taskId'] for row in imported.operation_task_map.values()]).count() >= 1
    assert Dependency.objects.filter(revision=revision).count() == preview['dependencyCount']
    assert Project.objects.get(pk=project.pk).project_version == 1

    task_count = TaskVersion.objects.filter(revision=revision).count()
    replay = import_opc_to_wbs(
        revision=revision,
        parent_wbs_node=root,
        opc_diagram=diagram,
        actor=admin,
        quantity=Decimal('2'),
        expected_project_version=1,
        idempotency_key='opc-wbs-svc',
    )
    assert replay.pk == imported.pk
    assert TaskVersion.objects.filter(revision=revision).count() == task_count


def test_import_rejects_unreleased_opc():
    admin, _project, revision, root = project_context('UNREL')
    _opc_admin, diagram, _nodes, _edges, _refs = make_phase6_ready('P-OPC-WBS-UNREL', status_value='APPROVED')

    with pytest.raises(ValidationError):
        import_opc_to_wbs(
            revision=revision,
            parent_wbs_node=root,
            opc_diagram=diagram,
            actor=admin,
            idempotency_key='unreleased',
        )


def test_project_opc_import_api_preview_apply_and_conflict():
    admin, project, revision, root = project_context('API')
    _opc_admin, diagram, _nodes, _edges, _refs = released_source('P-OPC-WBS-API')
    client = api(admin)

    payload = {
        'revision': revision.pk,
        'parentWbsNodeId': str(root.node_id),
        'opcDiagramId': str(diagram.pk),
        'quantity': '1.000000',
        'expectedProjectVersion': project.project_version,
        'idempotencyKey': 'opc-wbs-api',
    }
    preview = client.post(reverse('project-opc-import-preview'), payload, format='json')
    assert preview.status_code == status.HTTP_200_OK, preview.data
    assert preview.data['taskCount'] > 0
    assert ProjectOPCImport.objects.count() == 0

    applied = client.post(reverse('project-opc-import-apply'), payload, format='json')
    assert applied.status_code == status.HTTP_201_CREATED, applied.data
    assert applied.data['createdWbsNodeId']
    assert applied.data['operation_task_map']
    assert applied.data['resource_assignment_map']

    stale_payload = {**payload, 'idempotencyKey': 'new-import', 'expectedProjectVersion': 0}
    stale = client.post(reverse('project-opc-import-apply'), stale_payload, format='json')
    assert stale.status_code == status.HTTP_400_BAD_REQUEST
