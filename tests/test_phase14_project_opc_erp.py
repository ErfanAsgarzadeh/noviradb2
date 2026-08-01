from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Thread

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from ktcPlanning.models import (
    Dependency,
    Program,
    Project,
    ProjectBaselineSnapshot,
    ProjectDeliverable,
    ProjectDownstreamLink,
    ProjectForecastSnapshot,
    ProjectImpactEvent,
    ProjectMakeBuyDecision,
    ProjectManufacturingRequirement,
    ProjectMember,
    ProjectMilestone,
    ProjectProcurementRequirement,
    ProjectProgressSnapshot,
    Revision,
    Task,
    TaskActual,
    TaskVersion,
)
from ktcPlanning.project_phase14_services import (
    approve_make_buy_decision,
    approve_manufacturing_requirement,
    approve_procurement_requirement,
    approve_project_baseline,
    convert_manufacturing_requirement_to_demand,
    convert_procurement_requirement_to_requisition,
    create_forecast_snapshot,
    create_progress_snapshot,
)
from opc.models import PlanningDemand, PurchaseRequisition
from tests.factories import make_company_admin
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase10_inventory import make_location
from tests.test_opc_phase7_production_orders import released_source


pytestmark = pytest.mark.django_db


def aware(value: datetime):
    return timezone.make_aware(value) if value.tzinfo is None else value


def project_fixture(code='P14'):
    admin = make_company_admin()
    program = Program.objects.create(program_code=f'PG-{code}', name='Program', owner=admin)
    project = Project.objects.create(
        program=program,
        project_code=f'PRJ-{code}',
        name=f'Project {code}',
        project_type=Project.TYPE_MANUFACTURING,
        created_by=admin,
        sponsor=admin,
        start_date=aware(datetime(2026, 10, 1, 8)),
        end_date=aware(datetime(2026, 10, 31, 17)),
    )
    project.refresh_from_db()
    revision = project.working_revision
    root = revision.wbs_versions.get(parent__isnull=True)
    task = Task.objects.create(project=project, created_by=admin)
    task_version = TaskVersion.objects.create(
        task=task,
        revision=revision,
        wbs_node=root,
        title='Manufacture pump skid',
        planned_start=aware(datetime(2026, 10, 3, 8)),
        planned_finish=aware(datetime(2026, 10, 7, 17)),
        duration_hours=Decimal('32.00'),
        weight=Decimal('10.00'),
        sequence=10,
    )
    return admin, project, revision, task, task_version


def test_program_project_wbs_dependency_baseline_immutability_and_snapshots():
    admin, project, revision, task, task_version = project_fixture('BASE')
    successor = Task.objects.create(project=project, created_by=admin)
    TaskVersion.objects.create(
        task=successor,
        revision=revision,
        wbs_node=task_version.wbs_node,
        title='Inspect skid',
        planned_start=aware(datetime(2026, 10, 8, 8)),
        planned_finish=aware(datetime(2026, 10, 8, 17)),
        duration_hours=Decimal('8.00'),
        weight=Decimal('5.00'),
        sequence=20,
    )
    Dependency.objects.create(revision=revision, predecessor=task, successor=successor, dependency_type='FS', lag_hours=8)
    ProjectMember.objects.create(project=project, user=admin, role=ProjectMember.ROLE_PROJECT_MANAGER)
    milestone = ProjectMilestone.objects.create(project=project, activity=successor, milestone_code='MS-READY', name='Ready to ship', current_planned_date=date(2026, 10, 9), forecast_date=date(2026, 10, 12))
    deliverable = ProjectDeliverable.objects.create(project=project, activity=task, deliverable_code='DEL-SKID', name='Pump skid', deliverable_type=ProjectDeliverable.TYPE_MANUFACTURED_ITEM, quantity='1.000000', unit='EA')
    deliverable.acceptance_status = ProjectDeliverable.STATUS_ACCEPTED
    deliverable.accepted_by = admin
    deliverable.accepted_at = timezone.now()
    deliverable.save()
    TaskActual.objects.create(task_version=task_version, progress=Decimal('50.00'), updated_by=admin)

    baseline = approve_project_baseline(actor=admin, project=project, revision=revision, expected_project_version=0, change_summary='Initial project baseline')
    assert baseline.status == ProjectBaselineSnapshot.STATUS_APPROVED
    assert baseline.snapshot['tasks'][0]['task'] == str(task.pk)
    assert baseline.checksum
    with pytest.raises(ValidationError):
        baseline.change_summary = 'mutated'
        baseline.save()

    progress = create_progress_snapshot(actor=admin, project=project)
    forecast = create_forecast_snapshot(actor=admin, project=project)
    milestone.refresh_from_db()
    assert progress.progress_percent == Decimal('33.333333')
    assert progress.engineering_percent == Decimal('100.000000')
    assert isinstance(forecast, ProjectForecastSnapshot)
    assert milestone.status == ProjectMilestone.STATUS_AT_RISK
    assert Project.objects.get(pk=project.pk).forecast_completion_date is not None


def test_exact_engineering_links_make_buy_demand_and_requisition_conversion():
    admin, project, _revision, task, _task_version = project_fixture('REQ')
    _admin2, diagram, _nodes, _edges, refs = released_source('P-P14-REQ')
    warehouse, _location = make_location('P14REQ')
    deliverable = ProjectDeliverable.objects.create(project=project, activity=task, deliverable_code='DEL-REQ', name='Drive assembly', deliverable_type=ProjectDeliverable.TYPE_ASSEMBLY, quantity='3.000000', unit='EA', item_revision=diagram.item_revision)
    make_req = ProjectManufacturingRequirement.objects.create(
        project=project,
        activity=task,
        deliverable=deliverable,
        requirement_number='PMR-REQ-000001',
        strategy=ProjectManufacturingRequirement.STRATEGY_MAKE,
        item_revision=diagram.item_revision,
        bom_revision=refs['mbom_revision'],
        opc_diagram=diagram,
        opc_graph_version=diagram.graph_version,
        document_revision=refs['document_revision'],
        quantity='3.000000',
        unit='EA',
        required_date=date(2026, 10, 20),
        warehouse=warehouse,
    )
    buy_req = ProjectProcurementRequirement.objects.create(project=project, activity=task, deliverable=deliverable, requirement_number='PPR-REQ-000001', item_revision=refs['component'], quantity='2.000000', unit='EA', required_date=date(2026, 10, 15), warehouse=warehouse)
    decision = ProjectMakeBuyDecision.objects.create(project=project, manufacturing_requirement=make_req, strategy=ProjectMakeBuyDecision.STRATEGY_SPLIT, make_quantity='1.000000', buy_quantity='2.000000')

    approved_make = approve_manufacturing_requirement(make_req, actor=admin, expected_version=0)
    demand = convert_manufacturing_requirement_to_demand(approved_make, actor=admin, expected_version=1, idempotency_key='p14-demand')
    assert demand.status == PlanningDemand.STATUS_APPROVED
    assert demand.source_reference == 'PMR-REQ-000001'
    assert ProjectDownstreamLink.objects.filter(project=project, link_type=ProjectDownstreamLink.TYPE_PLANNING_DEMAND, object_id=str(demand.pk)).exists()
    assert convert_manufacturing_requirement_to_demand(approved_make, actor=admin, expected_version=2, idempotency_key='p14-demand').pk == demand.pk

    approved_buy = approve_procurement_requirement(buy_req, actor=admin, expected_version=0)
    requisition = convert_procurement_requirement_to_requisition(approved_buy, actor=admin, expected_version=1, idempotency_key='p14-pr')
    assert requisition.status == PurchaseRequisition.STATUS_DRAFT
    assert ProjectDownstreamLink.objects.filter(project=project, link_type=ProjectDownstreamLink.TYPE_PURCHASE_REQUISITION, object_id=str(requisition.pk)).exists()
    approved_decision = approve_make_buy_decision(decision, actor=admin)
    assert approved_decision.status == ProjectMakeBuyDecision.STATUS_APPROVED


def test_project_api_phase14_workbench_conflict_and_impact_history():
    admin, project, revision, task, _task_version = project_fixture('API')
    client = api(admin)
    baseline = client.post(reverse('project-approve-phase14-baseline', kwargs={'pk': project.pk}), {'revision': revision.pk, 'expected_project_version': 0}, format='json')
    assert baseline.status_code == status.HTTP_200_OK, baseline.data
    stale = client.post(reverse('project-approve-phase14-baseline', kwargs={'pk': project.pk}), {'revision': revision.pk, 'expected_project_version': 0}, format='json')
    assert stale.status_code == status.HTTP_409_CONFLICT

    impact = client.post(reverse('project-impact-event-list'), {'project': str(project.pk), 'activity': str(task.pk), 'domain': 'MATERIAL', 'severity': 'CRITICAL', 'impact_type': 'MATERIAL_SHORTAGE', 'title': 'Valve shortage', 'blocking': True}, format='json')
    assert impact.status_code == status.HTTP_201_CREATED, impact.data
    resolved = client.post(reverse('project-impact-event-resolve', kwargs={'pk': impact.data['id']}), {}, format='json')
    assert resolved.status_code == status.HTTP_200_OK
    row = ProjectImpactEvent.objects.get(pk=impact.data['id'])
    with pytest.raises(ValidationError):
        row.title = 'mutated impact'
        row.save()

    progress = client.post(reverse('project-progress-snapshot', kwargs={'pk': project.pk}), {}, format='json')
    forecast = client.post(reverse('project-forecast-snapshot', kwargs={'pk': project.pk}), {}, format='json')
    dashboard = client.get(reverse('project-phase14-dashboard', kwargs={'pk': project.pk}))
    assert progress.status_code == status.HTTP_200_OK, progress.data
    assert forecast.status_code == status.HTTP_200_OK, forecast.data
    assert dashboard.status_code == status.HTTP_200_OK
    assert dashboard.data['project']['number'].startswith('PRJ-')


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_project_baseline_approval_is_single_writer():
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-lock concurrency test.')
    admin, project, revision, _task, _task_version = project_fixture('CONC')
    barrier = Barrier(2)
    results: list[str] = []

    def worker(key):
        close_old_connections()
        barrier.wait()
        try:
            approve_project_baseline(actor=admin, project=project, revision=revision, expected_project_version=0, change_summary=key)
            results.append('ok')
        except Exception:
            results.append('conflict')
        finally:
            close_old_connections()

    threads = [Thread(target=worker, args=(f'w{i}',)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count('ok') == 1
    assert ProjectBaselineSnapshot.objects.filter(project=project, status=ProjectBaselineSnapshot.STATUS_APPROVED).count() == 1
