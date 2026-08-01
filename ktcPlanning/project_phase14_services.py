import hashlib
import json
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from opc.models import (
    MRPRecommendation,
    NonconformanceRecord,
    OperationExecution,
    PlanningDemand,
    ProductionOrder,
    PurchaseRequisition,
)
from opc.mrp_services import create_demand

from .models import (
    Dependency,
    Project,
    ProjectBaselineSnapshot,
    ProjectDeliverable,
    ProjectDownstreamLink,
    ProjectForecastSnapshot,
    ProjectImpactEvent,
    ProjectMakeBuyDecision,
    ProjectManufacturingRequirement,
    ProjectMilestone,
    ProjectProcurementRequirement,
    ProjectProgressSnapshot,
    Revision,
    TaskActual,
    TaskScheduleMetrics,
    TaskVersion,
    WBSNodeVersion,
)


class ProjectPhase14Conflict(ValidationError):
    def __init__(self, code, message, *, expected_version=None, current_version=None, object_id=None):
        self.payload = {
            'code': code,
            'detail': message,
            'expected_version': expected_version,
            'current_version': current_version,
            'object_id': str(object_id) if object_id else None,
        }
        super().__init__(self.payload)


def decimal_value(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.000001'))


def checksum(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def iso(value):
    return value.isoformat() if value else None


def next_number(model, field, prefix):
    latest = model.objects.filter(**{f'{field}__startswith': prefix}).aggregate(Max(field))[f'{field}__max']
    sequence = int(str(latest).split('-')[-1]) + 1 if latest else 1
    return f'{prefix}{sequence:06d}'


def check_version(obj, field, expected):
    if expected is None:
        return
    current = getattr(obj, field)
    if int(expected) != int(current):
        raise ProjectPhase14Conflict(f'{field}_conflict', 'Project version conflict.', expected_version=expected, current_version=current, object_id=obj.pk)


def project_baseline_payload(project: Project, revision: Revision):
    task_versions = list(TaskVersion.objects.filter(revision=revision, is_deleted=False).select_related('task', 'wbs_node').order_by('sequence', 'id'))
    wbs_versions = list(WBSNodeVersion.objects.filter(revision=revision, is_deleted=False).order_by('tree_id', 'lft'))
    dependencies = list(Dependency.objects.filter(revision=revision).order_by('predecessor_id', 'successor_id'))
    return {
        'project': {
            'id': str(project.pk),
            'number': project.project_number,
            'code': project.project_code,
            'name': project.name,
            'planned_start': iso(project.start_date),
            'planned_end': iso(project.end_date),
            'project_version': project.project_version,
        },
        'revision': {
            'id': revision.pk,
            'number': revision.number,
            'is_baseline': revision.is_baseline,
            'approved_at': iso(revision.approved_at),
        },
        'wbs': [
            {'node': str(row.node_id), 'version': row.pk, 'parent': row.parent_id, 'code': row.wbs_code, 'title': row.title, 'start': iso(row.planned_start), 'finish': iso(row.planned_finish), 'sequence': row.sequence}
            for row in wbs_versions
        ],
        'tasks': [
            {'task': str(row.task_id), 'version': row.pk, 'wbs': row.wbs_node_id, 'title': row.title, 'start': iso(row.planned_start), 'finish': iso(row.planned_finish), 'duration_hours': str(row.duration_hours), 'weight': str(row.weight), 'sequence': row.sequence}
            for row in task_versions
        ],
        'dependencies': [
            {'id': row.pk, 'predecessor': str(row.predecessor_id), 'successor': str(row.successor_id), 'type': row.dependency_type, 'lag_hours': row.lag_hours}
            for row in dependencies
        ],
    }


@transaction.atomic
def approve_project_baseline(*, actor, project: Project, revision: Revision, expected_project_version=None, change_summary=''):
    project = Project.objects.select_for_update().get(pk=project.pk)
    revision = Revision.objects.select_for_update().get(pk=revision.pk)
    check_version(project, 'project_version', expected_project_version)
    if revision.project_id != project.pk:
        raise ValidationError({'revision': 'Baseline revision must belong to the project.'})
    if revision.approved_at is None:
        revision.approved_at = timezone.now()
        revision.approved_by = actor
        revision.is_baseline = True
        revision.save(update_fields=['approved_at', 'approved_by', 'is_baseline'])
    active = ProjectBaselineSnapshot.objects.select_for_update().filter(project=project, status=ProjectBaselineSnapshot.STATUS_APPROVED).first()
    payload = project_baseline_payload(project, revision)
    snapshot = ProjectBaselineSnapshot.objects.create(
        project=project,
        revision=revision,
        baseline_number=next_number(ProjectBaselineSnapshot, 'baseline_number', f'PB-{timezone.now():%Y}-'),
        revision_number=(active.revision_number + 1) if active else 1,
        status=ProjectBaselineSnapshot.STATUS_APPROVED,
        snapshot=payload,
        checksum=checksum(payload),
        change_summary=change_summary,
        approved_by=actor,
        approved_at=timezone.now(),
    )
    if active:
        active.status = ProjectBaselineSnapshot.STATUS_SUPERSEDED
        active.superseded_by = snapshot
        active.save(update_fields=['status', 'superseded_by'])
    project.active_baseline_revision = revision
    project.lifecycle_status = Project.LIFECYCLE_ACTIVE
    project.project_version += 1
    project.save(update_fields=['active_baseline_revision', 'lifecycle_status', 'project_version'])
    return snapshot


@transaction.atomic
def approve_manufacturing_requirement(requirement: ProjectManufacturingRequirement, *, actor, expected_version=None):
    req = ProjectManufacturingRequirement.objects.select_for_update().get(pk=requirement.pk)
    check_version(req, 'requirement_version', expected_version)
    if req.status != ProjectManufacturingRequirement.STATUS_DRAFT:
        raise ValidationError({'status': 'Only draft manufacturing requirements can be approved.'})
    req.status = ProjectManufacturingRequirement.STATUS_APPROVED
    req.approved_by = actor
    req.approved_at = timezone.now()
    req.requirement_version += 1
    req.save(update_fields=['status', 'approved_by', 'approved_at', 'requirement_version', 'updated_at'])
    return req


@transaction.atomic
def approve_procurement_requirement(requirement: ProjectProcurementRequirement, *, actor, expected_version=None):
    req = ProjectProcurementRequirement.objects.select_for_update().get(pk=requirement.pk)
    check_version(req, 'requirement_version', expected_version)
    if req.status != ProjectProcurementRequirement.STATUS_DRAFT:
        raise ValidationError({'status': 'Only draft procurement requirements can be approved.'})
    req.status = ProjectProcurementRequirement.STATUS_APPROVED
    req.approved_by = actor
    req.approved_at = timezone.now()
    req.requirement_version += 1
    req.save(update_fields=['status', 'approved_by', 'approved_at', 'requirement_version', 'updated_at'])
    return req


@transaction.atomic
def convert_manufacturing_requirement_to_demand(requirement: ProjectManufacturingRequirement, *, actor, expected_version=None, idempotency_key=''):
    req = ProjectManufacturingRequirement.objects.select_for_update(of=('self',)).select_related('project', 'deliverable').get(pk=requirement.pk)
    check_version(req, 'requirement_version', expected_version)
    if req.converted_demand_id:
        return req.converted_demand
    if req.status != ProjectManufacturingRequirement.STATUS_APPROVED:
        raise ValidationError({'status': 'Only approved manufacturing requirements can create PlanningDemand.'})
    if idempotency_key:
        existing = ProjectManufacturingRequirement.objects.filter(idempotency_key=idempotency_key, converted_demand__isnull=False).exclude(pk=req.pk).first()
        if existing:
            return existing.converted_demand
    demand = create_demand(
        actor=actor,
        demand_type=PlanningDemand.TYPE_PRODUCTION_REQUIREMENT,
        item_revision=req.item_revision,
        warehouse=req.warehouse,
        plant=req.plant,
        quantity=req.quantity,
        unit=req.unit,
        required_date=req.required_date,
        priority=req.priority,
        source_reference=req.requirement_number,
        description=f'Project {req.project.project_number or req.project_id} manufacturing requirement {req.requirement_number}',
    )
    demand = PlanningDemand.objects.select_for_update().get(pk=demand.pk)
    demand.status = PlanningDemand.STATUS_APPROVED
    demand.approved_by = actor
    demand.approved_at = timezone.now()
    demand.demand_version += 1
    demand.save(update_fields=['status', 'approved_by', 'approved_at', 'demand_version', 'updated_at'])
    req.status = ProjectManufacturingRequirement.STATUS_CONVERTED
    req.converted_demand = demand
    req.idempotency_key = idempotency_key or req.idempotency_key
    req.requirement_version += 1
    req.save(update_fields=['status', 'converted_demand', 'idempotency_key', 'requirement_version', 'updated_at'])
    ProjectDownstreamLink.objects.get_or_create(
        project=req.project,
        link_type=ProjectDownstreamLink.TYPE_PLANNING_DEMAND,
        object_id=str(demand.pk),
        defaults={'activity': req.activity, 'deliverable': req.deliverable, 'manufacturing_requirement': req, 'object_number': demand.demand_number, 'quantity': demand.quantity, 'unit': demand.unit, 'status_snapshot': demand.status},
    )
    return demand


@transaction.atomic
def convert_procurement_requirement_to_requisition(requirement: ProjectProcurementRequirement, *, actor, expected_version=None, idempotency_key=''):
    req = ProjectProcurementRequirement.objects.select_for_update(of=('self',)).select_related('project', 'deliverable').get(pk=requirement.pk)
    check_version(req, 'requirement_version', expected_version)
    if req.converted_requisition_id:
        return req.converted_requisition
    if req.status != ProjectProcurementRequirement.STATUS_APPROVED:
        raise ValidationError({'status': 'Only approved procurement requirements can create draft PurchaseRequisition.'})
    if req.requirement_type != ProjectProcurementRequirement.TYPE_ITEM:
        raise ValidationError({'requirement_type': 'Service procurement is retained as a project requirement; purchase execution is out of scope.'})
    if idempotency_key:
        existing = ProjectProcurementRequirement.objects.filter(idempotency_key=idempotency_key, converted_requisition__isnull=False).exclude(pk=req.pk).first()
        if existing:
            return existing.converted_requisition
    requisition = PurchaseRequisition.objects.create(
        requisition_number=next_number(PurchaseRequisition, 'requisition_number', f'PR-{timezone.now():%Y}-'),
        item_revision=req.item_revision,
        quantity=req.quantity,
        unit=req.unit,
        required_date=req.required_date,
        warehouse=req.warehouse,
        status=PurchaseRequisition.STATUS_DRAFT,
        created_by=actor,
    )
    req.status = ProjectProcurementRequirement.STATUS_CONVERTED
    req.converted_requisition = requisition
    req.idempotency_key = idempotency_key or req.idempotency_key
    req.requirement_version += 1
    req.save(update_fields=['status', 'converted_requisition', 'idempotency_key', 'requirement_version', 'updated_at'])
    ProjectDownstreamLink.objects.get_or_create(
        project=req.project,
        link_type=ProjectDownstreamLink.TYPE_PURCHASE_REQUISITION,
        object_id=str(requisition.pk),
        defaults={'activity': req.activity, 'deliverable': req.deliverable, 'procurement_requirement': req, 'object_number': requisition.requisition_number, 'quantity': requisition.quantity, 'unit': requisition.unit, 'status_snapshot': requisition.status},
    )
    return requisition


@transaction.atomic
def approve_make_buy_decision(decision: ProjectMakeBuyDecision, *, actor):
    row = ProjectMakeBuyDecision.objects.select_for_update().get(pk=decision.pk)
    if row.status != ProjectMakeBuyDecision.STATUS_DRAFT:
        raise ValidationError({'status': 'Only draft make/buy decisions can be approved.'})
    ProjectMakeBuyDecision.objects.filter(project=row.project, status=ProjectMakeBuyDecision.STATUS_APPROVED).exclude(pk=row.pk).update(status=ProjectMakeBuyDecision.STATUS_SUPERSEDED, superseded_by=row)
    row.status = ProjectMakeBuyDecision.STATUS_APPROVED
    row.approved_by = actor
    row.approved_at = timezone.now()
    row.save(update_fields=['status', 'approved_by', 'approved_at'])
    return row


def project_rollup_payload(project: Project):
    tasks = TaskVersion.objects.filter(revision=project.current_execution_revision, is_deleted=False).select_related('actual') if project.current_execution_revision_id else TaskVersion.objects.none()
    task_count = tasks.count()
    progress_sum = Decimal('0')
    weight_sum = Decimal('0')
    for task in tasks:
        weight = Decimal(task.weight or 0) or Decimal('1')
        actual = getattr(task, 'actual', None)
        progress_sum += Decimal(actual.progress if actual else 0) * weight
        weight_sum += weight
    task_progress = decimal_value(progress_sum / weight_sum if weight_sum else 0)
    deliverables = ProjectDeliverable.objects.filter(project=project)
    accepted_deliverables = deliverables.filter(acceptance_status=ProjectDeliverable.STATUS_ACCEPTED).count()
    deliverable_progress = decimal_value((accepted_deliverables / max(1, deliverables.count())) * 100)
    manufacturing = ProjectManufacturingRequirement.objects.filter(project=project)
    converted_make = manufacturing.filter(status=ProjectManufacturingRequirement.STATUS_CONVERTED).count()
    procurement = ProjectProcurementRequirement.objects.filter(project=project)
    converted_buy = procurement.filter(status=ProjectProcurementRequirement.STATUS_CONVERTED).count()
    quality_blockers = NonconformanceRecord.objects.filter(status__in=['OPEN', 'DISPOSITION_PROPOSED']).count()
    maintenance_impacts = ProjectImpactEvent.objects.filter(project=project, domain=ProjectImpactEvent.DOMAIN_MAINTENANCE, active=True).count()
    return {
        'task_count': task_count,
        'task_progress_percent': str(task_progress),
        'deliverable_progress_percent': str(deliverable_progress),
        'engineering_percent': str(deliverable_progress),
        'manufacturing_percent': str(decimal_value((converted_make / max(1, manufacturing.count())) * 100)),
        'procurement_percent': str(decimal_value((converted_buy / max(1, procurement.count())) * 100)),
        'quality_blockers': quality_blockers,
        'maintenance_impact_count': maintenance_impacts,
        'open_impacts': ProjectImpactEvent.objects.filter(project=project, active=True).count(),
    }


@transaction.atomic
def create_progress_snapshot(*, actor, project: Project, data_date=None):
    project = Project.objects.select_for_update().get(pk=project.pk)
    payload = project_rollup_payload(project)
    snap = ProjectProgressSnapshot.objects.create(
        project=project,
        snapshot_number=next_number(ProjectProgressSnapshot, 'snapshot_number', f'PPS-{timezone.now():%Y}-'),
        data_date=data_date or timezone.now(),
        progress_percent=decimal_value(payload['task_progress_percent']),
        engineering_percent=decimal_value(payload['engineering_percent']),
        manufacturing_percent=decimal_value(payload['manufacturing_percent']),
        procurement_percent=decimal_value(payload['procurement_percent']),
        quality_percent=Decimal('0') if payload['quality_blockers'] else Decimal('100'),
        maintenance_impact_count=payload['maintenance_impact_count'],
        snapshot=payload,
        checksum=checksum(payload),
        created_by=actor,
    )
    return snap


@transaction.atomic
def create_forecast_snapshot(*, actor, project: Project, data_date=None):
    project = Project.objects.select_for_update().get(pk=project.pk)
    baseline_finish = project.active_baseline_revision.project_end if project.active_baseline_revision_id else None
    current_finish = project.current_execution_revision.project_end if project.current_execution_revision_id else project.end_date
    task_metrics = TaskScheduleMetrics.objects.filter(task_version__revision=project.current_execution_revision, is_critical=True).select_related('task_version') if project.current_execution_revision_id else []
    critical_path = [str(metric.task_version.task_id) for metric in task_metrics]
    risks = []
    for milestone in ProjectMilestone.objects.filter(project=project):
        if milestone.forecast_date and milestone.current_planned_date and milestone.forecast_date > milestone.current_planned_date:
            risks.append({'milestone': milestone.milestone_code, 'name': milestone.name, 'variance_days': (milestone.forecast_date - milestone.current_planned_date).days})
            ProjectMilestone.objects.filter(pk=milestone.pk).update(status=ProjectMilestone.STATUS_AT_RISK)
    active_impacts = list(ProjectImpactEvent.objects.filter(project=project, active=True).values('domain', 'severity', 'impact_type', 'title', 'blocking'))
    forecast_finish = current_finish or baseline_finish
    payload = {'critical_path': critical_path, 'milestone_risks': risks, 'active_impacts': active_impacts}
    snap = ProjectForecastSnapshot.objects.create(
        project=project,
        snapshot_number=next_number(ProjectForecastSnapshot, 'snapshot_number', f'PFS-{timezone.now():%Y}-'),
        data_date=data_date or timezone.now(),
        baseline_finish=baseline_finish,
        current_plan_finish=current_finish,
        forecast_finish=forecast_finish,
        actual_finish=project.end_date if project.lifecycle_status == Project.LIFECYCLE_COMPLETED else None,
        critical_path=critical_path,
        milestone_risks=risks,
        inputs=payload,
        checksum=checksum(payload),
        created_by=actor,
    )
    project.forecast_completion_date = forecast_finish
    project.project_version += 1
    project.save(update_fields=['forecast_completion_date', 'project_version'])
    return snap


def project_dashboard(project: Project):
    latest_progress = project.progress_snapshots.first()
    latest_forecast = project.forecast_snapshots.first()
    return {
        'project': {'id': str(project.pk), 'number': project.project_number, 'name': project.name, 'status': project.lifecycle_status, 'version': project.project_version},
        'rollup': project_rollup_payload(project),
        'latest_progress': latest_progress.snapshot if latest_progress else None,
        'latest_forecast': latest_forecast.inputs if latest_forecast else None,
        'baseline_count': project.phase14_baselines.count(),
        'open_impacts': project.impact_events.filter(active=True).count(),
        'manufacturing_requirements': project.manufacturing_requirements.count(),
        'procurement_requirements': project.procurement_requirements.count(),
        'downstream_links': project.downstream_links.count(),
        'mrp_recommendations': MRPRecommendation.objects.filter(source_requirement__demand_snapshot__source_demand__source_reference__in=project.manufacturing_requirements.values('requirement_number')).count(),
        'production_orders': project.downstream_links.filter(link_type=ProjectDownstreamLink.TYPE_PRODUCTION_ORDER).count(),
        'operation_executions': OperationExecution.objects.filter(production_order__inventory_transactions__isnull=False).distinct().count(),
    }
