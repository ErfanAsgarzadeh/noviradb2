from os import name

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from django.http import FileResponse, Http404, HttpResponse
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.db import transaction
from django.db.models import Q, Sum, Count, Prefetch, F, OuterRef, Subquery, Exists
from django.core.exceptions import ValidationError as DjangoValidationError
from datetime import date, timedelta, datetime
from decimal import Decimal
from collections import defaultdict

from rest_framework.views import APIView

from .cpm import CPMCycleError, CPMEngine
# ط§غŒظ…ظ¾ظˆط±طھ طھظ…ط§ظ…غŒ ظ…ط¯ظ„â€Œظ‡ط§غŒ ظ…ظˆط±ط¯ ظ†غŒط§ط²
from .models import Project, Revision, WBSNodeVersion, TaskVersion, Dependency, SubprojectDependency, TaskRole, Task, WBSNode, TaskReportLog, \
    TaskActual, TaskChatMessage, Assignment, Resource, ResourcePool, ResourceRole, ResourceSkill, ResourceSkillMapping, \
    ResourceException, ResourceRate, VarianceReport, Calendar, ProjectViewer, SystemSettings, UnitOfMeasure, \
    ExpenseType, FundingSource, BudgetAllocation, BudgetBorrow, UnfundedForecastCost, CostTransaction, TaskReportAttachment, BudgetConsumption, TaskFinancialPlan, PaymentMilestone, PaymentTransaction, Currency, ExchangeRate, \
    TaskDeliveryAttachment, \
    GlobalLevelingRun, LevelingPlanProject, TaskLevelingMetrics, ResourceUsage, TaskDelivery, Program, ProjectMember, ProjectDeliverable, ProjectMilestone, ProjectBaselineSnapshot, ProjectManufacturingRequirement, ProjectProcurementRequirement, ProjectMakeBuyDecision, ProjectDownstreamLink, ProjectProgressSnapshot, ProjectForecastSnapshot, ProjectImpactEvent, ProjectOPCImport
from opc.models import OPCDiagram
from .serializers import (
    ProjectSerializer,
    RevisionSerializer,
    WbsNodeSerializer,
    ActivityNodeSerializer,
    DependencySerializer,
    SubprojectDependencySerializer,
    subproject_virtual_id,
    TaskRoleSerializer, TaskReportLogSerializer, TaskChatMessageSerializer, ResourcePoolSerializer,
    ResourceRoleSerializer, ResourceSkillSerializer, ResourceSerializer, ResourceSkillMappingSerializer,
    ResourceExceptionSerializer, ResourceRateSerializer, AssignmentSerializer, VarianceReportSerializer,
    CalendarSerializer, ProjectViewerSerializer, SystemSettingsSerializer, UnitOfMeasureSerializer,
    ExpenseTypeSerializer, FundingSourceSerializer, BudgetAllocationSerializer, BudgetBorrowSerializer, UnfundedForecastCostSerializer,
    CostTransactionSerializer, TaskDropdownSerializer, ResourceLevelingPlanSerializer, TaskFinancialPlanSerializer, PaymentMilestoneSerializer, PaymentTransactionSerializer, PlanPaymentAllocationSerializer, TaskDeliveryAttachmentSerializer, TaskDeliverySerializer,
    ProgramSerializer, ProjectMemberSerializer, ProjectDeliverableSerializer, ProjectMilestoneSerializer, ProjectBaselineSnapshotSerializer, ProjectManufacturingRequirementSerializer, ProjectProcurementRequirementSerializer, ProjectMakeBuyDecisionSerializer, ProjectDownstreamLinkSerializer, ProjectProgressSnapshotSerializer, ProjectForecastSnapshotSerializer, ProjectImpactEventSerializer, ProjectOPCImportSerializer
)


def parse_cpm_data_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.lower() == "now":
        return timezone.now()

    text = str(value).strip()
    parsed = parse_datetime(text)
    if parsed is None:
        parsed_date = parse_date(text)
        if parsed_date is None:
            raise ValueError("Invalid dataDate. Use ISO datetime, date, or 'now'.")
        parsed = datetime.combine(parsed_date, datetime.min.time())

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from ktcPlanning.validators import validate_chat_file

from .msp_importer import import_msp_xml
from .msp_exporter import export_revision_to_msp_xml
from django.db.models import Max

from .variance_engine import EVMEngine
from .financial_services import (
    activate_plan, allocate_cost_transaction_to_milestones, allocate_plan_payment,
    get_task_delivery_attachment_capabilities, get_task_delivery_capabilities, get_task_financial_status, refresh_plan_cost_allocations, register_transaction,
    approve_task_delivery, cancel_task_delivery, reject_task_delivery, submit_task_delivery,
    validate_progress_transition, validate_task_delivery, validate_task_start,
)
from .financial_control import build_financial_control_payload
from .cost_variance_snapshots import generate_cost_variance_reports
from .permissions import (
    can_create_project, can_edit_project, require_can_create_project,
    require_can_edit_project, is_company_level, is_system_admin,
    accessible_project_ids, accessible_projects, can_view_project,
    require_can_manage_viewers,
)
from auditlog.services import diff_dicts, log_event, model_to_dict_safe
from .revision_policy import (
    ROLE_EXECUTION, ROLE_FORECAST, ROLE_WORKING,
    assign_working_revision, get_official_revision,
    official_revision_ids, promote_approved_revision,
    resolve_designated_approver,
)
from .project_phase14_services import (
    ProjectPhase14Conflict,
    approve_make_buy_decision,
    approve_manufacturing_requirement,
    approve_procurement_requirement,
    approve_project_baseline,
    convert_manufacturing_requirement_to_demand,
    convert_procurement_requirement_to_requisition,
    create_forecast_snapshot,
    create_progress_snapshot,
    next_number,
    project_dashboard,
)
from .opc_project_services import build_opc_wbs_preview, import_opc_to_wbs
from django.contrib.auth import get_user_model
User = get_user_model()


def phase14_error_response(exc):
    if isinstance(exc, ProjectPhase14Conflict):
        return Response(exc.payload, status=status.HTTP_409_CONFLICT)
    if isinstance(exc, DjangoValidationError):
        return Response(exc.message_dict if hasattr(exc, 'message_dict') else {'detail': exc.messages}, status=status.HTTP_400_BAD_REQUEST)
    return Response(getattr(exc, 'detail', {'detail': str(exc)}), status=status.HTTP_400_BAD_REQUEST)


class ProgramViewSet(viewsets.ModelViewSet):
    queryset = Program.objects.select_related('owner', 'sponsor').all()
    serializer_class = ProgramSerializer
    permission_classes = [IsAuthenticated]


class ProjectMemberViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectMemberSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectMember.objects.select_related('project', 'user').filter(project_id__in=accessible_project_ids(self.request.user))


class ProjectDeliverableViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectDeliverableSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectDeliverable.objects.select_related('project', 'activity', 'item_revision__item', 'document_revision').filter(project_id__in=accessible_project_ids(self.request.user))

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        deliverable = self.get_object()
        deliverable.acceptance_status = ProjectDeliverable.STATUS_ACCEPTED
        deliverable.accepted_by = request.user
        deliverable.accepted_at = timezone.now()
        deliverable.deliverable_version += 1
        deliverable.save(update_fields=['acceptance_status', 'accepted_by', 'accepted_at', 'deliverable_version', 'updated_at'])
        return Response(self.get_serializer(deliverable).data)


class ProjectMilestoneViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectMilestoneSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectMilestone.objects.select_related('project', 'activity', 'owner').filter(project_id__in=accessible_project_ids(self.request.user))


class ProjectBaselineSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectBaselineSnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectBaselineSnapshot.objects.select_related('project', 'revision', 'approved_by').filter(project_id__in=accessible_project_ids(self.request.user))


class ProjectManufacturingRequirementViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectManufacturingRequirementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectManufacturingRequirement.objects.select_related('project', 'activity', 'deliverable', 'item_revision__item', 'bom_revision', 'opc_diagram', 'warehouse', 'plant', 'converted_demand').filter(project_id__in=accessible_project_ids(self.request.user))

    def perform_create(self, serializer):
        serializer.save(requirement_number=next_number(ProjectManufacturingRequirement, 'requirement_number', f'PMR-{timezone.now():%Y}-'))

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        try:
            req = approve_manufacturing_requirement(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'))
            return Response(self.get_serializer(req).data)
        except Exception as exc:
            return phase14_error_response(exc)

    @action(detail=True, methods=['post'])
    def convert(self, request, pk=None):
        try:
            demand = convert_manufacturing_requirement_to_demand(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''))
            return Response({'planning_demand': str(demand.pk), 'demand_number': demand.demand_number, 'status': demand.status})
        except Exception as exc:
            return phase14_error_response(exc)


class ProjectProcurementRequirementViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectProcurementRequirementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectProcurementRequirement.objects.select_related('project', 'activity', 'deliverable', 'item_revision__item', 'warehouse', 'converted_requisition').filter(project_id__in=accessible_project_ids(self.request.user))

    def perform_create(self, serializer):
        serializer.save(requirement_number=next_number(ProjectProcurementRequirement, 'requirement_number', f'PPR-{timezone.now():%Y}-'))

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        try:
            req = approve_procurement_requirement(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'))
            return Response(self.get_serializer(req).data)
        except Exception as exc:
            return phase14_error_response(exc)

    @action(detail=True, methods=['post'])
    def convert(self, request, pk=None):
        try:
            requisition = convert_procurement_requirement_to_requisition(self.get_object(), actor=request.user, expected_version=request.data.get('expected_version'), idempotency_key=request.data.get('idempotency_key', ''))
            return Response({'purchase_requisition': str(requisition.pk), 'requisition_number': requisition.requisition_number, 'status': requisition.status})
        except Exception as exc:
            return phase14_error_response(exc)


class ProjectMakeBuyDecisionViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectMakeBuyDecisionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectMakeBuyDecision.objects.select_related('project', 'manufacturing_requirement', 'procurement_requirement').filter(project_id__in=accessible_project_ids(self.request.user))

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        try:
            decision = approve_make_buy_decision(self.get_object(), actor=request.user)
            return Response(self.get_serializer(decision).data)
        except Exception as exc:
            return phase14_error_response(exc)


class ProjectDownstreamLinkViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectDownstreamLinkSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectDownstreamLink.objects.select_related('project', 'activity', 'deliverable').filter(project_id__in=accessible_project_ids(self.request.user))


class ProjectOPCImportViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectOPCImportSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectOPCImport.objects.select_related(
            'project', 'revision', 'parent_wbs_node__node', 'created_wbs_node__node',
            'opc_diagram', 'created_by',
        ).filter(project_id__in=accessible_project_ids(self.request.user))

    def _context_objects(self, request):
        revision = get_object_or_404(
            Revision.objects.select_related('project'),
            pk=request.data.get('revision') or request.data.get('revisionId'),
            project_id__in=accessible_project_ids(request.user),
        )
        parent = get_object_or_404(
            WBSNodeVersion.objects.select_related('node', 'revision'),
            node_id=request.data.get('parent_wbs_node') or request.data.get('parentWbsNodeId') or request.data.get('parentId'),
            revision=revision,
            is_deleted=False,
        )
        diagram = get_object_or_404(
            OPCDiagram.objects.prefetch_related('nodes', 'edges'),
            pk=request.data.get('opc_diagram') or request.data.get('opcDiagramId'),
        )
        return revision, parent, diagram

    @action(detail=False, methods=['post'])
    def preview(self, request):
        try:
            revision, parent, diagram = self._context_objects(request)
            return Response(build_opc_wbs_preview(
                revision=revision,
                parent_wbs_node=parent,
                opc_diagram=diagram,
                quantity=request.data.get('quantity', 1),
            ))
        except Exception as exc:
            return phase14_error_response(exc)

    @action(detail=False, methods=['post'])
    def apply(self, request):
        try:
            revision, parent, diagram = self._context_objects(request)
            imported = import_opc_to_wbs(
                revision=revision,
                parent_wbs_node=parent,
                opc_diagram=diagram,
                actor=request.user,
                quantity=request.data.get('quantity', 1),
                expected_project_version=request.data.get('expected_project_version') or request.data.get('expectedProjectVersion'),
                idempotency_key=request.data.get('idempotency_key') or request.data.get('idempotencyKey') or '',
            )
            return Response(self.get_serializer(imported).data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            return phase14_error_response(exc)


class ProjectProgressSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectProgressSnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectProgressSnapshot.objects.select_related('project', 'created_by').filter(project_id__in=accessible_project_ids(self.request.user))


class ProjectForecastSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectForecastSnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectForecastSnapshot.objects.select_related('project', 'created_by').filter(project_id__in=accessible_project_ids(self.request.user))


class ProjectImpactEventViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectImpactEventSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ProjectImpactEvent.objects.select_related('project', 'activity', 'deliverable').filter(project_id__in=accessible_project_ids(self.request.user))

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        impact = self.get_object()
        impact.active = False
        impact.resolved_by = request.user
        impact.resolved_at = timezone.now()
        impact.save(update_fields=['active', 'resolved_by', 'resolved_at'])
        return Response(self.get_serializer(impact).data)


class FinancialControlView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = dict(request.query_params.items())
        params["_accessible_project_ids"] = accessible_project_ids(request.user)
        return Response(build_financial_control_payload(request.user, params))


class FinancialControlConvertView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        params = dict(request.data.get("filters") or {})
        params["currency_mode"] = "converted"
        params["reporting_currency"] = request.data.get("reporting_currency") or params.get("reporting_currency")
        params["_manual_rates"] = request.data.get("manual_rates") or []
        params["_accessible_project_ids"] = accessible_project_ids(request.user)
        return Response(build_financial_control_payload(request.user, params))


class FinancialControlExchangeRateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        role = getattr(request.user, "org_role", "") or ""
        if not (request.user.is_superuser or request.user.is_staff or role in {"company_admin", "company_pm"}):
            raise PermissionDenied("You do not have permission to save exchange rates.")
        source_currency = str(request.data.get("source_currency") or "").upper()
        target_currency = str(request.data.get("target_currency") or "").upper()
        rate = request.data.get("rate")
        effective_date = parse_date(str(request.data.get("effective_date") or ""))
        if not source_currency or not target_currency:
            raise ValidationError({"currency": "source_currency and target_currency are required."})
        if source_currency == target_currency:
            raise ValidationError({"currency": "source_currency and target_currency must differ."})
        if effective_date is None:
            raise ValidationError({"effective_date": "effective_date is required in YYYY-MM-DD format."})
        try:
            rate_value = Decimal(str(rate))
        except Exception as exc:
            raise ValidationError({"rate": "rate must be numeric."}) from exc
        if rate_value <= 0:
            raise ValidationError({"rate": "rate must be greater than zero."})
        exchange_rate, _ = ExchangeRate.objects.update_or_create(
            source_currency=source_currency,
            target_currency=target_currency,
            effective_date=effective_date,
            defaults={
                "rate": rate_value,
                "source": request.data.get("source") or "manual",
                "created_by": request.user,
            },
        )
        for code in {source_currency, target_currency}:
            Currency.objects.get_or_create(code=code, defaults={"name": code, "symbol": code})
        return Response({
            "id": exchange_rate.id,
            "source_currency": exchange_rate.source_currency,
            "target_currency": exchange_rate.target_currency,
            "rate": str(exchange_rate.rate),
            "effective_date": exchange_rate.effective_date.isoformat(),
            "created_by": exchange_rate.created_by_id,
            "created_at": exchange_rate.created_at.isoformat(),
        }, status=status.HTTP_201_CREATED)


class FinancialControlGenerateCostSnapshotsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        project_id = request.data.get("project_id")
        if not project_id:
            raise ValidationError({"project_id": "project_id is required."})
        project = get_object_or_404(Project, pk=project_id)
        require_can_edit_project(request.user, project)
        currency = str(request.data.get("currency") or "").upper().strip()
        if not currency:
            raise ValidationError({"currency": "currency is required."})
        task_ids = request.data.get("task_ids") or None
        if task_ids is not None and not isinstance(task_ids, list):
            raise ValidationError({"task_ids": "task_ids must be a list."})
        result = generate_cost_variance_reports(
            project=project,
            status_date=request.data.get("status_date"),
            currency=currency,
            task_ids=task_ids,
            actor=request.user,
        )
        return Response(result, status=status.HTTP_200_OK)


def can_approve_budget(user):
    role = getattr(user, 'org_role', '') or ''
    return user.is_superuser or user.is_staff or role in {'company_admin', 'company_pm'}


def clean_budget_object(obj):
    try:
        obj.full_clean()
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)


def get_active_revision(project):
    """Compatibility name; selection is now explicit and never falls back."""
    return get_official_revision(project, ROLE_FORECAST, required=False)


def format_gantt_datetime(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def parse_gantt_datetime(value):
    if not value:
        return None
    text = str(value).replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def rollup_gantt_wbs_nodes(nodes):
    rolled_nodes = [dict(node) for node in nodes]
    node_by_id = {str(node.get("id")): node for node in rolled_nodes}
    child_ids_by_parent = defaultdict(list)

    for node in rolled_nodes:
        parent_id = node.get("parentId")
        if parent_id is not None:
            child_ids_by_parent[str(parent_id)].append(str(node.get("id")))

    def rollup_node(node_id):
        node = node_by_id.get(str(node_id))
        if not node or node.get("type") != "wbs":
            return

        child_ids = child_ids_by_parent.get(str(node_id), [])
        for child_id in child_ids:
            rollup_node(child_id)

        children = [node_by_id[child_id] for child_id in child_ids if child_id in node_by_id]
        dated_children = [
            child for child in children
            if parse_gantt_datetime(child.get("startDate")) and parse_gantt_datetime(child.get("endDate"))
        ]
        if not dated_children:
            node["startDate"] = ""
            node["endDate"] = ""
            node["duration"] = 0
            node["progress"] = 0
            return

        min_start = min(parse_gantt_datetime(child.get("startDate")) for child in dated_children)
        max_end = max(parse_gantt_datetime(child.get("endDate")) for child in dated_children)
        total_duration = sum(float(child.get("duration") or 0) or 1 for child in dated_children)
        weighted_progress = sum(
            (float(child.get("progress") or 0) * (float(child.get("duration") or 0) or 1))
            for child in dated_children
        )

        node["startDate"] = format_gantt_datetime(min_start)
        node["endDate"] = format_gantt_datetime(max_end)
        node["duration"] = round(max((max_end - min_start).total_seconds() / 3600, 0), 2)
        node["progress"] = round(weighted_progress / total_duration) if total_duration else 0

    for node in rolled_nodes:
        if node.get("type") == "wbs":
            rollup_node(node.get("id"))

    return rolled_nodes


def build_subproject_gantt_node(subproject, parent_id, sequence):
    child_revision = get_active_revision(subproject)
    start = None
    finish = None
    duration_hours = 0
    weighted_progress = 0

    if child_revision:
        child_tasks = TaskVersion.objects.filter(
            revision=child_revision,
            is_deleted=False,
        ).select_related('actual')
        for task in child_tasks:
            if task.planned_start and (start is None or task.planned_start < start):
                start = task.planned_start
            if task.planned_finish and (finish is None or task.planned_finish > finish):
                finish = task.planned_finish
            task_duration = float(task.duration_hours or 0)
            duration_hours += task_duration
            actual = getattr(task, 'actual', None)
            weighted_progress += float(actual.progress if actual else 0) * (task_duration or 1)

        if not start:
            start = child_revision.project_start
        if not finish:
            finish = child_revision.project_end or child_revision.project_start

    if not start:
        start = subproject.start_date
    if not finish:
        finish = subproject.end_date or start

    if duration_hours <= 0 and start and finish:
        duration_hours = max((finish - start).total_seconds() / 3600, 0)

    progress_denominator = duration_hours if duration_hours > 0 else 1
    progress = round(weighted_progress / progress_denominator) if weighted_progress else 0

    return {
        "id": subproject_virtual_id(subproject.id),
        "code": f"SP-{str(subproject.id)[:4].upper()}",
        "name": subproject.name,
        "parentId": parent_id,
        "type": "activity",
        "startDate": format_gantt_datetime(start),
        "endDate": format_gantt_datetime(finish),
        "duration": round(duration_hours, 2),
        "progress": progress,
        "resources": [],
        "constraintType": "ASAP",
        "constraintDate": None,
        "notes": "",
        "sequence": sequence,
        "isSubproject": True,
        "subprojectId": str(subproject.id),
        "subprojectRevisionId": str(child_revision.id) if child_revision else None,
    }


def find_task_dependency_cycle(revision, extra_edge=None, exclude_dependency_id=None, exclude_subproject_dependency_id=None):
    node_ids = set(str(task_id) for task_id in TaskVersion.objects.filter(
        revision=revision,
        is_deleted=False,
    ).values_list('task_id', flat=True))
    subproject_ids = {
        subproject_virtual_id(project_id)
        for project_id in Project.objects.filter(
            parent_project=revision.project,
            is_deleted=False,
        ).values_list('id', flat=True)
    }
    node_ids.update(subproject_ids)
    adjacency = defaultdict(list)

    dependency_query = Dependency.objects.filter(revision=revision)
    if exclude_dependency_id:
        dependency_query = dependency_query.exclude(id=exclude_dependency_id)

    for predecessor_id, successor_id in dependency_query.values_list('predecessor_id', 'successor_id'):
        predecessor_id = str(predecessor_id)
        successor_id = str(successor_id)
        if predecessor_id in node_ids and successor_id in node_ids:
            adjacency[predecessor_id].append(successor_id)

    subproject_dependency_query = SubprojectDependency.objects.filter(
        revision=revision,
        subproject__parent_project=revision.project,
    )
    if exclude_subproject_dependency_id:
        subproject_dependency_query = subproject_dependency_query.exclude(id=exclude_subproject_dependency_id)

    for dep in subproject_dependency_query.values("task_id", "subproject_id", "direction"):
        task_id = str(dep["task_id"])
        subproject_id = subproject_virtual_id(dep["subproject_id"])
        if dep["direction"] == SubprojectDependency.DIRECTION_TASK_TO_SUBPROJECT:
            predecessor_id, successor_id = task_id, subproject_id
        else:
            predecessor_id, successor_id = subproject_id, task_id
        if predecessor_id in node_ids and successor_id in node_ids:
            adjacency[predecessor_id].append(successor_id)

    if extra_edge:
        predecessor_id, successor_id = map(str, extra_edge)
        if predecessor_id == successor_id:
            return [predecessor_id, successor_id]
        if predecessor_id in node_ids and successor_id in node_ids:
            adjacency[predecessor_id].append(successor_id)

    visited = set()
    visiting = set()
    stack = []

    def dfs(task_id):
        visited.add(task_id)
        visiting.add(task_id)
        stack.append(task_id)

        for next_id in adjacency.get(task_id, []):
            if next_id not in visited:
                found = dfs(next_id)
                if found:
                    return found
            elif next_id in visiting:
                start = stack.index(next_id)
                return stack[start:] + [next_id]

        stack.pop()
        visiting.remove(task_id)
        return None

    for node_id in node_ids:
        if node_id not in visited:
            found = dfs(node_id)
            if found:
                return found
    return []


def serialize_dependency_cycle(revision, cycle_task_ids):
    versions = {
        str(task.task_id): task
        for task in TaskVersion.objects.filter(revision=revision, task_id__in=cycle_task_ids)
    }
    subproject_ids = [
        node_id.replace("subproject-", "")
        for node_id in cycle_task_ids
        if str(node_id).startswith("subproject-")
    ]
    subprojects = {
        str(project.id): project
        for project in Project.objects.filter(id__in=subproject_ids)
    }

    cycle = []
    for task_id in cycle_task_ids:
        if str(task_id).startswith("subproject-"):
            project_id = str(task_id).replace("subproject-", "")
            project = subprojects.get(project_id)
            cycle.append({
                "id": task_id,
                "name": project.name if project else task_id,
                "code": f"SP-{project_id[:4].upper()}",
            })
        else:
            cycle.append({
                "id": task_id,
                "name": getattr(versions.get(task_id), 'title', task_id),
                "code": f"ACT-{task_id[:4].upper()}",
            })
    return cycle


def submit_budget_object(obj, user):
    if obj.status not in {'DRAFT', 'REJECTED'}:
        raise ValidationError({'status': 'Only draft or rejected budget records can be submitted.'})
    obj.status = 'SUBMITTED'
    obj.submitted_by = user
    obj.submitted_at = timezone.now()
    obj.approved_by = None
    obj.approved_at = None
    obj.rejected_by = None
    obj.rejected_at = None
    obj.rejection_reason = ''
    clean_budget_object(obj)
    obj.save(update_fields=[
        'status', 'submitted_by', 'submitted_at',
        'approved_by', 'approved_at', 'rejected_by', 'rejected_at',
        'rejection_reason',
    ])


def approve_budget_object(obj, user):
    if not can_approve_budget(user):
        raise PermissionDenied('You do not have permission to approve budget records.')
    if obj.status != 'SUBMITTED':
        raise ValidationError({'status': 'Only submitted budget records can be approved.'})
    obj.status = 'APPROVED'
    obj.approved_by = user
    obj.approved_at = timezone.now()
    obj.rejected_by = None
    obj.rejected_at = None
    obj.rejection_reason = ''
    clean_budget_object(obj)
    obj.save(update_fields=[
        'status', 'approved_by', 'approved_at',
        'rejected_by', 'rejected_at', 'rejection_reason',
    ])


def reject_budget_object(obj, user, reason=''):
    if not can_approve_budget(user):
        raise PermissionDenied('You do not have permission to reject budget records.')
    if obj.status != 'SUBMITTED':
        raise ValidationError({'status': 'Only submitted budget records can be rejected.'})
    obj.status = 'REJECTED'
    obj.rejected_by = user
    obj.rejected_at = timezone.now()
    obj.rejection_reason = reason or ''
    obj.approved_by = None
    obj.approved_at = None
    clean_budget_object(obj)
    obj.save(update_fields=[
        'status', 'rejected_by', 'rejected_at', 'rejection_reason',
        'approved_by', 'approved_at',
    ])


def log_budget_audit(request, action, target, old=None, extra=None):
    new = model_to_dict_safe(target)
    log_event(
        action,
        target=target,
        category='business',
        changes=diff_dicts(old, new) if old is not None else None,
        extra=extra,
        request=request,
    )


def check_revision_is_open(revision, user=None):
    """
    ع¯ط§ط±ط¯ طھط±ع©غŒط¨غŒ ط¨ط±ط§غŒ ظˆغŒط±ط§غŒط´ ط²ظ…ط§ظ†â€Œط¨ظ†ط¯غŒ:
    1) ظ†ط³ط®ظ‡ ظ†ط¨ط§غŒط¯ ظ‚ظپظ„ (approved) ط¨ط§ط´ط¯.
    2) ط§ع¯ط± ع©ط§ط±ط¨ط± ط¯ط§ط¯ظ‡ ط´ظˆط¯طŒ ط¨ط§غŒط¯ ظ…ط¬ظˆط² ظˆغŒط±ط§غŒط´ ظ¾ط±ظˆعکظ‡ ط±ط§ ط¯ط§ط´طھظ‡ ط¨ط§ط´ط¯.

    ط±ظپطھط§ط± ظ‚ط¯غŒظ…غŒ (ظپظ‚ط· ط¨ط§ revision) ط¨ط±ط§غŒ ط­ظپط¸ ط³ط§ط²ع¯ط§ط±غŒ ط­ظپط¸ ط´ط¯ظ‡ ط§ط³طھ.
    """
    if revision.approved_at is not None:
        raise PermissionDenied("ط§غŒظ† ظ†ط³ط®ظ‡ ظ‚ظپظ„ ط´ط¯ظ‡ ط§ط³طھ ظˆ ظ‚ط§ط¨ظ„ طھط؛غŒغŒط± ظ†غŒط³طھ.")
    if user is not None:
        require_can_edit_project(user, revision.project)


def check_can_edit_revision(user, revision):
    """ظ†ط³ط®ظ‡â€ŒغŒ طµط±غŒط­â€Œطھط± ط¨ط±ط§غŒ ط§ط³طھظپط§ط¯ظ‡â€Œظ‡ط§غŒ ط¬ط¯غŒط¯."""
    check_revision_is_open(revision, user)


class ProjectViewSet(viewsets.ModelViewSet):
    """ظ…ط¯غŒط±غŒطھ ظ¾ط±ظˆعکظ‡â€Œظ‡ط§"""
    queryset = Project.objects.filter(is_deleted=False).exclude(name='System-Personal-Tasks')
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # ظپظ‚ط· ظ¾ط±ظˆعکظ‡â€Œظ‡ط§غŒغŒ ع©ظ‡ ع©ط§ط±ط¨ط± ط§ط¬ط§ط²ظ‡ظ” ظ…ط´ط§ظ‡ط¯ظ‡ ط¯ط§ط±ط¯ (ط³ط·ط­ظگ ط´ط±ع©طھ â†’ ظ‡ظ…ظ‡).
        return super().get_queryset().filter(
            id__in=accessible_project_ids(self.request.user)
        )

    def perform_create(self, serializer):
        user = self.request.user
        require_can_create_project(user)
        # ظ…ط¯غŒط± ظˆط§ط­ط¯ â†’ ظ¾ط±ظˆعکظ‡ ط¨ظ‡ ظˆط§ط­ط¯ ط®ظˆط¯ط´ ع¯ط±ظ‡ ظ…غŒâ€Œط®ظˆط±ط¯
        owner_unit = getattr(user, 'unit', None)
        serializer.save(created_by=user, owner_unit=owner_unit)

    def perform_update(self, serializer):
        require_can_edit_project(self.request.user, serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        require_can_edit_project(self.request.user, instance)
        instance.is_deleted = True
        instance.lifecycle_status = Project.LIFECYCLE_ARCHIVED
        instance.save(update_fields=['is_deleted', 'lifecycle_status'])

    @action(detail=True, methods=['get', 'patch'], url_path='official-schedule')
    @transaction.atomic
    def official_schedule(self, request, pk=None):
        project = Project.objects.select_for_update().get(pk=self.get_object().pk)
        if request.method.lower() == 'patch':
            require_can_edit_project(request.user, project)
            allowed = {
                'activeBaselineRevisionId', 'currentExecutionRevisionId',
                'currentForecastRevisionId', 'workingRevisionId',
                'currentDataDate', 'lifecycleStatus',
            }
            unknown = set(request.data.keys()) - allowed
            if unknown:
                raise ValidationError({'detail': f"Unsupported fields: {', '.join(sorted(unknown))}"})
            baseline_id = request.data.get('activeBaselineRevisionId')
            if baseline_id:
                baseline = Revision.objects.filter(
                    pk=baseline_id,
                    project=project,
                    is_deleted=False,
                ).first()
                if baseline and baseline.approved_at is not None and not baseline.is_baseline:
                    baseline.is_baseline = True
                    baseline.save(update_fields=['is_baseline'])
            old = model_to_dict_safe(project)
            serializer = self.get_serializer(project, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            log_event(
                'official_schedule_changed', target=project, category='business',
                changes=diff_dicts(old, model_to_dict_safe(project)), request=request,
            )
        return Response(self.get_serializer(project).data)

    @action(detail=True, methods=['post'], url_path='approve-baseline')
    def approve_phase14_baseline(self, request, pk=None):
        project = self.get_object()
        revision_id = request.data.get('revision') or request.data.get('revision_id') or project.working_revision_id or project.active_baseline_revision_id
        revision = get_object_or_404(Revision, pk=revision_id, project=project, is_deleted=False)
        try:
            snapshot = approve_project_baseline(
                actor=request.user,
                project=project,
                revision=revision,
                expected_project_version=request.data.get('expected_project_version'),
                change_summary=request.data.get('change_summary', ''),
            )
            return Response(ProjectBaselineSnapshotSerializer(snapshot).data)
        except Exception as exc:
            return phase14_error_response(exc)

    @action(detail=True, methods=['post'], url_path='progress-snapshot')
    def progress_snapshot(self, request, pk=None):
        try:
            snapshot = create_progress_snapshot(actor=request.user, project=self.get_object(), data_date=parse_cpm_data_date(request.data.get('data_date')) if request.data.get('data_date') else None)
            return Response(ProjectProgressSnapshotSerializer(snapshot).data)
        except Exception as exc:
            return phase14_error_response(exc)

    @action(detail=True, methods=['post'], url_path='forecast-snapshot')
    def forecast_snapshot(self, request, pk=None):
        try:
            snapshot = create_forecast_snapshot(actor=request.user, project=self.get_object(), data_date=parse_cpm_data_date(request.data.get('data_date')) if request.data.get('data_date') else None)
            return Response(ProjectForecastSnapshotSerializer(snapshot).data)
        except Exception as exc:
            return phase14_error_response(exc)

    @action(detail=True, methods=['get'], url_path='phase14-dashboard')
    def phase14_dashboard(self, request, pk=None):
        return Response(project_dashboard(self.get_object()))


class ProjectViewerViewSet(viewsets.ModelViewSet):
    """
    ظ…ط¯غŒط±غŒطھظگ ظ…ط´ط§ظ‡ط¯ظ‡â€Œع¯ط±ظ‡ط§غŒ ظ¾ط±ظˆعکظ‡ (Project Viewers).
    ط§ظپط²ظˆط¯ظ†/ط­ط°ظپظگ ظ…ط´ط§ظ‡ط¯ظ‡â€Œع¯ط± ظپظ‚ط· طھظˆط³ط·ظگ ط³ط§ط²ظ†ط¯ظ‡ظ” ظ¾ط±ظˆعکظ‡ (ظˆ ط³ط·ط­ظگ ط´ط±ع©طھ) ظ…ط¬ط§ط² ط§ط³طھ.
    """
    queryset = ProjectViewer.objects.select_related('user', 'project', 'added_by').all()
    serializer_class = ProjectViewerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset

    def perform_create(self, serializer):
        project = serializer.validated_data['project']
        require_can_manage_viewers(self.request.user, project)
        serializer.save(added_by=self.request.user)

    def perform_destroy(self, instance):
        require_can_manage_viewers(self.request.user, instance.project)
        instance.delete()


class CalendarViewSet(viewsets.ModelViewSet):
    """طھط¹ط±غŒظپ ظˆ ظ…ط¯غŒط±غŒطھ طھظ‚ظˆغŒظ…â€Œظ‡ط§غŒ ع©ط§ط±غŒ ظ…ط³طھظ‚ظ„ (ط³ط§ط¹ط§طھ ع©ط§ط±غŒ + طھط¹ط·غŒظ„ط§طھ)"""
    queryset = Calendar.objects.all().prefetch_related('intervals', 'exceptions')
    serializer_class = CalendarSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        # ظپظ‚ط· ظ‚ط§ظ„ط¨â€Œظ‡ط§غŒ ظ…ط³طھظ‚ظ„ (ط¨ط¯ظˆظ† ظ¾ط±ظˆعکظ‡) ط¯ط± طµظˆط±طھ ط¯ط±ط®ظˆط§ط³طھ
        if self.request.query_params.get('templates') == 'true':
            queryset = queryset.filter(project__isnull=True)
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset


class RevisionViewSet(viewsets.ModelViewSet):
    """ظ…ط¯غŒط±غŒطھ ظ†ط³ط®ظ‡â€Œظ‡ط§ (Revisions) ط¨ط§ ظ‚ط§ط¨ظ„غŒطھ ظپغŒظ„طھط± ط¨ط± ط§ط³ط§ط³ ظ¾ط±ظˆعکظ‡"""
    queryset = Revision.objects.filter(is_deleted=False ).order_by('-number')
    serializer_class = RevisionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(project_id__in=accessible_project_ids(self.request.user))
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset

    def perform_destroy(self, instance):
        project = instance.project
        official_fields = {
            'active_baseline_revision': project.active_baseline_revision_id,
            'current_execution_revision': project.current_execution_revision_id,
            'current_forecast_revision': project.current_forecast_revision_id,
            'working_revision': project.working_revision_id,
        }
        used_as = [name for name, revision_id in official_fields.items() if revision_id == instance.pk]
        if used_as:
            raise ValidationError({
                'revision': f"Official revision cannot be deleted ({', '.join(used_as)})."
            })
        instance.is_deleted = True
        instance.save(update_fields=['is_deleted'])
    # --- ظ…طھط¯ ظ‚ظپظ„ ع©ط±ط¯ظ† ظ†ط³ط®ظ‡ ---
    @action(detail=True, methods=['post'], url_path='approve', url_name='approve')
    def approve_revision(self, request, pk=None):
        revision = self.get_object()

        if revision.approved_at:
            return Response({"detail": "ط§غŒظ† ظ†ط³ط®ظ‡ ظ‚ط¨ظ„ط§ظ‹ طھط§غŒغŒط¯ ظˆ ظ‚ظپظ„ ط´ط¯ظ‡ ط§ط³طھ."}, status=status.HTTP_400_BAD_REQUEST)

        # ظپظ‚ط· طھط§غŒغŒط¯ع©ظ†ظ†ط¯ظ‡â€ŒغŒ طھط¹غŒغŒظ†â€Œط´ط¯ظ‡ (غŒط§ admin) ظ…غŒâ€Œطھظˆط§ظ†ط¯ طھط§غŒغŒط¯ ع©ظ†ط¯
        from .permissions import require_can_approve_revision
        require_can_approve_revision(request.user, revision)

        revision.approved_by = request.user
        revision.approved_at = timezone.now()
        revision.save()
        promote_approved_revision(revision, data_date=revision.project.current_data_date)

        return Response({"detail": "ظ†ط³ط®ظ‡ ط¨ط§ ظ…ظˆظپظ‚غŒطھ ظ‚ظپظ„ ط´ط¯."}, status=status.HTTP_200_OK)

    # --- ط§ط±ط³ط§ظ„ ط§ط·ظ„ط§ط¹ط§طھ ط¨ظ‡ ع¯ط§ظ†طھâ€Œع†ط§ط±طھ ---
    @action(detail=True, methods=['get'], url_path='gantt-data', url_name='gantt-data')
    def get_gantt_data(self, request, pk=None):
        revision = self.get_object()

        wbs_nodes = WBSNodeVersion.objects.filter(
            revision=revision,
            is_deleted=False,
        ).select_related('node', 'parent__node')
        wbs_serializer = WbsNodeSerializer(wbs_nodes, many=True)

        assignments = Assignment.objects.filter(revision=revision).select_related('resource')
        tasks = TaskVersion.objects.filter(
            revision=revision,
            is_deleted=False,
        ).select_related(
            'task',
            'wbs_node__node',
            'actual',
            'metrics',
        ).prefetch_related(
            Prefetch('task__assignment_set', queryset=assignments, to_attr='revision_assignments')
        )
        activity_serializer = ActivityNodeSerializer(tasks, many=True)

        nodes = wbs_serializer.data + activity_serializer.data
        root_wbs = next((node for node in wbs_nodes if node.parent_id is None), None)
        root_parent_id = root_wbs.node_id if root_wbs else None
        subprojects = Project.objects.filter(
            parent_project=revision.project,
            is_deleted=False,
        ).order_by('name')
        subproject_nodes = [
            build_subproject_gantt_node(subproject, root_parent_id, 900000 + index)
            for index, subproject in enumerate(subprojects, start=1)
        ]
        nodes = rollup_gantt_wbs_nodes(nodes + subproject_nodes)

        dependencies = Dependency.objects.filter(revision=revision)
        dependency_serializer = DependencySerializer(dependencies, many=True)
        subproject_dependencies = SubprojectDependency.objects.filter(
            revision=revision,
            subproject__parent_project=revision.project,
        ).select_related('task', 'subproject')
        subproject_dependency_serializer = SubprojectDependencySerializer(subproject_dependencies, many=True)

        return Response({
            "nodes": nodes,
            "dependencies": list(dependency_serializer.data) + list(subproject_dependency_serializer.data)
        }, status=status.HTTP_200_OK)

    # --- ط³ط§ط®طھ ظ¾غŒط´â€Œظ†ظˆغŒط³ (Draft) ط§ط² غŒع© ظ†ط³ط®ظ‡ ---
    @action(detail=True, methods=['post'], url_path='create-draft')
    @transaction.atomic
    def create_draft_from_revision(self, request, pk=None):
        base_revision = self.get_object()

        # ظپظ‚ط· ع©ط³غŒ ع©ظ‡ ط§ط¬ط§ط²ظ‡ ظˆغŒط±ط§غŒط´ ظ¾ط±ظˆعکظ‡ ط±ط§ ط¯ط§ط±ط¯ ظ…غŒâ€Œطھظˆط§ظ†ط¯ ظ¾غŒط´â€Œظ†ظˆغŒط³ ط¨ط³ط§ط²ط¯
        require_can_edit_project(request.user, base_revision.project)
        existing_working = base_revision.project.working_revision
        if (
            existing_working
            and existing_working.pk != base_revision.pk
            and not existing_working.is_deleted
            and existing_working.approved_at is None
        ):
            return Response(
                {"detail": f"Revision {existing_working.number} is already the official working revision."},
                status=status.HTTP_409_CONFLICT,
            )

        if not base_revision.approved_at:
            return Response(
                {"detail": "ظ†ط³ط®ظ‡ ظ¾ط§غŒظ‡ ظ‡ظ†ظˆط² ط¨ط§ط² ط§ط³طھ. ط§ط¨طھط¯ط§ ط¢ظ† ط±ط§ ظ‚ظپظ„ ع©ظ†غŒط¯."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ط¯ط±غŒط§ظپطھ ظˆ ط§ط¹طھط¨ط§ط±ط³ظ†ط¬غŒ طھظˆط¶غŒط­ط§طھ (ط§ط¬ط¨ط§ط±غŒ)
        description = request.data.get('description', '').strip()
        if not description:
            return Response(
                {"detail": "ظˆط§ط±ط¯ ع©ط±ط¯ظ† طھظˆط¶غŒط­ط§طھ (ط¯ظ„غŒظ„ ط³ط§ط®طھ ظ¾غŒط´â€Œظ†ظˆغŒط³) ط§ظ„ط²ط§ظ…غŒ ط§ط³طھ."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # طھط¹غŒغŒظ†ظگ Approver ط¯ط± ط²ظ…ط§ظ†ظگ ط³ط§ط®طھظگ ظ†ط³ط®ظ‡ â€” scope-aware:
        # ط´ط±ع©طھغŒ â†’ ظ¾غŒط´â€Œظپط±ط¶ = ظ…ط¯غŒط±ظگ ط¨ط±ظ†ط§ظ…ظ‡â€Œط±غŒط²غŒ
        # ط¯ط±ظˆظ†â€Œظˆط§ط­ط¯غŒ â†’ ظ¾غŒط´â€Œظپط±ط¶ = ظ…ط¯غŒط±ظگ ظˆط§ط­ط¯ظگ طµط§ط­ط¨ظگ ظ¾ط±ظˆعکظ‡
        # override ط¯ط³طھغŒ ظ‡ظ…غŒط´ظ‡ ظ…ظ…ع©ظ† ط§ط³طھ (approverId / approver_id)
        approver_id = request.data.get('approverId') or request.data.get('approver_id')
        requested_approver = get_object_or_404(User, pk=approver_id) if approver_id else None
        try:
            approver = resolve_designated_approver(base_revision.project, requested_approver)
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)

        locked_project = Project.objects.select_for_update().get(pk=base_revision.project_id)
        new_revision_number = (
            Revision.objects.filter(project=locked_project).aggregate(max_number=Max('number'))['max_number'] or 0
        ) + 1
        new_revision = Revision.objects.create(
            project=locked_project,
            number=new_revision_number,
            description=description,
            project_start=base_revision.project_start,
            project_end=base_revision.project_end,
            created_by=request.user,
            designated_approver=approver,
        )

        assign_working_revision(locked_project, new_revision)

        old_to_new_wbs_map = {}
        old_wbs_nodes = WBSNodeVersion.objects.filter(
            revision=base_revision, is_deleted=False
        ).order_by('level', 'sequence')

        for old_node in old_wbs_nodes:
            new_parent = None
            if old_node.parent_id:
                new_parent = old_to_new_wbs_map.get(old_node.parent_id)

            new_node = WBSNodeVersion.objects.create(
                node=old_node.node,
                revision=new_revision,
                parent=new_parent,
                title=old_node.title,
                sequence=old_node.sequence
            )
            old_to_new_wbs_map[old_node.id] = new_node

        old_tasks = list(
            TaskVersion.objects.filter(revision=base_revision, is_deleted=False)
            .select_related('actual')
        )
        new_tasks_to_create = []

        for old_task in old_tasks:
            new_tasks_to_create.append(
                TaskVersion(
                    task=old_task.task,
                    revision=new_revision,
                    wbs_node=old_to_new_wbs_map[old_task.wbs_node_id],
                    title=old_task.title,
                    calendar=old_task.calendar,
                    planned_start=old_task.planned_start,
                    planned_finish=old_task.planned_finish,
                    duration_hours=old_task.duration_hours,
                    weight=old_task.weight,
                    description=old_task.description
                )
            )
        TaskVersion.objects.bulk_create(new_tasks_to_create)

        new_task_versions = {
            task_version.task_id: task_version
            for task_version in TaskVersion.objects.filter(
                revision=new_revision,
                task_id__in=[old_task.task_id for old_task in old_tasks],
                is_deleted=False,
            )
        }
        new_actuals_to_create = []
        for old_task in old_tasks:
            old_actual = getattr(old_task, 'actual', None)
            new_task_version = new_task_versions.get(old_task.task_id)
            if old_actual is None or new_task_version is None:
                continue
            new_actuals_to_create.append(
                TaskActual(
                    task_version=new_task_version,
                    actual_start=old_actual.actual_start,
                    actual_finish=old_actual.actual_finish,
                    progress=old_actual.progress,
                    updated_by=old_actual.updated_by,
                )
            )
        TaskActual.objects.bulk_create(new_actuals_to_create)

        old_deps = Dependency.objects.filter(revision=base_revision)
        new_deps_to_create = []
        for dep in old_deps:
            new_deps_to_create.append(
                Dependency(
                    revision=new_revision,
                    predecessor=dep.predecessor,
                    successor=dep.successor,
                    dependency_type=dep.dependency_type,
                    lag_hours=dep.lag_hours
                )
            )
        Dependency.objects.bulk_create(new_deps_to_create)

        old_subproject_deps = SubprojectDependency.objects.filter(revision=base_revision)
        new_subproject_deps_to_create = []
        for dep in old_subproject_deps:
            new_subproject_deps_to_create.append(
                SubprojectDependency(
                    revision=new_revision,
                    task=dep.task,
                    subproject=dep.subproject,
                    direction=dep.direction,
                    dependency_type=dep.dependency_type,
                    lag_hours=dep.lag_hours
                )
            )
        SubprojectDependency.objects.bulk_create(new_subproject_deps_to_create)

        serializer = self.get_serializer(new_revision)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='run-cpm', url_name='run-cpm')
    def run_cpm_engine(self, request, pk=None):
        """
        ط§ط¬ط±ط§غŒ ظ…ظˆطھظˆط± ظ…ط­ط§ط³ط¨ط§طھغŒ ط²ظ…ط§ظ†â€Œط¨ظ†ط¯غŒ (CPM) ط±ظˆغŒ غŒع© ظ†ط³ط®ظ‡ ط®ط§طµ
        """
        revision = self.get_object()
        official_working = get_official_revision(revision.project, ROLE_WORKING, required=False)
        if official_working is None or official_working.pk != revision.pk:
            return Response(
                {"detail": "CPM can only run on the official working revision."},
                status=status.HTTP_409_CONFLICT,
            )

        # ط¨ط±ط±ط³غŒ ط§غŒظ†ع©ظ‡ ط¢غŒط§ ظ†ط³ط®ظ‡ ط¨ط§ط² ط§ط³طھ ظˆ ظ‚ط§ط¨ظ„غŒطھ ظˆغŒط±ط§غŒط´ ط¯ط§ط±ط¯ غŒط§ ط®غŒط±
        check_revision_is_open(revision, request.user)

        try:
            # ط§ط¬ط±ط§غŒ ظ…ظˆطھظˆط± CPM ع©ظ‡ Early/Late start ظˆ finish ظ‡ط§ ط±ط§ ط­ط³ط§ط¨ ظˆ ط°ط®غŒط±ظ‡ ظ…غŒâ€Œع©ظ†ط¯

            data_date = parse_cpm_data_date(request.data.get("dataDate"))
            engine = CPMEngine(revision, data_date=data_date)
            cpm_result = engine.run()
            Project.objects.filter(pk=revision.project_id).update(
                current_data_date=cpm_result.get("data_date")
            )

            # Persist the latest parent-network warning on each child project so
            # its manager can see the conflict without access to the parent.
            warning_time = timezone.now()
            child_projects = Project.objects.filter(parent_project=revision.project, is_deleted=False)
            child_projects.update(parent_schedule_warning={}, parent_schedule_warning_updated_at=warning_time)
            enriched_warnings = []
            for warning in cpm_result.get("subproject_warnings", []):
                enriched = {
                    **warning,
                    "parentProjectId": str(revision.project_id),
                    "parentProjectName": revision.project.name,
                    "parentRevisionId": revision.id,
                    "generatedAt": warning_time.isoformat(),
                }
                enriched_warnings.append(enriched)
                child_projects.filter(pk=warning.get("subprojectId")).update(
                    parent_schedule_warning=enriched,
                    parent_schedule_warning_updated_at=warning_time,
                )
            cpm_result["subproject_warnings"] = enriched_warnings

            # ظ¾ط³ ط§ط² ظ…ط­ط§ط³ط¨ظ‡طŒ ظ…ط³طھظ‚غŒظ…ط§ظ‹ ط¯ط§ط¯ظ‡â€Œظ‡ط§غŒ ط¢ظ¾ط¯غŒطھâ€Œط´ط¯ظ‡ ع¯ط§ظ†طھâ€Œع†ط§ط±طھ ط±ط§ ط§ط³طھط®ط±ط§ط¬ ع©ط±ط¯ظ‡ ظˆ ط¨ط±ظ…غŒâ€Œع¯ط±ط¯ط§ظ†غŒظ…
            # ط§غŒظ† ع©ط§ط± ط¨ط§ط¹ط« ظ…غŒâ€Œط´ظˆط¯ ظپط±ط§ظ†طھâ€Œط§ظ†ط¯ ظ†غŒط§ط² ط¨ظ‡ Request ط¯ظˆظ… ظ†ط¯ط§ط´طھظ‡ ط¨ط§ط´ط¯
            response = self.get_gantt_data(request, pk=pk)
            subproject_warnings = cpm_result.get("subproject_warnings", [])
            warnings_by_node = {warning["nodeId"]: warning for warning in subproject_warnings}
            for node in response.data.get("nodes", []):
                warning = warnings_by_node.get(str(node.get("id")))
                if warning:
                    node["scheduleWarning"] = warning
            response.data["cpm"] = {
                "totalTasks": cpm_result.get("total_tasks", 0),
                "externalSubprojects": cpm_result.get("external_subprojects", 0),
                "criticalTasks": cpm_result.get("critical_tasks", 0),
                "frozenTasks": cpm_result.get("frozen_tasks", 0),
                "completedTasks": cpm_result.get("completed_tasks", 0),
                "inProgressTasks": cpm_result.get("in_progress_tasks", 0),
                "replannedTasks": cpm_result.get("replanned_tasks", 0),
                "dataDate": cpm_result.get("data_date").isoformat() if cpm_result.get("data_date") else None,
                "projectStart": cpm_result.get("project_start").isoformat() if cpm_result.get("project_start") else None,
                "projectFinish": cpm_result.get("project_finish").isoformat() if cpm_result.get("project_finish") else None,
                "subprojectWarnings": subproject_warnings,
            }
            return response

        except CPMCycleError as e:
            return Response(
                {
                    "detail": str(e),
                    "cycle": serialize_dependency_cycle(revision, e.cycle_task_ids),
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        except ValueError as e:
            # ط§غŒظ† ط®ط·ط§ ظ…ط¹ظ…ظˆظ„ط§ظ‹ ط¨ظ‡ ط®ط§ط·ط± ظˆط¬ظˆط¯ ط­ظ„ظ‚ظ‡ (Cycle) ط¯ط± ع¯ط±ط§ظپ ظˆط§ط¨ط³طھع¯غŒâ€Œظ‡ط§ ظ¾ط±طھط§ط¨ ظ…غŒâ€Œط´ظˆط¯
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"detail": f"ط®ط·ط§غŒ ظ¾غŒط´â€Œط¨غŒظ†غŒ ظ†ط´ط¯ظ‡ ط¯ط± ظ…ط­ط§ط³ط¨ط§طھ CPM: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
class WbsNodeViewSet(viewsets.ModelViewSet):
    queryset = WBSNodeVersion.objects.filter(is_deleted=False)
    serializer_class = WbsNodeSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'node__id'

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_value = self.kwargs[self.lookup_field]

        # ع¯ط±ظپطھظ† ط±غŒظˆغŒعکظ† ط§ط² ط¢ط¯ط±ط³ ط¯ط± طµظˆط±طھ ظˆط¬ظˆط¯
        revision_id = self.request.query_params.get('revision_id')

        filter_kwargs = {self.lookup_field: lookup_value}
        if revision_id:
            filter_kwargs['revision_id'] = revision_id
        else:
            # ظ¾غŒط¯ط§ ع©ط±ط¯ظ† ط±ط¯غŒظپ ط¯ط± ظ†ط³ط®ظ‡â€Œط§غŒ ع©ظ‡ ظ‡ظ†ظˆط² طھط§غŒغŒط¯ ظˆ ظ‚ظپظ„ ظ†ط´ط¯ظ‡ ط§ط³طھ
            filter_kwargs['revision_id'] = F('node__project__working_revision_id')

        # ط§ط³طھظپط§ط¯ظ‡ ط§ط² first() ط¨ط±ط§غŒ ط¬ظ„ظˆع¯غŒط±غŒ ط§ط² ط§ط±ظˆط± طھط¹ط¯ط¯ ط±ط¯غŒظپ
        obj = queryset.filter(**filter_kwargs).first()

        if not obj:
            from django.http import Http404
            raise Http404("ع¯ط±ظ‡ WBS ط¯ط± ظ†ط³ط®ظ‡ ظپط¹ط§ظ„ غŒط§ظپطھ ظ†ط´ط¯.")

        self.check_object_permissions(self.request, obj)
        return obj
    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(revision__project_id__in=accessible_project_ids(self.request.user))
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(revision__project_id=project_id)
        revision_id = self.request.query_params.get('revision_id')
        revision_role = self.request.query_params.get('revision_role')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        elif project_id:
            project = get_object_or_404(Project, pk=project_id, is_deleted=False)
            if revision_role == 'execution':
                queryset = queryset.filter(revision_id=project.current_execution_revision_id) if project.current_execution_revision_id else queryset.none()
            else:
                queryset = queryset.filter(revision_id=project.working_revision_id) if project.working_revision_id else queryset.none()
        elif revision_role == 'execution':
            queryset = queryset.filter(revision_id=F('node__project__current_execution_revision_id'))
        else:
            queryset = queryset.filter(revision_id=F('node__project__working_revision_id'))
        return queryset

    # --- ظ‡ظ†ط¯ظ„ ع©ط±ط¯ظ† ط³ط§ط®طھ طµط­غŒط­ ع¯ط±ظ‡ WBS ---
    def perform_create(self, serializer):
        revision_id = self.request.data.get('revisionId') or self.request.query_params.get('revision_id')
        if not revision_id:
            raise ValidationError({"revisionId": "ط¢غŒط¯غŒ ظ†ط³ط®ظ‡ ط¨ط±ط§غŒ ط³ط§ط®طھ ع¯ط±ظ‡ ط§ظ„ط²ط§ظ…غŒ ط§ط³طھ."})

        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision, self.request.user)

        # ظ¾غŒط¯ط§ ع©ط±ط¯ظ† ع¯ط±ظ‡ ظˆط§ظ„ط¯ (ط¯ط± طµظˆط±طھ ظˆط¬ظˆط¯)
        parent_id = self.request.data.get('parentId')
        parent_node = None
        if parent_id:
            parent_node = get_object_or_404(WBSNodeVersion, node_id=parent_id, revision=revision)

        # ---------------- NEW CODE ----------------
        # Calculate the next sequence number for this parent in this revision
        max_seq_dict = WBSNodeVersion.objects.filter(
            revision=revision,
            parent=parent_node
        ).aggregate(Max('sequence'))

        task_max_seq = 0
        if parent_node is not None:
            task_max_seq = TaskVersion.objects.filter(
                revision=revision, wbs_node=parent_node, is_deleted=False
            ).aggregate(Max('sequence')).get('sequence__max') or 0
        current_max_seq = max(max_seq_dict.get('sequence__max') or 0, task_max_seq)
        next_sequence = current_max_seq + 1
        # ------------------------------------------

        base_node = WBSNode.objects.create(project=revision.project)

        # Pass the newly calculated sequence to save()
        serializer.save(
            node=base_node,
            revision=revision,
            parent=parent_node,
            sequence=next_sequence
        )

    def perform_update(self, serializer):
        check_revision_is_open(serializer.instance.revision, self.request.user)
        serializer.save()

    def perform_destroy(self, instance):
        # ط¨ط±ط±ط³غŒ ظ‚ظپظ„ ظ†ط¨ظˆط¯ظ† ظ†ط³ط®ظ‡
        check_revision_is_open(instance.revision, self.request.user)
        if instance.parent_id is None:
            raise ValidationError({"detail": "The root WBS node cannot be deleted."})

        # غ±. ع¯ط±ظپطھظ† ط®ظˆط¯ ع¯ط±ظ‡ ظˆ طھظ…ط§ظ…غŒ ط²غŒط±ظ…ط¬ظ…ظˆط¹ظ‡â€Œظ‡ط§غŒ ط¢ظ† (ظپط±ط²ظ†ط¯ط§ظ†طŒ ظ†ظˆظ‡â€Œظ‡ط§ ظˆ...) ط¨ظ‡ ع©ظ…ع© MPTT
        descendants = instance.get_descendants(include_self=True)

        # غ². ظ…ط®ظپغŒ ع©ط±ط¯ظ† طھظ…ط§ظ… طھط³ع©â€Œظ‡ط§غŒغŒ ع©ظ‡ ط¨ظ‡ ط§غŒظ† ع¯ط±ظ‡â€Œظ‡ط§ (ظˆط§ظ„ط¯ غŒط§ ظپط±ط²ظ†ط¯ط§ظ†) ظ…طھطµظ„ ظ‡ط³طھظ†ط¯
        TaskVersion.objects.filter(
            wbs_node__in=descendants,
            revision=instance.revision
        ).update(is_deleted=True)

        # غ³. ظ…ط®ظپغŒ ع©ط±ط¯ظ† ط®ظˆط¯ ع¯ط±ظ‡ WBS ظˆ طھظ…ط§ظ…غŒ ع¯ط±ظ‡â€Œظ‡ط§غŒ ظپط±ط²ظ†ط¯ ط¢ظ† ط¨ظ‡ طµظˆط±طھ غŒع©ط¬ط§
        descendants.update(is_deleted=True)

    # --- ظ…ط±طھط¨â€Œط³ط§ط²غŒ ظ…ط¬ط¯ط¯ ظ†ظˆط¯ظ‡ط§غŒ WBS (drag & drop) ---
    @action(detail=False, methods=['post'], url_path='reorder')
    @transaction.atomic
    def reorder(self, request):
        """
        طھط±طھغŒط¨ ظ†ظ…ط§غŒط´ ظ†ظˆط¯ظ‡ط§غŒ WBS ظ‡ظ…â€Œظ†غŒط§ (ط²غŒط± غŒع© ظˆط§ظ„ط¯) ط±ط§ طھط؛غŒغŒط± ظ…غŒâ€Œط¯ظ‡ط¯.
        ظˆط±ظˆط¯غŒ: revisionId ظˆ orderedIds (ظ„غŒط³طھ node.id ظ‡ط§ ط¨ظ‡ طھط±طھغŒط¨ ط¬ط¯غŒط¯).
        ط¨ظ‡ ط¯ظ„غŒظ„ ظ…ط­ط¯ظˆط¯غŒطھ غŒع©طھط§غŒغŒ (revision, parent, sequence) ط§ط² ط±ظˆط´ ط¯ظˆ ظ…ط±ط­ظ„ظ‡â€Œط§غŒ
        (ط¢ظپط³طھ ظ…ظˆظ‚طھ ط³ظ¾ط³ ظ…ظ‚ط¯ط§ط± ظ†ظ‡ط§غŒغŒ) ط§ط³طھظپط§ط¯ظ‡ ظ…غŒâ€Œط´ظˆط¯ طھط§ طھط¯ط§ط®ظ„ ظ¾غŒط´ ظ†غŒط§غŒط¯.
        """
        revision_id = request.data.get('revisionId')
        ordered_ids = request.data.get('orderedIds', [])

        if not revision_id or not ordered_ids:
            return Response(
                {"detail": "revisionId ظˆ orderedIds ط§ظ„ط²ط§ظ…غŒ ظ‡ط³طھظ†ط¯."},
                status=status.HTTP_400_BAD_REQUEST
            )

        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision, request.user)

        # ظ†ع¯ط§ط´طھ node.id â†’ pk ظ†ط³ط®ظ‡ WBS ط¯ط± ط§غŒظ† ط±غŒظˆغŒعکظ†
        pk_map = {
            str(v.node_id): v.pk
            for v in WBSNodeVersion.objects.filter(revision=revision, node_id__in=ordered_ids)
        }

        # ظ†ع©طھظ‡ ظ…ظ‡ظ…: ط§ط² .update() ط§ط³طھظپط§ط¯ظ‡ ظ…غŒâ€Œع©ظ†غŒظ… ظ†ظ‡ .save()
        # ع†ظˆظ† ظ…ط¯ظ„ MPTT ط¨ط§ order_insertion_by=['sequence'] ط§ط³طھ ظˆ save() ط¨ط§ط¹ط«
        # ط¬ط§ط¨ط¬ط§غŒغŒ ظ†ظˆط¯ ط¯ط± ط¯ط±ط®طھ ظˆ ط®ط·ط§غŒ _make_sibling_of_root_node ظ…غŒâ€Œط´ظˆط¯.
        # .update() ظپظ‚ط· ط³طھظˆظ† sequence ط±ط§ ط¢ظ¾ط¯غŒطھ ظ…غŒâ€Œع©ظ†ط¯ ظˆ ط¨ظ‡ ط³ط§ط®طھط§ط± ط¯ط±ط®طھ ع©ط§ط±غŒ ظ†ط¯ط§ط±ط¯.

        # ظ…ط±ط­ظ„ظ‡ غ±: ط¢ظپط³طھ ظ…ظˆظ‚طھ ط¨ط±ط§غŒ ط¯ظˆط± ط²ط¯ظ† ظ…ط­ط¯ظˆط¯غŒطھ غŒع©طھط§غŒغŒ (revision, parent, sequence)
        for i, nid in enumerate(ordered_ids):
            pk = pk_map.get(str(nid))
            if pk:
                WBSNodeVersion.objects.filter(pk=pk).update(sequence=100000 + i)

        # ظ…ط±ط­ظ„ظ‡ غ²: ظ…ظ‚ط§ط¯غŒط± ظ†ظ‡ط§غŒغŒ غ±..N
        for i, nid in enumerate(ordered_ids):
            pk = pk_map.get(str(nid))
            if pk:
                WBSNodeVersion.objects.filter(pk=pk).update(sequence=i + 1)

        return Response({"detail": "طھط±طھغŒط¨ ظ†ظˆط¯ظ‡ط§غŒ WBS ط¨ظ‡â€Œط±ظˆط²ط±ط³ط§ظ†غŒ ط´ط¯."}, status=status.HTTP_200_OK)


    @action(detail=False, methods=['post'], url_path='reorder-mixed')
    @transaction.atomic
    def reorder_mixed(self, request):
        revision_id = request.data.get('revisionId')
        ordered_items = request.data.get('orderedItems', [])
        if not revision_id or not ordered_items:
            return Response(
                {"detail": "revisionId and orderedItems are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision, request.user)

        if any(
            not isinstance(item, dict)
            or item.get('type') not in {'wbs', 'activity'}
            or not item.get('id')
            for item in ordered_items
        ):
            raise ValidationError({"orderedItems": "Each row needs a valid id and type."})

        wbs_ids = [item.get('id') for item in ordered_items if item.get('type') == 'wbs']
        task_ids = [item.get('id') for item in ordered_items if item.get('type') == 'activity']
        wbs_map = {
            str(item.node_id): item
            for item in WBSNodeVersion.objects.filter(
                revision=revision, node_id__in=wbs_ids, is_deleted=False
            )
        }
        task_map = {
            str(item.task_id): item
            for item in TaskVersion.objects.filter(
                revision=revision, task_id__in=task_ids, is_deleted=False
            )
        }

        if len(wbs_map) != len(wbs_ids) or len(task_map) != len(task_ids):
            raise ValidationError({"orderedItems": "One or more rows do not belong to this revision."})

        parent_ids = {
            str(item.parent.node_id) if item.parent_id else None
            for item in wbs_map.values()
        } | {
            str(item.wbs_node.node_id)
            for item in task_map.values()
        }
        if len(parent_ids) != 1:
            raise ValidationError({"orderedItems": "All rows must have the same WBS parent."})

        # Move WBS values outside their unique range before assigning shared
        # positions. Task sequences have no sibling uniqueness constraint.
        for index, item in enumerate(wbs_map.values()):
            WBSNodeVersion.objects.filter(pk=item.pk).update(sequence=100000 + index)

        for sequence, row in enumerate(ordered_items, start=1):
            row_id = str(row.get('id'))
            if row.get('type') == 'wbs':
                WBSNodeVersion.objects.filter(pk=wbs_map[row_id].pk).update(sequence=sequence)
            else:
                TaskVersion.objects.filter(pk=task_map[row_id].pk).update(sequence=sequence)

        return Response({"detail": "Mixed WBS/task order updated."})

class ActivityNodeViewSet(viewsets.ModelViewSet):
    queryset = TaskVersion.objects.filter(is_deleted=False).select_related('metrics','actual')
    serializer_class = ActivityNodeSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'task__id'

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_value = self.kwargs[self.lookup_field]

        revision_id = self.request.query_params.get('revision_id')

        filter_kwargs = {self.lookup_field: lookup_value}
        if revision_id:
            filter_kwargs['revision_id'] = revision_id
        else:
            filter_kwargs['revision_id'] = F('task__project__working_revision_id')

        # ط§ظ†طھط®ط§ط¨ ط¯ظ‚غŒظ‚ ظ‡ظ…ط§ظ† ط±ط¯غŒظپغŒ ع©ظ‡ ظ…طھط¹ظ„ظ‚ ط¨ظ‡ ظ†ط³ط®ظ‡ ط¨ط§ط² ط§ط³طھ
        obj = queryset.filter(**filter_kwargs).first()

        if not obj:
            from django.http import Http404
            raise Http404("طھط³ع© ظ…ظˆط±ط¯ ظ†ط¸ط± ط¯ط± ظ†ط³ط®ظ‡ ظپط¹ط§ظ„ غŒط§ظپطھ ظ†ط´ط¯.")

        self.check_object_permissions(self.request, obj)
        return obj

    def get_queryset(self):
        queryset = super().get_queryset()
        project_ids = accessible_project_ids(self.request.user)
        queryset = queryset.filter(revision__project_id__in=project_ids)
        revision_id = self.request.query_params.get('revision_id')
        user_id = self.request.query_params.get('user_id')  # <--- ظپغŒظ„طھط± ط¬ط¯غŒط¯
        search = (self.request.query_params.get('search') or '').strip()

        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        else:
            queryset = queryset.filter(revision_id__in=official_revision_ids(project_ids, ROLE_WORKING))

        # ظپغŒظ„طھط± ع©ط±ط¯ظ† طھط³ع©â€Œظ‡ط§غŒغŒ ع©ظ‡ ط§غŒظ† ع©ط§ط±ط¨ط± ط¯ط± ط¢ظ†â€Œظ‡ط§ ظ†ظ‚ط´ ط¯ط§ط±ط¯
        if user_id:
            queryset = queryset.filter(task__roles__user_id=user_id).distinct()
        if search:
            queryset = queryset.filter(Q(title__icontains=search) | Q(wbs_node__wbs_code__icontains=search)).distinct()

        return queryset.order_by('sequence')

    def _attach_schedule_quality(self, task_versions):
        items = list(task_versions)
        if not items:
            return items

        revision_ids = {item.revision_id for item in items}
        successor_sources = defaultdict(set)
        for revision_id, predecessor_id in Dependency.objects.filter(
            revision_id__in=revision_ids
        ).values_list('revision_id', 'predecessor_id'):
            successor_sources[revision_id].add(str(predecessor_id))

        finish_by_revision = {}
        for revision_id, finish in TaskVersion.objects.filter(
            revision_id__in=revision_ids,
            is_deleted=False,
            planned_finish__isnull=False,
        ).values('revision_id').annotate(project_finish=Max('planned_finish')).values_list('revision_id', 'project_finish'):
            finish_by_revision[revision_id] = finish

        for item in items:
            actual = getattr(item, 'actual', None)
            progress = float(actual.progress or 0) if actual else 0
            is_completed = bool(actual and (actual.actual_finish is not None or progress >= 100))
            has_successor = str(item.task_id) in successor_sources.get(item.revision_id, set())
            project_finish = finish_by_revision.get(item.revision_id)
            title_text = (item.title or '').strip().lower()
            is_terminal_finish = bool(
                (not has_successor) and item.planned_finish and project_finish and item.planned_finish == project_finish and
                any(marker in title_text for marker in ('خاتمه', 'پایان', 'finish', 'completion', 'closeout', 'close out'))
            )
            is_open_end = (not has_successor) and (not is_completed) and (not is_terminal_finish)
            defines_project_finish = bool(
                is_open_end and item.planned_finish and project_finish and item.planned_finish == project_finish
            )
            if is_terminal_finish:
                severity = 'ok'
                message = 'Recognized project finish terminal.'
            elif defines_project_finish:
                severity = 'warning'
                message = 'Open-end activity defines the project finish; connect this path to the final milestone.'
            elif is_open_end:
                severity = 'warning'
                message = 'Open-end activity; this path is not connected to a successor/final milestone.'
            elif is_completed:
                severity = 'done'
                message = 'Actualized task; not counted as remaining critical work.'
            else:
                severity = 'ok'
                message = ''
            item._schedule_quality = {
                'isCompleted': is_completed,
                'isOpenEnd': is_open_end,
                'definesProjectFinish': defines_project_finish,
                'severity': severity,
                'message': message,
            }
        return items

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        display_queryset = queryset

        page_param = request.query_params.get('page')
        page_size_param = (
                request.query_params.get('pageSize')
                or request.query_params.get('page_size')
        )

        if not page_param and not page_size_param:
            display_items = self._attach_schedule_quality(display_queryset)
            serializer = self.get_serializer(display_items, many=True)
            return Response(serializer.data)

        try:
            page = max(int(page_param or 1), 1)
            page_size = min(max(int(page_size_param or 50), 1), 100)
        except (TypeError, ValueError):
            page, page_size = 1, 50

        total = display_queryset.count()
        start = (page - 1) * page_size

        page_items = self._attach_schedule_quality(
            display_queryset[start:start + page_size]
        )
        serializer = self.get_serializer(page_items, many=True)

        return Response({
            'results': serializer.data,
            'page': page,
            'pageSize': page_size,
            'total': total,
            'hasNext': start + len(serializer.data) < total,
        })


    def perform_create(self, serializer):
        revision_id = self.request.data.get('revision_id')
        print(self.request.data)
        print(revision_id)
        if not revision_id:
            raise ValidationError({"revision_id": "ط¢غŒط¯غŒ ظ†ط³ط®ظ‡ ط¨ط±ط§غŒ ط³ط§ط®طھ طھط³ع© ط§ظ„ط²ط§ظ…غŒ ط§ط³طھ."})

        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision, self.request.user)

        # طھط³ع© ط¨ط§غŒط¯ ط­طھظ…ط§ ط¨ظ‡ غŒع© WBS ظ…طھطµظ„ ط´ظˆط¯
        parent_id = self.request.data.get('parentId')
        if not parent_id:
            raise ValidationError({"parentId": "ظ…ط´ط®طµ ع©ط±ط¯ظ† ع¯ط±ظ‡ ظˆط§ظ„ط¯ (WBS) ط¨ط±ط§غŒ ط³ط§ط®طھ طھط³ع© ط§ظ„ط²ط§ظ…غŒ ط§ط³طھ."})

        wbs_node = get_object_or_404(WBSNodeVersion, node_id=parent_id, revision=revision)

        # طھط®طµغŒطµ sequence ط¨ط± ط§ط³ط§ط³ طھط±طھغŒط¨ ط³ط§ط®طھ (ط¢ط®ط±غŒظ† + غ±) ط¯ط± ظ‡ظ…ط§ظ† ع¯ط±ظ‡ WBS
        # طھط§ طھط±طھغŒط¨ ظ¾غŒط´â€Œظپط±ط¶ ظ†ظ…ط§غŒط´طŒ طھط±طھغŒط¨ ط§غŒط¬ط§ط¯ طھط³ع©â€Œظ‡ط§ ط¨ط§ط´ط¯
        max_task_seq = TaskVersion.objects.filter(
            revision=revision, wbs_node=wbs_node, is_deleted=False
        ).aggregate(Max('sequence'))['sequence__max'] or 0
        max_wbs_seq = WBSNodeVersion.objects.filter(
            revision=revision, parent=wbs_node, is_deleted=False
        ).aggregate(Max('sequence'))['sequence__max'] or 0
        max_seq = max(max_task_seq, max_wbs_seq)

        base_task = Task.objects.create(project=revision.project)
        serializer.save(task=base_task, revision=revision, wbs_node=wbs_node, sequence=max_seq + 1)

    def perform_update(self, serializer):
        check_revision_is_open(serializer.instance.revision, self.request.user)
        serializer.save()

    def perform_destroy(self, instance):
        check_revision_is_open(instance.revision, self.request.user)
        instance.is_deleted = True
        instance.save()


class DependencyViewSet(viewsets.ModelViewSet):
    queryset = Dependency.objects.all()
    serializer_class = DependencySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(revision__project_id__in=accessible_project_ids(self.request.user))
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        return queryset

    def perform_create(self, serializer):
        # ط§ط¶ط§ظپظ‡ ع©ط±ط¯ظ† ع†ع© ط¨ط§ط² ط¨ظˆط¯ظ† ظ†ط³ط®ظ‡ ظ‡ظ†ع¯ط§ظ… ط§غŒط¬ط§ط¯ غŒع© Dependency
        revision_id = self.request.data.get('revisionId')
        if not revision_id:
            raise ValidationError({"revisionId": "ط¢غŒط¯غŒ ظ†ط³ط®ظ‡ ط§ظ„ط²ط§ظ…غŒ ط§ط³طھ."})
        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision, self.request.user)
        predecessor_id = serializer.validated_data.get('predecessor_id')
        successor_id = serializer.validated_data.get('successor_id')
        cycle = find_task_dependency_cycle(revision, extra_edge=(predecessor_id, successor_id))
        if cycle:
            raise ValidationError({
                "detail": "This dependency creates a cycle in the CPM graph.",
                "cycle": serialize_dependency_cycle(revision, cycle),
            })
        serializer.save(revision=revision)

    def perform_update(self, serializer):
        check_revision_is_open(serializer.instance.revision, self.request.user)
        predecessor_id = serializer.validated_data.get('predecessor_id', serializer.instance.predecessor_id)
        successor_id = serializer.validated_data.get('successor_id', serializer.instance.successor_id)
        cycle = find_task_dependency_cycle(
            serializer.instance.revision,
            extra_edge=(predecessor_id, successor_id),
            exclude_dependency_id=serializer.instance.id,
        )
        if cycle:
            raise ValidationError({
                "detail": "This dependency creates a cycle in the CPM graph.",
                "cycle": serialize_dependency_cycle(serializer.instance.revision, cycle),
            })
        serializer.save()

    def perform_destroy(self, instance):
        check_revision_is_open(instance.revision, self.request.user)
        instance.delete()  # ظˆط§ط¨ط³طھع¯غŒâ€Œظ‡ط§ ظ…غŒâ€Œطھظˆط§ظ†ظ†ط¯ ظپغŒط²غŒع©غŒ ط­ط°ظپ ط´ظˆظ†ط¯


class SubprojectDependencyViewSet(viewsets.ModelViewSet):
    queryset = SubprojectDependency.objects.all()
    serializer_class = SubprojectDependencySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(revision__project_id__in=accessible_project_ids(self.request.user))
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        return queryset

    def perform_create(self, serializer):
        revision = serializer.validated_data.get('revision')
        if not revision:
            raise ValidationError({"revisionId": "Revision id is required."})
        check_revision_is_open(revision, self.request.user)
        task = serializer.validated_data.get('task')
        subproject = serializer.validated_data.get('subproject')
        direction = serializer.validated_data.get('direction')
        subproject_node_id = subproject_virtual_id(subproject.id)
        extra_edge = (
            (str(task.id), subproject_node_id)
            if direction == SubprojectDependency.DIRECTION_TASK_TO_SUBPROJECT
            else (subproject_node_id, str(task.id))
        )
        cycle = find_task_dependency_cycle(revision, extra_edge=extra_edge)
        if cycle:
            raise ValidationError({
                "detail": "This dependency creates a cycle in the CPM graph.",
                "cycle": serialize_dependency_cycle(revision, cycle),
            })
        serializer.save()

    def perform_update(self, serializer):
        check_revision_is_open(serializer.instance.revision, self.request.user)
        task = serializer.validated_data.get('task', serializer.instance.task)
        subproject = serializer.validated_data.get('subproject', serializer.instance.subproject)
        direction = serializer.validated_data.get('direction', serializer.instance.direction)
        subproject_node_id = subproject_virtual_id(subproject.id)
        extra_edge = (
            (str(task.id), subproject_node_id)
            if direction == SubprojectDependency.DIRECTION_TASK_TO_SUBPROJECT
            else (subproject_node_id, str(task.id))
        )
        cycle = find_task_dependency_cycle(
            serializer.instance.revision,
            extra_edge=extra_edge,
            exclude_subproject_dependency_id=serializer.instance.id,
        )
        if cycle:
            raise ValidationError({
                "detail": "This dependency creates a cycle in the CPM graph.",
                "cycle": serialize_dependency_cycle(serializer.instance.revision, cycle),
            })
        serializer.save()

    def perform_destroy(self, instance):
        check_revision_is_open(instance.revision, self.request.user)
        instance.delete()


class TaskReportLogViewSet(viewsets.ModelViewSet):
    queryset = TaskReportLog.objects.all()
    serializer_class = TaskReportLogSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(task__project_id__in=accessible_project_ids(self.request.user))
        task_id = self.request.query_params.get('task_id')
        for_approval = self.request.query_params.get('for_approval')
        official_working_ids = official_revision_ids(accessible_project_ids(self.request.user), ROLE_WORKING)

        if task_id:
            queryset = queryset.filter(task_id=task_id)

        if for_approval == 'true':
            user = self.request.user
            from .models import SystemSettings
            from .permissions import is_planning_manager as _is_pm
            from django.db.models import Q

            # طµظپ ط¨ط±ط±ط³غŒâ€Œع©ظ†ظ†ط¯ظ‡: ع¯ط²ط§ط±ط´â€Œظ‡ط§غŒغŒ ط¨ط§ ظˆط¶ط¹غŒطھ pending ع©ظ‡ ع©ط§ط±ط¨ط± ط±ظˆغŒ طھط³ع©ط´ط§ظ† reviewer/PM ط§ط³طھ
            reviewer_q = Q(
                approval_status='pending',
                task__roles__user=user,
                task__roles__revision_id__in=official_working_ids,
                task__roles__role__in=['reviewer', 'project manager'],
            )
            # طµظپ ظ…ط¯غŒط± ط¨ط±ظ†ط§ظ…ظ‡â€Œط±غŒط²غŒ: ع¯ط²ط§ط±ط´â€Œظ‡ط§غŒ reviewer_approved ط§ط² ظ¾ط±ظˆعکظ‡â€Œظ‡ط§غŒ ط´ط±ع©طھغŒ
            planning_q = Q(
                approval_status='reviewer_approved',
                task__project__scope='company',
            ) if _is_pm(user) or is_company_level(user) else Q(pk=None)  # empty

            queryset = queryset.filter(reviewer_q | planning_q).distinct()

        return queryset.select_related('task', 'task__project', 'user').prefetch_related('attachments')

    def perform_create(self, serializer):
        uploaded_files = self.request.FILES.getlist('attachments')
        for uploaded_file in uploaded_files:
            try:
                validate_chat_file(uploaded_file)
            except DjangoValidationError as exc:
                raise ValidationError({'attachments': exc.messages})

        report = serializer.save(user=self.request.user)
        for uploaded_file in uploaded_files:
            TaskReportAttachment.objects.create(
                report=report,
                file=uploaded_file,
                file_name=uploaded_file.name,
                file_type=getattr(uploaded_file, 'content_type', '') or '',
                file_size=getattr(uploaded_file, 'size', 0) or 0,
            )

    def perform_update(self, serializer):
        report = serializer.instance
        # ط¬ظ„ظˆع¯غŒط±غŒ ط§ط² ظˆغŒط±ط§غŒط´ ظ¾ط³ ط§ط² طھط§غŒغŒط¯ (ظ‡ط± ظ…ط±ط­ظ„ظ‡)
        if report.approval_status != 'pending':
            raise PermissionDenied("ط§غŒظ† ع¯ط²ط§ط±ط´ ط¯ط± ط­ط§ظ„ ط¨ط±ط±ط³غŒ غŒط§ طھط§غŒغŒط¯ ط´ط¯ظ‡ ظˆ ط¯غŒع¯ط± ظ‚ط§ط¨ظ„ ظˆغŒط±ط§غŒط´ ظ†غŒط³طھ.")
        serializer.save()

    @action(detail=True, methods=['get'], url_path=r'attachments/(?P<attachment_id>[^/.]+)/download')
    def download_attachment(self, request, pk=None, attachment_id=None):
        report = self.get_object()
        attachment = get_object_or_404(report.attachments, pk=attachment_id)
        response = FileResponse(
            attachment.file.open('rb'),
            as_attachment=True,
            filename=attachment.file_name or attachment.file.name,
        )
        if attachment.file_type:
            response['Content-Type'] = attachment.file_type
        return response

    @action(detail=True, methods=['post'], url_path='approve', url_name='approve')
    def approve_report(self, request, pk=None):
        """
        طھط§غŒغŒط¯ظگ ع¯ط²ط§ط±ط´ (ط¯ظˆâ€Œظ…ط±ط­ظ„ظ‡â€Œط§غŒ):
        - ظ…ط±ط­ظ„ظ‡ظ” غ±: ط¨ط±ط±ط³غŒâ€Œع©ظ†ظ†ط¯ظ‡ (reviewer / project manager ط±ظˆغŒ طھط³ع©) â†’ reviewer_approved
          ط¨ط±ط§غŒ ظ¾ط±ظˆعکظ‡ظ” ط¯ط±ظˆظ†â€Œظˆط§ط­ط¯غŒ: auto-collapse ط¨ظ‡ final_approved.
        - ظ…ط±ط­ظ„ظ‡ظ” غ²: ظ…ط¯غŒط±ظگ ط¨ط±ظ†ط§ظ…ظ‡â€Œط±غŒط²غŒ (غŒط§ company-level) â†’ final_approved (ظپظ‚ط· ط´ط±ع©طھغŒ).
        - Bypass: ط§ع¯ط± SystemSettings.allow_planning_manager_bypass_reviewer ظپط¹ط§ظ„ ط¨ط§ط´ط¯طŒ
          ظ…ط¯غŒط±ظگ ط¨ط±ظ†ط§ظ…ظ‡â€Œط±غŒط²غŒ ظ…غŒâ€Œطھظˆط§ظ†ط¯ ظ…ط³طھظ‚غŒظ…ط§ظ‹ ط§ط² pending ط¨ظ‡ final_approved ط¨ط¨ط±ط¯.
        ظ¾غŒط´ط±ظپطھ ط¯ط± TaskActual ظپظ‚ط· ظ‡ظ†ع¯ط§ظ…ظگ final_approved ط«ط¨طھ ظ…غŒâ€Œط´ظˆط¯.
        """
        from .models import SystemSettings
        from .permissions import is_planning_manager as _is_pm

        report = self.get_object()
        user = request.user
        project = report.task.project
        now = timezone.now()

        if report.approval_status == 'final_approved':
            return Response({"detail": "ط§غŒظ† ع¯ط²ط§ط±ط´ ظ‚ط¨ظ„ط§ظ‹ طھط§غŒغŒط¯ ظ†ظ‡ط§غŒغŒ ط´ط¯ظ‡ ط§ط³طھ."}, status=status.HTTP_400_BAD_REQUEST)
        if report.approval_status == 'rejected':
            return Response({"detail": "ط§غŒظ† ع¯ط²ط§ط±ط´ ط±ط¯ ط´ط¯ظ‡ ظˆ ظ‚ط§ط¨ظ„ظگ طھط§غŒغŒط¯ ظ†غŒط³طھ."}, status=status.HTTP_400_BAD_REQUEST)

        # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ Bypass path â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if (report.approval_status == 'pending'
                and project.scope == 'company'
                and (_is_pm(user) or is_company_level(user))
                and SystemSettings.current().allow_planning_manager_bypass_reviewer):
            report.approval_status = 'final_approved'
            report.reviewer_approved_by = user
            report.reviewer_approved_at = now
            report.final_approved_by = user
            report.final_approved_at = now
            # ط³ط§ط²ع¯ط§ط±غŒ legacy
            report.is_approved = True
            report.approved_by = user
            report.approved_at = now
            report.save()
            self._commit_progress(report, user)
            return Response({
                "detail": "ع¯ط²ط§ط±ط´ ط¨ط§ bypass ظ…ط³طھظ‚غŒظ…ط§ظ‹ طھط§غŒغŒط¯ ظ†ظ‡ط§غŒغŒ ط´ط¯.",
                "approvalStatus": "final_approved",
                "viaBypass": True,
            }, status=status.HTTP_200_OK)

        # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ ظ…ط±ط­ظ„ظ‡ظ” غ±: طھط§غŒغŒط¯ظگ ط¨ط±ط±ط³غŒâ€Œع©ظ†ظ†ط¯ظ‡ â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if report.approval_status == 'pending':
            is_reviewer = TaskRole.objects.filter(
                task=report.task, user=user,
                role__in=['reviewer', 'project manager']
            ).exists()
            if not (is_reviewer or is_company_level(user)):
                raise PermissionDenied("ظپظ‚ط· ط¨ط±ط±ط³غŒâ€Œع©ظ†ظ†ط¯ظ‡ظ” طھط³ع© ظ…غŒâ€Œطھظˆط§ظ†ط¯ طھط§غŒغŒط¯ظگ ظ…ط±ط­ظ„ظ‡ظ” ط§ظˆظ„ ط¨ط¯ظ‡ط¯.")

            report.reviewer_approved_by = user
            report.reviewer_approved_at = now

            # ط¯ط±ظˆظ†â€Œظˆط§ط­ط¯غŒ â†’ auto-collapse: ظ‡ظ…غŒظ† ظ…ط±ط­ظ„ظ‡ ظ†ظ‡ط§غŒغŒ ط§ط³طھ
            if project.scope == 'intra_unit':
                report.approval_status = 'final_approved'
                report.final_approved_by = user
                report.final_approved_at = now
                report.is_approved = True
                report.approved_by = user
                report.approved_at = now
                report.save()
                self._commit_progress(report, user)
                return Response({
                    "detail": "ع¯ط²ط§ط±ط´ طھط§غŒغŒط¯ ط´ط¯ ظˆ ظ¾غŒط´ط±ظپطھ طھط³ع© ط¨ظ‡â€Œط±ظˆط²ط±ط³ط§ظ†غŒ ع¯ط±ط¯غŒط¯.",
                    "approvalStatus": "final_approved",
                }, status=status.HTTP_200_OK)
            else:
                # ط´ط±ع©طھغŒ â†’ ظ…ظ†طھط¸ط±ظگ طھط§غŒغŒط¯ظگ ظ†ظ‡ط§غŒغŒظگ ظ…ط¯غŒط±ظگ ط¨ط±ظ†ط§ظ…ظ‡â€Œط±غŒط²غŒ
                report.approval_status = 'reviewer_approved'
                report.save()
                return Response({
                    "detail": "ع¯ط²ط§ط±ط´ طھظˆط³ط· ط¨ط±ط±ط³غŒâ€Œع©ظ†ظ†ط¯ظ‡ طھط§غŒغŒط¯ ط´ط¯. ط¯ط± ط§ظ†طھط¸ط§ط± طھط§غŒغŒط¯ ظ†ظ‡ط§غŒغŒ ظ…ط¯غŒط± ط¨ط±ظ†ط§ظ…ظ‡â€Œط±غŒط²غŒ.",
                    "approvalStatus": "reviewer_approved",
                }, status=status.HTTP_200_OK)

        # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ ظ…ط±ط­ظ„ظ‡ظ” غ²: طھط§غŒغŒط¯ ظ†ظ‡ط§غŒغŒظگ ظ…ط¯غŒط± ط¨ط±ظ†ط§ظ…ظ‡â€Œط±غŒط²غŒ â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        elif report.approval_status == 'reviewer_approved':
            if project.scope != 'company':
                return Response(
                    {"detail": "ط§غŒظ† ظ¾ط±ظˆعکظ‡ ط¯ط±ظˆظ†â€Œظˆط§ط­ط¯غŒ ط§ط³طھ ظˆ ظ†غŒط§ط²غŒ ط¨ظ‡ طھط§غŒغŒط¯ ظ†ظ‡ط§غŒغŒ ط¬ط¯ط§ع¯ط§ظ†ظ‡ ظ†ط¯ط§ط±ط¯."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if not (_is_pm(user) or is_company_level(user)):
                raise PermissionDenied(
                    "طھط§غŒغŒط¯ظگ ظ†ظ‡ط§غŒغŒظگ ع¯ط²ط§ط±ط´â€Œظ‡ط§غŒ ظ¾ط±ظˆعکظ‡â€Œظ‡ط§غŒ ط´ط±ع©طھغŒ ظپظ‚ط· طھظˆط³ط· ظ…ط¯غŒط±ظگ ظˆط§ط­ط¯ظگ ط¨ط±ظ†ط§ظ…ظ‡â€Œط±غŒط²غŒ ظ…ط¬ط§ط² ط§ط³طھ."
                )
            report.approval_status = 'final_approved'
            report.final_approved_by = user
            report.final_approved_at = now
            report.is_approved = True
            report.approved_by = user
            report.approved_at = now
            report.save()
            self._commit_progress(report, user)
            return Response({
                "detail": "ع¯ط²ط§ط±ط´ طھط§غŒغŒط¯ ظ†ظ‡ط§غŒغŒ ط´ط¯ ظˆ ظ¾غŒط´ط±ظپطھ طھط³ع© ط¨ظ‡â€Œط±ظˆط²ط±ط³ط§ظ†غŒ ع¯ط±ط¯غŒط¯.",
                "approvalStatus": "final_approved",
            }, status=status.HTTP_200_OK)

        return Response({"detail": "ظˆط¶ط¹غŒطھ ظ†ط§ظ…ط¹طھط¨ط±."}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='reject', url_name='reject')
    def reject_report(self, request, pk=None):
        """ط±ط¯ظگ ع¯ط²ط§ط±ط´ طھظˆط³ط· ط¨ط±ط±ط³غŒâ€Œع©ظ†ظ†ط¯ظ‡ غŒط§ ظ…ط¯غŒط± ط¨ط±ظ†ط§ظ…ظ‡â€Œط±غŒط²غŒ."""
        report = self.get_object()
        user = request.user

        if report.approval_status == 'final_approved':
            return Response({"detail": "ط§غŒظ† ع¯ط²ط§ط±ط´ ظ‚ط¨ظ„ط§ظ‹ طھط§غŒغŒط¯ ظ†ظ‡ط§غŒغŒ ط´ط¯ظ‡ ظˆ ظ‚ط§ط¨ظ„ظگ ط±ط¯ ظ†غŒط³طھ."}, status=status.HTTP_400_BAD_REQUEST)

        reason = request.data.get('reason', '').strip()

        is_reviewer = TaskRole.objects.filter(
            task=report.task, user=user,
            role__in=['reviewer', 'project manager']
        ).exists()
        from .permissions import is_planning_manager as _is_pm
        if not (is_reviewer or _is_pm(user) or is_company_level(user)):
            raise PermissionDenied("ط´ظ…ط§ ط§ط¬ط§ط²ظ‡ظ” ط±ط¯ ع©ط±ط¯ظ† ط§غŒظ† ع¯ط²ط§ط±ط´ ط±ط§ ظ†ط¯ط§ط±غŒط¯.")

        report.approval_status = 'rejected'
        if reason:
            report.notes = f"REJECTED: {reason}\n---\n{report.notes}"
        report.save()
        return Response({"detail": "ع¯ط²ط§ط±ط´ ط±ط¯ ط´ط¯.", "approvalStatus": "rejected"}, status=status.HTTP_200_OK)

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ Helper: ط«ط¨طھظگ ظ¾غŒط´ط±ظپطھ ط¯ط± TaskActual â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def _commit_progress(self, report, user):
        """ط«ط¨طھظگ ظ¾غŒط´ط±ظپطھ ظپظ‚ط· ظ‡ظ†ع¯ط§ظ…ظگ final_approved â€” ظپط±ط§ط®ظˆط§ظ†غŒ ط®ط§ط±ط¬ ط§ط² ط§غŒظ† ط­ط§ظ„طھ ظ…ط¬ط§ط² ظ†غŒط³طھ."""
        if report.approval_status != 'final_approved':
            raise ValidationError({'approval_status': 'Only final approved reports can commit task progress.'})
        if report.progress_percent and report.progress_percent > 0:
            validate_task_start(report.task)
        validate_progress_transition(report.task, report.progress_percent)
        if report.progress_percent >= 100:
            validate_task_delivery(report.task)

        execution_revision = get_official_revision(
            report.task.project, ROLE_EXECUTION, required=True
        )
        active_task_version = TaskVersion.objects.filter(
            task=report.task,
            revision=execution_revision,
            is_deleted=False,
        ).first()
        if not active_task_version:
            raise ValidationError({
                "task": "Task does not exist in the project's official execution revision."
            })
        task_actual, _ = TaskActual.objects.get_or_create(
            task_version=active_task_version,
            defaults={'updated_by': user}
        )
        task_actual.progress = report.progress_percent
        if report.progress_percent <= 0:
            # Progress 0 resets actual execution state.
            task_actual.actual_start = None
            task_actual.actual_finish = None
            task_actual.updated_by = user
            task_actual.save()
            for plan in TaskFinancialPlan.objects.filter(task=report.task).exclude(status=TaskFinancialPlan.STATUS_CANCELLED):
                refresh_plan_cost_allocations(plan, as_of_date=report.timestamp)
            return

        # ظ…ط­ط§ط³ط¨ظ‡ ط®ظˆط¯ع©ط§ط± actual_start/finish
        approved_reports = TaskReportLog.objects.filter(
            task=report.task, approval_status='final_approved'
        ).order_by('timestamp')

        if task_actual.actual_start is None:
            first_progress = approved_reports.filter(progress_percent__gt=0).first()
            if first_progress:
                task_actual.actual_start = first_progress.timestamp

        if task_actual.actual_finish is None:
            completion = approved_reports.filter(progress_percent__gte=100).first()
            if completion:
                task_actual.actual_finish = completion.timestamp

        task_actual.updated_by = user
        task_actual.save()
        for plan in TaskFinancialPlan.objects.filter(task=report.task).exclude(status=TaskFinancialPlan.STATUS_CANCELLED):
            refresh_plan_cost_allocations(plan, as_of_date=report.timestamp)
class TaskChatMessageViewSet(viewsets.ModelViewSet):
    queryset = TaskChatMessage.objects.all()
    serializer_class = TaskChatMessageSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(task__project_id__in=accessible_project_ids(self.request.user))
        task_id = self.request.query_params.get('task_id')
        if task_id:
            queryset = queryset.filter(task_id=task_id)
        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class TaskRoleViewSet(viewsets.ModelViewSet):
    """ظ…ط¯غŒط±غŒطھ ظ†ظ‚ط´â€Œظ‡ط§غŒ طھط®طµغŒطµ ط¯ط§ط¯ظ‡ ط´ط¯ظ‡ ط¨ظ‡ طھط³ع©â€Œظ‡ط§ (Task Roles)"""
    queryset = TaskRole.objects.all()
    serializer_class = TaskRoleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        project_ids = accessible_project_ids(self.request.user)
        queryset = queryset.filter(revision__project_id__in=project_ids)

        # ط§ظ…ع©ط§ظ† ظپغŒظ„طھط± ع©ط±ط¯ظ† ط¯غŒطھط§غŒ ط¨ط±ع¯ط´طھغŒ
        revision_id = self.request.query_params.get('revision_id')
        task_id = self.request.query_params.get('taskId')
        user_id = self.request.query_params.get('userId') or self.request.query_params.get('user_id')

        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        else:
            queryset = queryset.filter(revision_id__in=official_revision_ids(project_ids, ROLE_WORKING))
        if task_id:
            queryset = queryset.filter(task_id=task_id)
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        return queryset

    def perform_create(self, serializer):
        from .permissions import require_can_assign_task_role
        task = serializer.validated_data.get('task')
        target_user = serializer.validated_data.get('user')
        role = serializer.validated_data.get('role')
        require_can_assign_task_role(self.request.user, task, target_user, role)
        serializer.save()

    def perform_destroy(self, instance):
        from .permissions import require_can_assign_task_role
        # ط­ط°ظپ ظ†ظ‚ط´ ظ‡ظ… ط¨ط§ ظ‡ظ…ط§ظ† ظ…ظ†ط·ظ‚ ظ†ظ‚ط´ (ظپظ‚ط· ع©ط³غŒ ع©ظ‡ ظ…غŒâ€Œطھظˆط§ظ†ط³طھظ‡ ط¨ط³ط§ط²ط¯ ظ…غŒâ€Œطھظˆط§ظ†ط¯ ط­ط°ظپ ع©ظ†ط¯)
        require_can_assign_task_role(self.request.user, instance.task, instance.user, instance.role)
        instance.delete()

    @action(detail=False, methods=['post'], url_path='bulk-assign-reviewer')
    @transaction.atomic
    def bulk_assign_reviewer(self, request):
        revision_id = request.data.get('revisionId') or request.data.get('revision_id')
        wbs_node_id = request.data.get('wbsNodeId') or request.data.get('wbs_node_id')
        user_id = request.data.get('userId') or request.data.get('user_id')
        replace_existing = request.data.get('replaceExisting', True)

        if not revision_id or not wbs_node_id or not user_id:
            return Response(
                {"detail": "revisionIdطŒ wbsNodeId ظˆ userId ط§ظ„ط²ط§ظ…غŒ ظ‡ط³طھظ†ط¯."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        revision = get_object_or_404(
            Revision.objects.filter(project_id__in=accessible_project_ids(request.user)),
            pk=revision_id,
        )
        check_revision_is_open(revision, request.user)

        wbs_node = get_object_or_404(
            WBSNodeVersion.objects.filter(revision=revision, is_deleted=False),
            node_id=wbs_node_id,
        )
        target_user = get_object_or_404(User, pk=user_id)

        wbs_scope = wbs_node.get_descendants(include_self=True).filter(is_deleted=False)
        task_versions = list(
            TaskVersion.objects.filter(
                revision=revision,
                is_deleted=False,
                wbs_node__in=wbs_scope,
            ).select_related('task', 'task__project')
        )

        if not task_versions:
            return Response(
                {"detail": "ط¯ط± ط§غŒظ† ظ†ظˆط¯ WBS طھط³ع©غŒ ط¨ط±ط§غŒ طھط®طµغŒطµ reviewer ظˆط¬ظˆط¯ ظ†ط¯ط§ط±ط¯."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .permissions import require_can_assign_task_role
        for task_version in task_versions:
            require_can_assign_task_role(request.user, task_version.task, target_user, 'reviewer')

        task_ids = [task_version.task_id for task_version in task_versions]
        if replace_existing in (True, 'true', 'True', '1', 1):
            TaskRole.objects.filter(
                revision=revision,
                task_id__in=task_ids,
                role='reviewer',
            ).exclude(user=target_user).delete()

        created_count = 0
        for task_version in task_versions:
            _, created = TaskRole.objects.get_or_create(
                revision=revision,
                task=task_version.task,
                user=target_user,
                role='reviewer',
            )
            if created:
                created_count += 1

        roles = TaskRole.objects.filter(revision=revision, task_id__in=task_ids)
        return Response(
            {
                "assignedTaskCount": len(task_versions),
                "createdCount": created_count,
                "roles": TaskRoleSerializer(roles, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'], url_path='bulk-assign-executor')
    @transaction.atomic
    def bulk_assign_executor(self, request):
        revision_id = request.data.get('revisionId')
        project_id = request.data.get('projectId')
        wbs_node_id = request.data.get('wbsNodeId')
        user_id = request.data.get('userId')
        if not user_id or not (revision_id or project_id):
            return Response({'detail': 'userId and revisionId/projectId are required.'}, status=status.HTTP_400_BAD_REQUEST)
        revision_qs = Revision.objects.filter(
            pk__in=official_revision_ids(accessible_project_ids(request.user), ROLE_WORKING),
            project__is_deleted=False, is_deleted=False,
        )
        revision = get_object_or_404(revision_qs, pk=revision_id) if revision_id else get_object_or_404(revision_qs, project_id=project_id)
        check_revision_is_open(revision, request.user)
        target = get_object_or_404(User, pk=user_id)
        versions = TaskVersion.objects.filter(revision=revision, is_deleted=False, wbs_node__is_deleted=False).select_related('task')
        if not is_system_admin(request.user):
            versions = versions.filter(task__roles__user=request.user, task__roles__role__in=['reviewer', 'project manager'], task__roles__revision=revision).distinct()
        if wbs_node_id:
            node = get_object_or_404(WBSNodeVersion.objects.filter(revision=revision, is_deleted=False), node_id=wbs_node_id)
            versions = versions.filter(wbs_node__in=node.get_descendants(include_self=True).filter(is_deleted=False))
        versions = list(versions)
        from .permissions import require_can_assign_task_role
        for version in versions:
            require_can_assign_task_role(request.user, version.task, target, 'executor')
        created = 0
        for version in versions:
            _, was_created = TaskRole.objects.get_or_create(revision=revision, task=version.task, user=target, role='executor')
            created += int(was_created)
        return Response({'assignedTaskCount': len(versions), 'createdCount': created}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='assignment-filter-options')
    def assignment_filter_options(self, request):
        actor = request.user
        versions = TaskVersion.objects.filter(
            is_deleted=False, wbs_node__is_deleted=False, revision__is_deleted=False,
            revision__project__is_deleted=False,
            revision_id__in=official_revision_ids(accessible_project_ids(actor), ROLE_WORKING),
        )
        if not is_system_admin(actor):
            versions = versions.filter(task__roles__user=actor, task__roles__role__in=['reviewer', 'project manager'], task__roles__revision=F('revision'))
        versions = versions.select_related('revision__project', 'wbs_node').distinct()
        projects, nodes = {}, {}
        for version in versions:
            project = version.revision.project
            projects[str(project.id)] = {'id': str(project.id), 'name': project.name, 'revisionId': version.revision_id}
            if version.wbs_node:
                nodes[str(version.wbs_node.node_id)] = {'id': str(version.wbs_node.node_id), 'projectId': str(project.id), 'code': version.wbs_node.wbs_code, 'title': version.wbs_node.title, 'revisionId': version.revision_id}
        return Response({'projects': list(projects.values()), 'nodes': list(nodes.values())})
    @action(detail=False, methods=['get'], url_path='assignable-users')
    def assignable_users(self, request):
        """
        ط§ظپط±ط§ط¯ ظ‚ط§ط¨ظ„ ط§ظ†طھط®ط§ط¨ ط¨ط±ط§غŒ غŒع© ظ†ظ‚ط´ ط±ظˆغŒ غŒع© طھط³ع© ط®ط§طµ â€” ط¨ط±ط§غŒ dropdown ظپط±ط§ظ†طھ.
        ظ¾ط§ط±ط§ظ…طھط±ظ‡ط§: ?taskId=<task_id>&role=<reviewer|executor>
        """
        from .permissions import _role as get_role, can_edit_project, is_task_reviewer
        task_id = request.query_params.get('taskId')
        role = request.query_params.get('role', 'reviewer')

        if not task_id:
            return Response({"detail": "taskId ط§ظ„ط²ط§ظ…غŒ ط§ط³طھ."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            task = Task.objects.get(pk=task_id)
        except Task.DoesNotExist:
            return Response({"detail": "طھط³ع© غŒط§ظپطھ ظ†ط´ط¯."}, status=status.HTTP_404_NOT_FOUND)

        actor = request.user
        users = User.objects.none()

        if actor.is_superuser or get_role(actor) == 'company_admin':
            users = User.objects.all()
        elif role == 'reviewer' and can_edit_project(actor, task.project):
            users = User.objects.all()
        elif role == 'executor' and is_task_reviewer(actor, task) and getattr(actor, 'unit_id', None):
            # Executor ظپظ‚ط· ط§ط² ظˆط§ط­ط¯ ظ…ط³طھظ‚غŒظ… ط®ظˆط¯ Reviewer (= actor)
            users = User.objects.filter(unit_id=actor.unit_id)

        from CustomUser.serializers import CustomUserSerializer
        return Response(CustomUserSerializer(users.order_by('id'), many=True).data)

    @action(detail=False, methods=['get'], url_path='my-reviewer-tasks')
    def my_reviewer_tasks(self, request):
        """
        ظ„غŒط³طھظگ طھظ…ط§ظ…ظگ طھط³ع©â€Œظ‡ط§غŒغŒ ع©ظ‡ ع©ط§ط±ط¨ط± ط¬ط§ط±غŒ ط±ظˆغŒ ط¢ظ†â€Œظ‡ط§ reviewer (غŒط§ project manager) ط§ط³طھ
        â€” ط¨ط±ط§غŒ طµظپط­ظ‡ظ” آ«ط§ظ†طھط®ط§ط¨ ط§ظ†ط¬ط§ظ…â€Œط¯ظ‡ظ†ط¯ظ‡آ» (Assign Executors).

        ظ¾ط§ط³ط® ط´ط§ظ…ظ„:
          - tasks: ظ„غŒط³طھ طھط³ع©â€Œظ‡ط§ ط¨ط§ ط§ط·ظ„ط§ط¹ط§طھظگ ظ¾ط±ظˆعکظ‡طŒ WBSطŒ طھط§ط±غŒط®â€Œظ‡ط§ ظˆ executors ظپط¹ظ„غŒ
          - unitMembers: ط§ط¹ط¶ط§غŒ ظˆط§ط­ط¯ظگ ع©ط§ط±ط¨ط± (ط¨ط±ط§غŒ dropdown ط§ظ†طھط®ط§ط¨ executor)
        """
        actor = request.user
        unit_id = getattr(actor, 'unit_id', None)
        is_system_admin_actor = is_system_admin(actor)

        # System admins can assign executors on every open task. Other users
        # only see tasks where they are reviewer/project manager in this revision.
        task_versions = TaskVersion.objects.filter(
            is_deleted=False,
            wbs_node__is_deleted=False,
            revision__is_deleted=False,
            revision__project__is_deleted=False,
            revision_id__in=official_revision_ids(accessible_project_ids(actor), ROLE_WORKING),
        )
        if not is_system_admin_actor:
            task_versions = task_versions.filter(
                task__roles__user=actor,
                task__roles__role__in=['reviewer', 'project manager'],
                task__roles__revision=F('revision'),
            )

        project_id = request.query_params.get('projectId')
        node_id = request.query_params.get('wbsNodeId')
        search = (request.query_params.get('search') or '').strip()
        if project_id:
            task_versions = task_versions.filter(revision__project_id=project_id)
        if node_id:
            node = WBSNodeVersion.objects.filter(
                node_id=node_id, revision_id=F('node__project__working_revision_id'), is_deleted=False,
            ).first()
            if node:
                task_versions = task_versions.filter(wbs_node__in=node.get_descendants(include_self=True).filter(is_deleted=False))
        if search:
            task_versions = task_versions.filter(Q(title__icontains=search) | Q(wbs_node__wbs_code__icontains=search) | Q(revision__project__name__icontains=search))

        task_versions = task_versions.select_related(
            'task', 'wbs_node', 'revision', 'revision__project'
        ).distinct().order_by('revision__project__name', 'sequence')

        # Return a bounded page so large task lists do not block the UI.
        try:
            page = max(int(request.query_params.get('page', 1)), 1)
            page_size = min(max(int(request.query_params.get('pageSize', 25)), 1), 100)
        except (TypeError, ValueError):
            page, page_size = 1, 25
        total_tasks = task_versions.count()
        start = (page - 1) * page_size
        task_versions = list(task_versions[start:start + page_size])

        task_ids = [tv.task_id for tv in task_versions]
        executor_roles = TaskRole.objects.filter(
            task_id__in=task_ids,
            role='executor',
        ).select_related('user')

        # ع¯ط±ظˆظ‡â€Œط¨ظ†ط¯غŒ ط¨ط± ط§ط³ط§ط³ (revision_id, task_id)
        executors_by_task = {}
        for tr in executor_roles:
            key = (tr.revision_id, tr.task_id)
            executors_by_task.setdefault(key, []).append({
                'taskRoleId': tr.id,
                'userId': tr.user_id,
                'username': tr.user.username,
                'jobTitle': getattr(tr.user, 'job_title', '') or '',
            })

        # ط³ط§ط®طھ ظ¾ط§ط³ط® ظ‡ط± طھط³ع©
        tasks_data = []
        for tv in task_versions:
            project = tv.revision.project
            tasks_data.append({
                'taskId': str(tv.task_id),
                'revisionId': tv.revision_id,
                'projectId': str(project.id),
                'projectName': project.name,
                'title': tv.title,
                'wbsCode': tv.wbs_node.wbs_code if tv.wbs_node else '',
                'wbsNodeId': tv.wbs_node.node_id if tv.wbs_node else None,
                'wbsTitle': tv.wbs_node.title if tv.wbs_node else '',
                'plannedStart': tv.planned_start.strftime('%Y-%m-%d %H:%M') if tv.planned_start else None,
                'plannedFinish': tv.planned_finish.strftime('%Y-%m-%d %H:%M') if tv.planned_finish else None,
                'durationHours': float(tv.duration_hours) if tv.duration_hours else 0,
                'description': tv.description or '',
                'executors': executors_by_task.get((tv.revision_id, tv.task_id), []),
            })

        # ط§ط¹ط¶ط§غŒ ظˆط§ط­ط¯ظگ ع©ط§ط±ط¨ط± â€” dropdown ظ‡ط§ ط§ط² ط§غŒظ† ظ„غŒط³طھ ظ¾ط± ظ…غŒâ€Œط´ظˆظ†ط¯
        if is_system_admin_actor:
            unit_members_qs = User.objects.all().order_by('username')
        elif unit_id:
            unit_members_qs = User.objects.filter(unit_id=unit_id).order_by('username')
        else:
            unit_members_qs = User.objects.none()

        unit_members = [
            {
                'id': u.id,
                'username': u.username,
                'jobTitle': getattr(u, 'job_title', '') or '',
                'employeeCode': getattr(u, 'employee_code', '') or '',
            }
            for u in unit_members_qs
        ]

        return Response({
            'tasks': tasks_data,
            'unitMembers': unit_members,
            'unitId': unit_id,
            'canAssignAcrossUnits': is_system_admin_actor,
            'page': page,
            'pageSize': page_size,
            'totalTasks': total_tasks,
            'hasNext': start + len(tasks_data) < total_tasks,
        })


def _date_range(start: date, end: date):
    """Yield every date from start to end (inclusive)."""
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _bucket_key(d: date, granularity: str) -> str:
    if granularity == "week":
        return _week_key(d)
    if granularity == "month":
        return _month_key(d)
    return d.isoformat()          # "day" (default)


def _bucket_label(key: str, granularity: str) -> str:
    """Human-readable label for a bucket key."""
    if granularity == "day":
        d = date.fromisoformat(key)
        return d.strftime("%d %b")
    if granularity == "week":
        # key like "2026-W23"
        year, wk = key.split("-W")
        d = datetime.strptime(f"{year}-W{wk}-1", "%G-W%V-%u").date()
        return f"W{wk} ({d.strftime('%d %b')})"
    if granularity == "month":
        year, month = key.split("-")
        d = date(int(year), int(month), 1)
        return d.strftime("%b %Y")
    return key


def _working_days_in_bucket(bucket_dates: list[date]) -> int:
    """Count Monâ€“Fri days in a list of dates (simplistic; ignores CalendarExceptions)."""
    return sum(1 for d in bucket_dates if d.weekday() < 5)


# â”€â”€â”€ view â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ResourceHistogramView(APIView):
    """
    Returns a resource load histogram for a given revision.

    Response shape:
    {
      "revision_id": "...",
      "granularity": "day",
      "buckets": ["2026-06-01", "2026-06-02", ...],
      "bucket_labels": ["01 Jun", "02 Jun", ...],
      "resources": [
        {
          "id": 1,
          "name": "Ali Ahmadi",
          "capacity_hours_per_day": 8.0,
          "load": [
            {
              "bucket": "2026-06-01",
              "allocated_hours": 6.0,
              "capacity_hours": 8.0,
              "load_percent": 75.0,
              "status": "optimum",   // "underload" | "optimum" | "overload"
              "tasks": [
                {"task_id": "...", "title": "Design", "hours": 6.0}
              ]
            },
            ...
          ]
        },
        ...
      ]
    }
    """

    permission_classes = [IsAuthenticated]

    UNDERLOAD_THRESHOLD = 50    # % below this â†’ underload
    OVERLOAD_THRESHOLD  = 100   # % above this â†’ overload

    def get(self, request, revision_id):
        # â”€â”€ 1. Fetch revision â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            revision = Revision.objects.get(pk=revision_id)
        except Revision.DoesNotExist:
            return Response({"detail": "Revision not found."}, status=status.HTTP_404_NOT_FOUND)

        # ظ…ط­ط¯ظˆط¯ط³ط§ط²غŒظگ ط®ظˆط§ظ†ط¯ظ†: ع©ط§ط±ط¨ط± ط¨ط§غŒط¯ ط¨ظ‡ ظ¾ط±ظˆعکظ‡ظ” ط§غŒظ† ظ†ط³ط®ظ‡ ط¯ط³طھط±ط³غŒظگ ظ…ط´ط§ظ‡ط¯ظ‡ ط¯ط§ط´طھظ‡ ط¨ط§ط´ط¯.
        if not can_view_project(request.user, revision.project):
            return Response({"detail": "ط´ظ…ط§ ط¨ظ‡ ط§غŒظ† ظ¾ط±ظˆعکظ‡ ط¯ط³طھط±ط³غŒ ظ†ط¯ط§ط±غŒط¯."}, status=status.HTTP_403_FORBIDDEN)

        granularity = request.query_params.get("granularity", "day")
        if granularity not in ("day", "week", "month"):
            return Response({"detail": "granularity must be day|week|month."}, status=status.HTTP_400_BAD_REQUEST)

        # â”€â”€ 2. Pull all task versions for this revision â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        task_versions = (
            TaskVersion.objects
            .filter(revision=revision, is_deleted=False)
            .exclude(planned_start=None)
            .exclude(planned_finish=None)
            .select_related("task")
        )

        # â”€â”€ 3. Pull assignments for this revision â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        assignments = (
            Assignment.objects
            .filter(revision=revision)
            .select_related("resource", "task")
        )

        # Map task_id â†’ TaskVersion for quick lookup
        tv_by_task = {str(tv.task_id): tv for tv in task_versions}

        # â”€â”€ 4. Determine global window â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        starts  = [tv.planned_start.date() for tv in task_versions]
        finishes = [tv.planned_finish.date() for tv in task_versions]

        if not starts:
            return Response({
                "revision_id": str(revision_id),
                "granularity": granularity,
                "buckets": [],
                "bucket_labels": [],
                "resources": [],
            })

        window_start = date.fromisoformat(request.query_params["start"]) if "start" in request.query_params else min(starts)
        window_end   = date.fromisoformat(request.query_params["end"])   if "end"   in request.query_params else max(finishes)

        all_dates = list(_date_range(window_start, window_end))

        # â”€â”€ 5. Build bucket â†’ list[date] mapping â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        bucket_dates: dict[str, list[date]] = defaultdict(list)
        for d in all_dates:
            bucket_dates[_bucket_key(d, granularity)].append(d)

        ordered_buckets = list(dict.fromkeys(_bucket_key(d, granularity) for d in all_dates))

        # â”€â”€ 6. Build per-resource, per-bucket load â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Structure: resource_id â†’ bucket_key â†’ { allocated_hours, tasks }
        resource_load: dict[int, dict[str, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"allocated_hours": Decimal("0"), "tasks": []})
        )

        resources_seen: dict[int, Resource] = {}

        for asgn in assignments:
            tv = tv_by_task.get(str(asgn.task_id))
            if tv is None:
                continue

            resource = asgn.resource
            resources_seen[resource.id] = resource

            cap_per_day = Decimal("8") * (resource.max_units / Decimal("100"))
            units_frac  = asgn.units_percent / Decimal("100")  # e.g. 0.5 for 50 %

            # Daily allocated hours from this assignment
            hours_per_working_day = cap_per_day * units_frac

            task_start  = tv.planned_start.date()
            task_finish = tv.planned_finish.date()

            # Clip to window
            eff_start = max(task_start,  window_start)
            eff_end   = min(task_finish, window_end)
            if eff_start > eff_end:
                continue

            for d in _date_range(eff_start, eff_end):
                if d.weekday() >= 5:        # skip weekends (simple rule)
                    continue
                bk = _bucket_key(d, granularity)
                resource_load[resource.id][bk]["allocated_hours"] += hours_per_working_day
                # Track which tasks contributed (deduplicate per bucket later)
                resource_load[resource.id][bk]["tasks"].append({
                    "task_id": str(asgn.task_id),
                    "title": tv.title,
                    "hours_per_day": float(round(hours_per_working_day, 2)),
                })

        # â”€â”€ 7. Deduplicate task entries per bucket â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for rid in resource_load:
            for bk in resource_load[rid]:
                seen_tasks: dict[str, float] = {}
                for t in resource_load[rid][bk]["tasks"]:
                    tid = t["task_id"]
                    seen_tasks[tid] = seen_tasks.get(tid, 0) + t["hours_per_day"]
                resource_load[rid][bk]["tasks"] = [
                    {"task_id": tid, "title": next(
                        t["title"] for t in resource_load[rid][bk]["tasks"] if t["task_id"] == tid
                    ), "hours": round(hrs, 2)}
                    for tid, hrs in seen_tasks.items()
                ]

        # â”€â”€ 8. Assemble response â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        result_resources = []
        # Resources are global master data (Resource has no project FK). Include
        # active resources so planners can also see idle/available capacity.
        all_resources = Resource.objects.filter(is_active=True)
        for res in all_resources:
            resources_seen.setdefault(res.id, res)

        for res in resources_seen.values():
            cap_per_day = 8.0 * float(res.max_units) / 100.0
            load_buckets = []

            for bk in ordered_buckets:
                working_days = _working_days_in_bucket(bucket_dates[bk])
                bucket_capacity = cap_per_day * working_days

                allocated = float(resource_load[res.id][bk]["allocated_hours"])
                load_pct  = (allocated / bucket_capacity * 100) if bucket_capacity > 0 else 0.0

                if load_pct <= 0:
                    st = "idle"
                elif load_pct < self.UNDERLOAD_THRESHOLD:
                    st = "underload"
                elif load_pct <= self.OVERLOAD_THRESHOLD:
                    st = "optimum"
                else:
                    st = "overload"

                load_buckets.append({
                    "bucket":           bk,
                    "allocated_hours":  round(allocated, 2),
                    "capacity_hours":   round(bucket_capacity, 2),
                    "load_percent":     round(load_pct, 1),
                    "status":           st,
                    "tasks":            resource_load[res.id][bk]["tasks"],
                })

            result_resources.append({
                "id":                    res.id,
                "name":                  res.name,
                "capacity_hours_per_day": cap_per_day,
                "user_id":               getattr(res, "user_id", None),
                "load":                  load_buckets,
            })

        # Sort resources: most overloaded first
        result_resources.sort(
            key=lambda r: -max((b["load_percent"] for b in r["load"]), default=0)
        )

        return Response({
            "revision_id":    str(revision_id),
            "granularity":    granularity,
            "buckets":        ordered_buckets,
            "bucket_labels":  [_bucket_label(b, granularity) for b in ordered_buckets],
            "resources":      result_resources,
        })


class ImportMSPView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        xml_file = request.FILES.get("file")
        # ط¯ط±غŒط§ظپطھ project_id ظˆ revision_id ط§ط² ط¯ط±ط®ظˆط§ط³طھ
        project_id = request.data.get("project_id")
        revision_id = request.data.get("revision_id")
        active_node_id = request.data.get('active_node_id')
        if not xml_file:
            return Response({"error": "No file provided."}, status=400)

        if not project_id or not revision_id:
            return Response({"error": "project_id and revision_id are required."}, status=400)

        if not xml_file.name.lower().endswith(".xml"):
            return Response({"error": "File must be a .xml export from MS Project."}, status=400)

        max_upload_bytes = getattr(settings, 'MSP_IMPORT_MAX_UPLOAD_MB', 100) * 1024 * 1024
        if xml_file.size and xml_file.size > max_upload_bytes:
            return Response(
                {
                    "error": "File is too large.",
                    "detail": f"MSP XML import limit is {getattr(settings, 'MSP_IMPORT_MAX_UPLOAD_MB', 100)} MB.",
                },
                status=413,
            )

        project = Project.objects.filter(pk=project_id, is_deleted=False).first()
        if project is None:
            return Response(
                {"error": "Invalid project_id.", "detail": "Selected project was not found."},
                status=400,
            )

        revision = Revision.objects.filter(pk=revision_id, project=project, is_deleted=False).first()
        if revision is None:
            return Response(
                {
                    "error": "Invalid revision_id.",
                    "detail": "Selected revision was not found for this project.",
                },
                status=400,
            )

        if active_node_id:
            try:
                node_exists = WBSNodeVersion.objects.filter(
                    node_id=active_node_id,
                    revision=revision,
                    is_deleted=False,
                ).exists()
            except (ValueError, DjangoValidationError):
                node_exists = False

            if not node_exists:
                return Response(
                    {
                        "error": "Invalid active_node_id.",
                        "detail": "Import target must be an existing WBS node in the selected revision.",
                    },
                    status=400,
                )

        try:
            # ظپط±ط§ط®ظˆط§ظ†غŒ طھط§ط¨ط¹ ط§طµظ„ط§ط­ ط´ط¯ظ‡ ط¯ط± msp_importer.py
            result = import_msp_xml(xml_file, project_id, revision_id, active_node_id=active_node_id)
            if result.get("error"):
                return Response(result, status=400)
        except Exception as exc:
            return Response(
                {"error": "Import failed.", "detail": str(exc)},
                status=500,
            )

        return Response(result, status=200)


class ExportMSPView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, revision_id):
        revision = get_object_or_404(
            Revision.objects.select_related("project").filter(
                project_id__in=accessible_project_ids(request.user),
                is_deleted=False,
                project__is_deleted=False,
            ),
            pk=revision_id,
        )

        try:
            xml_bytes = export_revision_to_msp_xml(revision)
        except Exception as exc:
            return Response(
                {"error": "Export failed.", "detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        safe_project_name = "".join(
            ch if ch.isascii() and (ch.isalnum() or ch in ("-", "_")) else "_"
            for ch in revision.project.name
        ).strip("_") or "project"
        filename = f"{safe_project_name}_rev_{revision.number}_msp.xml"
        response = HttpResponse(xml_bytes, content_type="application/xml; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ResourcePoolViewSet(viewsets.ModelViewSet):
    queryset = ResourcePool.objects.all()
    serializer_class = ResourcePoolSerializer
    permission_classes = [IsAuthenticated]

class ResourceRoleViewSet(viewsets.ModelViewSet):
    queryset = ResourceRole.objects.all()
    serializer_class = ResourceRoleSerializer
    permission_classes = [IsAuthenticated]

class ResourceSkillViewSet(viewsets.ModelViewSet):
    queryset = ResourceSkill.objects.all()
    serializer_class = ResourceSkillSerializer
    permission_classes = [IsAuthenticated]

class ResourceViewSet(viewsets.ModelViewSet):
    queryset = Resource.objects.all()
    serializer_class = ResourceSerializer
    permission_classes = [IsAuthenticated]

class ResourceSkillMappingViewSet(viewsets.ModelViewSet):
    queryset = ResourceSkillMapping.objects.all()
    serializer_class = ResourceSkillMappingSerializer
    permission_classes = [IsAuthenticated]

class ResourceExceptionViewSet(viewsets.ModelViewSet):
    queryset = ResourceException.objects.all()
    serializer_class = ResourceExceptionSerializer
    permission_classes = [IsAuthenticated]

class ResourceRateViewSet(viewsets.ModelViewSet):
    queryset = ResourceRate.objects.all()
    serializer_class = ResourceRateSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()

        # ظپغŒظ„طھط± ط§ظ…ظ†غŒطھغŒ/ط¯ط³طھط±ط³غŒ (ط§ع¯ط± ط¯ط§ط±غŒ)
        # queryset = queryset.filter(...)

        resource_id = self.request.query_params.get('resource_id')
        before_date = self.request.query_params.get('before_date')

        if resource_id:
            queryset = queryset.filter(resource_id=resource_id)

        if before_date:
            queryset = queryset.filter(effectiveFrom__lte=before_date)

        return queryset

class AssignmentViewSet(viewsets.ModelViewSet):
    queryset = Assignment.objects.all()
    serializer_class = AssignmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        accessible_ids = accessible_project_ids(self.request.user)
        queryset = queryset.filter(revision__project_id__in=accessible_ids)

        revision_id = self.request.query_params.get('revision_id')
        task_id = self.request.query_params.get('task_id')

        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        elif task_id:
            task = Task.objects.filter(pk=task_id, project_id__in=accessible_ids).select_related('project').first()
            if task and task.project.current_execution_revision_id:
                queryset = queryset.filter(revision_id=task.project.current_execution_revision_id)
            else:
                queryset = queryset.none()
        if task_id:
            queryset = queryset.filter(task_id=task_id)

        return queryset

from django.contrib.auth import get_user_model
User = get_user_model()
class PersonalTaskViewSet(viewsets.ViewSet):
    """
    ظ…ط¯غŒط±غŒطھ طھط³ع©â€Œظ‡ط§غŒ ط´ط®طµغŒ ع©ط§ط±ط¨ط±ط§ظ† ع©ظ‡ ط¨ظ‡ ط¹ظ†ظˆط§ظ† غŒع© ظ¾ط±ظˆعکظ‡ ط³غŒط³طھظ…غŒ ط¯ط± ط¨ع©â€Œط§ظ†ط¯ ط«ط¨طھ ظ…غŒâ€Œط´ظˆظ†ط¯.
    """
    permission_classes = [IsAuthenticated]

    # ظ…طھط¯ GET ط¨ط±ط§غŒ ع¯ط±ظپطھظ† ظ„غŒط³طھ طھط³ع©â€Œظ‡ط§غŒ ط´ط®طµغŒ ط§ط² ط³ظ…طھ ظپط±ط§ظ†طھâ€Œط§ظ†ط¯
    def list(self, request):
        sys_project = Project.objects.filter(name="System-Personal-Tasks").first()
        if not sys_project:
            # ط§ع¯ط± ظ¾ط±ظˆعکظ‡ ظ‡ظ†ظˆط² ط³ط§ط®طھظ‡ ظ†ط´ط¯ظ‡طŒ غŒط¹ظ†غŒ ع©ط§ط±ط¨ط± ظ‡ظ†ظˆط² طھط³ع©غŒ ط§غŒط¬ط§ط¯ ظ†ع©ط±ط¯ظ‡ ط§ط³طھ
            return Response([], status=status.HTTP_200_OK)

        # ظ¾غŒط¯ط§ ع©ط±ط¯ظ† ط±غŒظˆغŒعکظ† ظپط¹ط§ظ„ ظˆ طھظ…ط§ظ… طھط³ع©â€Œظ‡ط§غŒغŒ ع©ظ‡ ط­ط°ظپ ظ†ط´ط¯ظ‡â€Œط§ظ†ط¯
        revision = get_official_revision(sys_project, ROLE_WORKING, required=True)
        tasks = TaskVersion.objects.filter(revision=revision, is_deleted=False)

        # ط§ط³طھظپط§ط¯ظ‡ ط§ط² ط³ط±غŒط§ظ„ط§غŒط²ط± ع¯ط§ظ†طھâ€Œع†ط§ط±طھ ط¨ط±ط§غŒ ظ‡ظ…ط®ظˆط§ظ†غŒ ط³ط§ط®طھط§ط± ط¯غŒطھط§ ط¨ط§ ظپط±ط§ظ†طھâ€Œط§ظ†ط¯
        serializer = ActivityNodeSerializer(tasks, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # ظ…طھط¯ POST ط¨ط±ط§غŒ ط§غŒط¬ط§ط¯ طھط³ع© ط´ط®طµغŒ ط¬ط¯غŒط¯
    @action(detail=False, methods=['post'], url_path='create')
    @transaction.atomic
    def create_personal_task(self, request):
        title = request.data.get('title')
        start_date = request.data.get('start_date')
        duration_hours = request.data.get('duration_hours')
        description = request.data.get('description')
        user_id = request.data.get('user_id')
        current_id = (request.data.get('current_user')).get('id')

        if not all([title, start_date, duration_hours, user_id]):
            return Response({"detail": "طھظ…ط§ظ…غŒ ظپغŒظ„ط¯ظ‡ط§ (ط¹ظ†ظˆط§ظ†طŒ طھط§ط±غŒط®طŒ ظ…ط¯طھâ€Œط²ظ…ط§ظ† ظˆ ع©ط§ط±ط¨ط±) ط§ظ„ط²ط§ظ…غŒ ط§ط³طھ."},
                            status=status.HTTP_400_BAD_REQUEST)

        # غ±. ط³ط§ط®طھ غŒط§ ط¯ط±غŒط§ظپطھ ظ¾ط±ظˆعکظ‡ ط³غŒط³طھظ…غŒ
        sys_project, created = Project.objects.get_or_create(
            name="System-Personal-Tasks",
            defaults={'created_by': request.user}
        )

        # غ². ط¯ط±غŒط§ظپطھ ط±غŒظˆغŒعکظ† (ط·ط¨ظ‚ ظ…ط¯ظ„â€Œظ‡ط§غŒ ط´ظ…ط§طŒ ط±غŒظˆغŒعکظ† طµظپط± ط®ظˆط¯ع©ط§ط± ط¨ط§ ط³ط§ط®طھ ظ¾ط±ظˆعکظ‡ ط§غŒط¬ط§ط¯ ظ…غŒâ€Œط´ظˆط¯)
        revision = get_official_revision(sys_project, ROLE_WORKING, required=True)

        # غ³. ظ…ط¯غŒط±غŒطھ ط³ط§ط®طھط§ط± WBS ط¨ط±ط§غŒ طھط³ع©â€Œظ‡ط§غŒ ط´ط®طµغŒ
        # ظ…ط¯ظ„ WBSNode ظپغŒظ„ط¯ ظ†ط§ظ… ظ†ط¯ط§ط±ط¯طŒ ظ†ط§ظ… ط¯ط± WBSNodeVersion ط°ط®غŒط±ظ‡ ظ…غŒâ€Œط´ظˆط¯
        wbs_node_version = WBSNodeVersion.objects.filter(revision=revision, title="My Personal Tasks").first()

        if not wbs_node_version:
            # ظ¾غŒط¯ط§ ع©ط±ط¯ظ† ع¯ط±ظ‡ ط±غŒط´ظ‡ ع©ظ‡ ط¨ط§ ط³غŒع¯ظ†ط§ظ„ ط§غŒط¬ط§ط¯ ط´ط¯ظ‡
            root_wbs = WBSNodeVersion.objects.get(revision=revision, parent__isnull=True)

            # ط³ط§ط®طھ ع¯ط±ظ‡ WBS ظپط±ط²ظ†ط¯ ط¨ط±ط§غŒ ع©ط§ط±ظ‡ط§غŒ ط´ط®طµغŒ
            base_node = WBSNode.objects.create(project=sys_project)
            wbs_node_version = WBSNodeVersion.objects.create(
                node=base_node,
                revision=revision,
                parent=root_wbs,
                title="My Personal Tasks",
                sequence=1
            )

        # غ´. ط³ط§ط®طھ طھط³ع© ظپغŒط²غŒع©غŒ ظˆ ظ†ط³ط®ظ‡ ط¢ظ†
        task = Task.objects.create(project=sys_project)

        task_ver = TaskVersion.objects.create(
            task=task,
            revision=revision,
            wbs_node=wbs_node_version,
            title=title,
            planned_start=start_date,
            duration_hours=duration_hours,
            description=description,
        )

        # غµ. ط§غŒط¬ط§ط¯ ظ†ظ‚ط´ ظ…ط¬ط±غŒ
        # ط§غŒظ† ع©ط§ط± ط¨ط§ط¹ط« ظ…غŒâ€Œط´ظˆط¯ ط³غŒع¯ظ†ط§ظ„غŒ ع©ظ‡ ط¯ط± signals.py ط¯ط§ط±غŒط¯طŒ ظپظˆط±ط§ظ‹ ع©ط§ط±ط¨ط± ط±ط§ ط¨ظ‡ ط¬ط¯ظˆظ„ Assignment
        # ط§ط¶ط§ظپظ‡ ع©ظ†ط¯ طھط§ ط¨ط±ط§غŒ ظ„ظˆظ„غŒظ†ع¯ ط¢ظ…ط§ط¯ظ‡ ط´ظˆط¯.
        user = User.objects.get(id=user_id)
        current=User.objects.get(id=current_id)
        TaskRole.objects.create(
            revision=revision,
            task=task,
            user=user,
            role='executor'
        )
        TaskRole.objects.create(
            revision=revision,
            task=task,
            user=current,
            role='reviewer'
        )


        # غ¶. ط¨ط§ط²ع¯ط±ط¯ط§ظ†ط¯ظ† ط¯غŒطھط§غŒ طھط³ع© ط¨ط§ ظپط±ظ…طھ ط§ط³طھط§ظ†ط¯ط§ط±ط¯ ط¨ط±ط§غŒ ظ†ظ…ط§غŒط´ ط³ط±غŒط¹ ط¯ط± ظ„غŒط³طھ ظپط±ط§ظ†طھâ€Œط§ظ†ط¯
        serializer = ActivityNodeSerializer(task_ver)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # ظ…طھط¯ DELETE ط¨ط±ط§غŒ ظ„ط؛ظˆ غŒط§ ظ¾ط§ع© ع©ط±ط¯ظ† طھط³ع© ط´ط®طµغŒ
    def destroy(self, request, pk=None):
        try:
            task_ver = TaskVersion.objects.get(task__id=pk)

            # ط§ط³طھظپط§ط¯ظ‡ ط§ط² ظˆغŒعکع¯غŒ Soft Delete ع©ظ‡ ط¯ط± ط³غŒط³طھظ… ط´ظ…ط§ ظ¾غŒط§ط¯ظ‡â€Œط³ط§ط²غŒ ط´ط¯ظ‡ ط§ط³طھ
            task_ver.is_deleted = True
            task_ver.save()

            # ط­ط°ظپ ظ†ظ‚ط´ ع©ط§ط±ط¨ط± طھط§ ط³غŒع¯ظ†ط§ظ„ remove_executor_assignment ط¯ط± signals.py
            # طھط±غŒع¯ط± ط´ظˆط¯ ظˆ ظ…ظ†ط¨ط¹ ط±ط§ ط§ط² Assignment ظ¾ط§ع© ع©ظ†ط¯طŒ طھط§ ط¸ط±ظپغŒطھ ط¢ط²ط§ط¯ ط´ظˆط¯.
            TaskRole.objects.filter(task__id=pk).delete()

            return Response(status=status.HTTP_204_NO_CONTENT)
        except TaskVersion.DoesNotExist:
            return Response({"detail": "طھط³ع© غŒط§ظپطھ ظ†ط´ط¯."}, status=status.HTTP_404_NOT_FOUND)


    def partial_update(self, request, pk=None):
        try:
            # ظ¾غŒط¯ط§ ع©ط±ط¯ظ† طھط³ع© ظپط¹ظ„غŒ ع©ظ‡ ط­ط°ظپ ظ†ط´ط¯ظ‡ ط¨ط§ط´ط¯
            task_ver = TaskVersion.objects.get(task__id=pk, is_deleted=False)

            # ط¯ط±غŒط§ظپطھ ظپغŒظ„ط¯ظ‡ط§غŒ ط§ط±ط³ط§ظ„ ط´ط¯ظ‡ ط§ط² ط³ظ…طھ ع©ظ„ط§غŒظ†طھ
            title = request.data.get('title')
            start_date = request.data.get('start_date')
            duration_hours = request.data.get('duration_hours')
            description = request.data.get('description')
            user_id = request.data.get('user_id')

            # ط§ط¹ظ…ط§ظ„ طھط؛غŒغŒط±ط§طھ ط±ظˆغŒ طھط³ع© (ط¯ط± طµظˆط±طھ ظˆط¬ظˆط¯ ظ‡ط± ظپغŒظ„ط¯ ط¯ط± ط±غŒع©ظˆط¦ط³طھ)
            if title:
                task_ver.title = title
            if start_date:
                task_ver.planned_start = start_date
            if duration_hours:
                task_ver.duration_hours = duration_hours
            if description is not None:  # طھظˆط¶غŒط­ط§طھ ظ…غŒâ€Œطھظˆط§ظ†ط¯ ط®ط§ظ„غŒ ط¨ط§ط´ط¯
                task_ver.description = description

            task_ver.save()

            # ط¯ط± طµظˆط±طھغŒ ع©ظ‡ ع©ط§ط±ط¨ط± ظ…ط¬ط±غŒ طھط؛غŒغŒط± ع©ط±ط¯ظ‡ ط¨ط§ط´ط¯طŒ ظ†ظ‚ط´ ط§ظˆ ط±ط§ ط¢ظ¾ط¯غŒطھ ظ…غŒâ€Œع©ظ†غŒظ…
            if user_id:
                task_role = TaskRole.objects.filter(task=task_ver.task, role='executor').first()
                if task_role:
                    if str(task_role.user_id) != str(user_id):
                        task_role.user_id = user_id
                        task_role.save()
                else:
                    # ط§ع¯ط± ظ†ظ‚ط´غŒ ط§ط² ظ‚ط¨ظ„ ظ†ط¨ظˆط¯طŒ غŒع©غŒ ظ…غŒâ€Œط³ط§ط²غŒظ…
                    TaskRole.objects.create(
                        revision=task_ver.revision,
                        task=task_ver.task,
                        user_id=user_id,
                        role='executor'
                    )

            # ط§ط³طھظپط§ط¯ظ‡ ط§ط² ظ‡ظ…ط§ظ† ط³ط±غŒط§ظ„ط§غŒط²ط±غŒ ع©ظ‡ ط¯ط± ظ„غŒط³طھ ظˆ ط³ط§ط®طھ ط§ط³طھظپط§ط¯ظ‡ ع©ط±ط¯غŒط¯
            serializer = ActivityNodeSerializer(task_ver)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except TaskVersion.DoesNotExist:
            return Response({"detail": "طھط³ع© غŒط§ظپطھ ظ†ط´ط¯."}, status=status.HTTP_404_NOT_FOUND)


class VarianceReportViewSet(viewsets.ModelViewSet):
    """ظ…ط¯غŒط±غŒطھ ع¯ط²ط§ط±ط´â€Œظ‡ط§غŒ ط§ظ†ط­ط±ط§ظپ ظˆ ط§طھطµط§ظ„ ط¨ظ‡ ظ…ظˆطھظˆط± EVM"""
    queryset = VarianceReport.objects.all()
    serializer_class = VarianceReportSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(revision__project_id__in=accessible_project_ids(self.request.user))
        dimension = (self.request.query_params.get('dimension') or VarianceReport.DIMENSION_EFFORT).strip().lower()
        if dimension not in {VarianceReport.DIMENSION_EFFORT, VarianceReport.DIMENSION_COST}:
            raise ValidationError({"dimension": "Invalid dimension. Use 'effort' or 'cost'."})
        queryset = queryset.filter(dimension=dimension)
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        task_id = self.request.query_params.get('task_id')
        if task_id:
            queryset = queryset.filter(task_id=task_id)
        wbs_node_id = self.request.query_params.get('wbs_node_id')
        if wbs_node_id:
            wbs_queryset = WBSNodeVersion.objects.filter(node_id=wbs_node_id, is_deleted=False)
            if revision_id:
                wbs_queryset = wbs_queryset.filter(revision_id=revision_id)
            wbs_version = wbs_queryset.first()
            if wbs_version:
                scoped_wbs_ids = wbs_version.get_descendants(include_self=True).values_list('id', flat=True)
                queryset = queryset.filter(
                    task__versions__revision_id=F('revision_id'),
                    task__versions__wbs_node_id__in=scoped_wbs_ids,
                ).distinct()
            else:
                queryset = queryset.none()
        search = (self.request.query_params.get('search') or '').strip()
        if search:
            queryset = queryset.filter(
                Q(task__versions__title__icontains=search, task__versions__revision_id=F('revision_id')) |
                Q(task__versions__wbs_node__wbs_code__icontains=search, task__versions__revision_id=F('revision_id'))
            ).distinct()
        return queryset.select_related('task', 'revision').order_by('-report_date', 'id')

    def _latest_task_queryset(self, queryset):
        latest_date = VarianceReport.objects.filter(
            task_id=OuterRef('task_id'),
            revision_id=OuterRef('revision_id'),
            dimension=OuterRef('dimension'),
        ).order_by('-report_date').values('report_date')[:1]
        return queryset.filter(report_date=Subquery(latest_date))

    def _series(self, queryset):
        rows = queryset.order_by('report_date').values('report_date').annotate(
            total_pv=Sum('planned_value'),
            total_ev=Sum('earned_value'),
            total_ac=Sum('actual_cost'),
        )
        return [
            {
                'date': item['report_date'].isoformat() if item['report_date'] else None,
                'plannedValue': float(item['total_pv'] or Decimal('0')),
                'earnedValue': float(item['total_ev'] or Decimal('0')),
                'actualCost': float(item['total_ac'] or Decimal('0')),
            }
            for item in rows
        ]
    def _summary(self, queryset):
        totals = queryset.aggregate(
            total_bac=Sum('budget_at_completion'),
            total_pv=Sum('planned_value'),
            total_ev=Sum('earned_value'),
            total_ac=Sum('actual_cost'),
        )
        total_bac = totals.get('total_bac') or Decimal('0')
        total_pv = totals.get('total_pv') or Decimal('0')
        total_ev = totals.get('total_ev') or Decimal('0')
        total_ac = totals.get('total_ac') or Decimal('0')
        total_sv = total_ev - total_pv
        total_cv = total_ev - total_ac
        return {
            'totalBAC': float(total_bac),
            'totalPV': float(total_pv),
            'totalEV': float(total_ev),
            'totalAC': float(total_ac),
            'overallSPI': float((total_ev / total_pv).quantize(Decimal('0.01'))) if total_pv else 1.0,
            'overallCPI': float((total_ev / total_ac).quantize(Decimal('0.01'))) if total_ac else 1.0,
            'totalSV': float(total_sv),
            'totalCV': float(total_cv),
            'criticalCount': queryset.filter(action_required=True).count(),
        }

    def _parse_evm_dimension(self, request):
        dimension = (request.query_params.get('dimension') or 'effort').strip().lower()
        if dimension not in {'effort', 'cost'}:
            return None, Response(
                {"dimension": "Invalid dimension. Use 'effort' or 'cost'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return dimension, None

    def _resolve_cost_project_id(self, request):
        revision_id = request.query_params.get('revision_id')
        project_id = request.query_params.get('project_id')
        task_id = request.query_params.get('task_id')

        if project_id:
            return str(project_id)
        if revision_id:
            revision = Revision.objects.filter(pk=revision_id).only('project_id').first()
            return str(revision.project_id) if revision else None
        if task_id:
            task = Task.objects.filter(pk=task_id).only('project_id').first()
            return str(task.project_id) if task else None
        return None

    def _cost_snapshot_queryset(self, request, project_id):
        queryset = VarianceReport.objects.filter(
            revision__project_id=project_id,
            revision__project_id__in=accessible_project_ids(request.user),
            dimension=VarianceReport.DIMENSION_COST,
        )
        revision_id = request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        task_id = request.query_params.get('task_id')
        if task_id:
            queryset = queryset.filter(task_id=task_id)
        currency = (request.query_params.get('currency') or '').strip().upper()
        if currency:
            queryset = queryset.filter(currency=currency)

        status_date_value = request.query_params.get('status_date')
        if status_date_value:
            try:
                queryset = queryset.filter(report_date__lte=parse_cpm_data_date(status_date_value).date())
            except ValueError:
                return VarianceReport.objects.none()

        wbs_node_id = request.query_params.get('wbs_node_id')
        if wbs_node_id:
            wbs_queryset = WBSNodeVersion.objects.filter(node_id=wbs_node_id, is_deleted=False)
            if revision_id:
                wbs_queryset = wbs_queryset.filter(revision_id=revision_id)
            wbs_version = wbs_queryset.first()
            if wbs_version:
                scoped_wbs_ids = wbs_version.get_descendants(include_self=True).values_list('id', flat=True)
                queryset = queryset.filter(
                    task__versions__revision_id=F('revision_id'),
                    task__versions__wbs_node_id__in=scoped_wbs_ids,
                ).distinct()
            else:
                queryset = queryset.none()

        search = (request.query_params.get('search') or '').strip()
        if search:
            queryset = queryset.filter(
                Q(task__versions__title__icontains=search, task__versions__revision_id=F('revision_id')) |
                Q(task__versions__wbs_node__wbs_code__icontains=search, task__versions__revision_id=F('revision_id'))
            ).distinct()
        return queryset

    def _cost_series(self, request, rows, project_id):
        snapshot_rows = self._cost_snapshot_queryset(request, project_id).order_by('report_date').values('report_date').annotate(
            total_pv=Sum('planned_value'),
            total_ev=Sum('earned_value'),
            total_ac=Sum('actual_cost'),
        )
        series = [
            {
                'date': item['report_date'].isoformat() if item['report_date'] else None,
                'plannedValue': float(item['total_pv'] or Decimal('0')),
                'earnedValue': float(item['total_ev'] or Decimal('0')),
                'actualCost': float(item['total_ac'] or Decimal('0')),
            }
            for item in snapshot_rows
        ]
        if series:
            return series

        totals = self._cost_summary(rows)
        dates = sorted({row.get('report_date') for row in rows if row.get('report_date')})
        return [
            {
                'date': date_value,
                'plannedValue': totals['totalPV'],
                'earnedValue': totals['totalEV'],
                'actualCost': totals['totalAC'],
            }
            for date_value in dates[:1]
        ]

    def _cost_summary(self, rows):
        def as_decimal(value):
            if value in (None, ''):
                return Decimal('0.00')
            return Decimal(str(value))

        total_bac = sum((as_decimal(row.get('budget_at_completion')) for row in rows), Decimal('0.00'))
        total_pv = sum((as_decimal(row.get('planned_value')) for row in rows), Decimal('0.00'))
        total_ev = sum((as_decimal(row.get('earned_value')) for row in rows), Decimal('0.00'))
        total_ac = sum((as_decimal(row.get('actual_cost')) for row in rows), Decimal('0.00'))
        total_sv = total_ev - total_pv
        total_cv = total_ev - total_ac
        return {
            'totalBAC': float(total_bac),
            'totalPV': float(total_pv),
            'totalEV': float(total_ev),
            'totalAC': float(total_ac),
            'overallSPI': float((total_ev / total_pv).quantize(Decimal('0.0001'))) if total_pv else None,
            'overallCPI': float((total_ev / total_ac).quantize(Decimal('0.0001'))) if total_ac else None,
            'totalSV': float(total_sv),
            'totalCV': float(total_cv),
            'criticalCount': sum(1 for row in rows if row.get('action_required')),
        }

    def _filtered_cost_rows(self, request, rows):
        revision_id = request.query_params.get('revision_id')
        task_id = request.query_params.get('task_id')
        wbs_node_id = request.query_params.get('wbs_node_id')
        search = (request.query_params.get('search') or '').strip().lower()

        if task_id:
            rows = [row for row in rows if str(row.get('task')) == str(task_id)]
        if wbs_node_id:
            wbs_queryset = WBSNodeVersion.objects.filter(node_id=wbs_node_id, is_deleted=False)
            if revision_id:
                wbs_queryset = wbs_queryset.filter(revision_id=revision_id)
            wbs_version = wbs_queryset.first()
            if wbs_version:
                scoped_wbs_ids = set(wbs_version.get_descendants(include_self=True).values_list('id', flat=True))
                rows = [row for row in rows if row.get('wbs_node_id') in scoped_wbs_ids]
            else:
                rows = []
        if search:
            rows = [
                row for row in rows
                if search in str(row.get('task_name') or '').lower()
                or search in str(row.get('task_code') or '').lower()
            ]
        return rows

    def _cost_list(self, request):
        status_date_value = request.query_params.get('status_date')
        try:
            data_datetime = parse_cpm_data_date(status_date_value) if status_date_value else None
        except ValueError:
            return Response(
                {"status_date": "Invalid status_date. Use ISO datetime, date, or 'now'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        project_id = self._resolve_cost_project_id(request)
        if not project_id:
            return Response(
                {"project_id": "project_id, revision_id, or task_id is required for cost EVM."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if project_id not in {str(item) for item in accessible_project_ids(request.user)}:
            raise PermissionDenied("You do not have access to this project.")

        try:
            engine = EVMEngine(project_id=project_id, data_datetime=data_datetime)
            rows = engine.run_cost_task_variances(revision_id=request.query_params.get('revision_id'))
        except Revision.DoesNotExist:
            return Response({"revision_id": "Revision not found for this project."}, status=status.HTTP_404_NOT_FOUND)

        rows = self._filtered_cost_rows(request, rows)
        page_param = request.query_params.get('page')
        page_size_param = request.query_params.get('pageSize') or request.query_params.get('page_size')

        if not page_param and not page_size_param:
            return Response(rows)

        try:
            page = max(int(page_param or 1), 1)
            page_size = min(max(int(page_size_param or 100), 1), 250)
        except (TypeError, ValueError):
            page, page_size = 1, 100

        total = len(rows)
        start = (page - 1) * page_size
        page_rows = rows[start:start + page_size]
        return Response({
            'results': page_rows,
            'summary': self._cost_summary(rows),
            'series': self._cost_series(request, rows, project_id),
            'page': page,
            'pageSize': page_size,
            'total': total,
            'hasNext': start + len(page_rows) < total,
        })

    def list(self, request, *args, **kwargs):
        dimension, error_response = self._parse_evm_dimension(request)
        if error_response:
            return error_response
        if dimension == 'cost':
            return self._cost_list(request)

        queryset = self.filter_queryset(self.get_queryset())
        series = self._series(queryset)
        include_history = (request.query_params.get('history') or '').lower() in {'1', 'true', 'yes'}
        display_queryset = queryset if include_history else self._latest_task_queryset(queryset)
        page_param = request.query_params.get('page')
        page_size_param = request.query_params.get('pageSize') or request.query_params.get('page_size')

        if not page_param and not page_size_param:
            serializer = self.get_serializer(display_queryset, many=True)
            return Response(serializer.data)

        try:
            page = max(int(page_param or 1), 1)
            page_size = min(max(int(page_size_param or 100), 1), 250)
        except (TypeError, ValueError):
            page, page_size = 1, 100

        total = display_queryset.count()
        start = (page - 1) * page_size
        page_queryset = display_queryset[start:start + page_size]
        serializer = self.get_serializer(page_queryset, many=True)
        return Response({
            'results': serializer.data,
            'summary': self._summary(display_queryset),
            'series': series,
            'page': page,
            'pageSize': page_size,
            'total': total,
            'hasNext': start + len(serializer.data) < total,
        })

    @action(detail=False, methods=['post'], url_path='calculate', url_name='calculate')
    def trigger_calculation(self, request):
        """ط§ط¬ط±ط§غŒ ط¯ط³طھغŒ ظ…ظˆطھظˆط± ظ…ط­ط§ط³ط¨ط§طھغŒ ط¨ط±ط§غŒ غŒع© ظ¾ط±ظˆعکظ‡"""
        project_id = request.data.get('project_id')
        revision_id = request.data.get('revision_id')
        if not project_id:
            return Response({"error": "project_id ط§ظ„ط²ط§ظ…غŒ ط§ط³طھ."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            data_datetime = parse_cpm_data_date(request.data.get('dataDate'))
            engine = EVMEngine(project_id=project_id, data_datetime=data_datetime, revision_id=revision_id)
            result = engine.run_historical_task_level_variances()
            return Response({
                "status": "ظ…ط­ط§ط³ط¨ط§طھ ط¨ط§ ظ…ظˆظپظ‚غŒطھ ط§ظ†ط¬ط§ظ… ط´ط¯ ظˆ ط¯غŒطھط§ط¨غŒط³ ط¨ظ‡â€Œط±ظˆط²ط±ط³ط§ظ†غŒ ع¯ط±ط¯غŒط¯.",
                "dataDate": engine.data_datetime.isoformat() if engine.data_datetime else None,
                "revisionId": str(engine.current_rev.id),
                "historyDates": result.get("dates", []),
                "snapshots": result.get("snapshots", 0),
            }, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Revision.DoesNotExist:
            return Response({"revision_id": "Revision not found for this project."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# â”€â”€â”€ SystemSettings endpoint (singleton) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class SystemSettingsView(APIView):
    """
    GET: ط®ظˆط§ظ†ط¯ظ†ظگ طھظ†ط¸غŒظ…ط§طھظگ ع©ظ„غŒظگ ط³غŒط³طھظ… (ظ‡ط± ع©ط§ط±ط¨ط±ظگ ط§ط­ط±ط§ط²ط´ط¯ظ‡).
    PUT/PATCH: ظˆغŒط±ط§غŒط´ ظپظ‚ط· طھظˆط³ط·ظگ ط³ط·ط­ظگ ط´ط±ع©طھ (company_admin / company_pm / superuser).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        settings_obj = SystemSettings.current()
        serializer = SystemSettingsSerializer(settings_obj)
        return Response(serializer.data)

    def put(self, request):
        return self._update(request)

    def patch(self, request):
        return self._update(request)

    def _update(self, request):
        if not is_company_level(request.user):
            raise PermissionDenied("ظˆغŒط±ط§غŒط´ظگ طھظ†ط¸غŒظ…ط§طھظگ ط³غŒط³طھظ… ظپظ‚ط· ط¨ط±ط§غŒ ع©ط§ط±ط¨ط±ط§ظ†ظگ ط³ط·ط­ظگ ط´ط±ع©طھ ظ…ط¬ط§ط² ط§ط³طھ.")
        settings_obj = SystemSettings.current()
        serializer = SystemSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class UnitOfMeasureViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ظˆغŒظˆط³طھ ط¨ط±ط§غŒ ظˆط§ط­ط¯ظ‡ط§غŒ ط§ظ†ط¯ط§ط²ظ‡â€Œع¯غŒط±غŒ.
    ظ…ط¹ظ…ظˆظ„ط§ظ‹ ظˆط§ط­ط¯ظ‡ط§ ظپظ‚ط· ط®ظˆط§ظ†ط¯ظ†غŒ (ReadOnly) ظ‡ط³طھظ†ط¯ ظˆ ط§ط² ط·ط±غŒظ‚ ظ¾ظ†ظ„ ط§ط¯ظ…غŒظ† غŒط§ ط´ظ„ ط§ط¶ط§ظپظ‡ ظ…غŒâ€Œط´ظˆظ†ط¯.
    ط§ع¯ط± ظ…غŒâ€Œط®ظˆط§ظ‡غŒط¯ ط§ط² ط·ط±غŒظ‚ API ظ‡ظ… ظ‚ط§ط¨ظ„غŒطھ ط§ط¶ط§ظپظ‡ ع©ط±ط¯ظ† ط¯ط§ط´طھظ‡ ط¨ط§ط´غŒط¯طŒ ط§ط² ModelViewSet ط§ط³طھظپط§ط¯ظ‡ ع©ظ†غŒط¯.
    """
    queryset = UnitOfMeasure.objects.all().order_by('name')
    serializer_class = UnitOfMeasureSerializer
    permission_classes = [IsAuthenticated] # ط¯ط± طµظˆط±طھ ظ†غŒط§ط² ط¨ظ‡ ط§ط­ط±ط§ط² ظ‡ظˆغŒطھ

class ExpenseTypeViewSet(viewsets.ModelViewSet):
    """
    ظˆغŒظˆط³طھ ع©ط§ظ…ظ„ ط¨ط±ط§غŒ ظ…ط¯غŒط±غŒطھ ط§ظ†ظˆط§ط¹ ظ‡ط²غŒظ†ظ‡â€Œظ‡ط§ (Expense Types).
    """
    queryset = ExpenseType.objects.all().order_by('name')
    serializer_class = ExpenseTypeSerializer
    permission_classes = [IsAuthenticated]


class FundingSourceViewSet(viewsets.ModelViewSet):
    queryset = FundingSource.objects.all().order_by('-received_date', '-created_at')
    serializer_class = FundingSourceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        source_type = self.request.query_params.get('source_type')
        if source_type:
            queryset = queryset.filter(source_type=source_type)
        status = self.request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def perform_create(self, serializer):
        funding_source = serializer.save(created_by=self.request.user)
        log_budget_audit(self.request, 'funding_source_created', funding_source)

    def perform_update(self, serializer):
        old = model_to_dict_safe(serializer.instance)
        funding_source = serializer.save()
        log_budget_audit(self.request, 'funding_source_updated', funding_source, old=old)

    def perform_destroy(self, instance):
        old = model_to_dict_safe(instance)
        log_budget_audit(self.request, 'funding_source_deleted', instance, old=old, extra={'deleted': old})
        instance.delete()

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        funding_source = self.get_object()
        old = model_to_dict_safe(funding_source)
        submit_budget_object(funding_source, request.user)
        log_budget_audit(request, 'funding_source_submitted', funding_source, old=old)
        return Response(self.get_serializer(funding_source).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        funding_source = self.get_object()
        old = model_to_dict_safe(funding_source)
        approve_budget_object(funding_source, request.user)
        log_budget_audit(request, 'funding_source_approved', funding_source, old=old)
        return Response(self.get_serializer(funding_source).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        funding_source = self.get_object()
        old = model_to_dict_safe(funding_source)
        reason = request.data.get('reason', '')
        reject_budget_object(funding_source, request.user, reason)
        log_budget_audit(request, 'funding_source_rejected', funding_source, old=old, extra={'reason': reason})
        return Response(self.get_serializer(funding_source).data)


class BudgetAllocationViewSet(viewsets.ModelViewSet):
    queryset = BudgetAllocation.objects.select_related(
        'funding_source', 'parent_allocation', 'project', 'revision', 'wbs_node', 'task', 'org_unit'
    ).all()
    serializer_class = BudgetAllocationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(
            Q(project_id__in=accessible_project_ids(self.request.user)) |
            Q(project__isnull=True)
        )

        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        funding_source_id = self.request.query_params.get('funding_source_id')
        if funding_source_id:
            queryset = queryset.filter(funding_source_id=funding_source_id)
        parent_allocation_id = self.request.query_params.get('parent_allocation_id')
        if parent_allocation_id:
            queryset = queryset.filter(parent_allocation_id=parent_allocation_id)
        scope_type = self.request.query_params.get('scope_type')
        if scope_type:
            queryset = queryset.filter(scope_type=scope_type)
        cost_type = self.request.query_params.get('cost_type')
        if cost_type:
            queryset = queryset.filter(cost_type=cost_type)
        status = self.request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)
        task_id = self.request.query_params.get('task_id')
        if task_id:
            queryset = queryset.filter(task_id=task_id)
        return queryset

    def perform_create(self, serializer):
        allocation = serializer.save(created_by=self.request.user)
        log_budget_audit(self.request, 'budget_allocation_created', allocation)

    def perform_update(self, serializer):
        old = model_to_dict_safe(serializer.instance)
        allocation = serializer.save()
        log_budget_audit(self.request, 'budget_allocation_updated', allocation, old=old)

    def perform_destroy(self, instance):
        old = model_to_dict_safe(instance)
        log_budget_audit(self.request, 'budget_allocation_deleted', instance, old=old, extra={'deleted': old})
        instance.delete()

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        allocation = self.get_object()
        old = model_to_dict_safe(allocation)
        submit_budget_object(allocation, request.user)
        log_budget_audit(request, 'budget_allocation_submitted', allocation, old=old)
        return Response(self.get_serializer(allocation).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        allocation = self.get_object()
        old = model_to_dict_safe(allocation)
        approve_budget_object(allocation, request.user)
        log_budget_audit(request, 'budget_allocation_approved', allocation, old=old)
        return Response(self.get_serializer(allocation).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        allocation = self.get_object()
        old = model_to_dict_safe(allocation)
        reason = request.data.get('reason', '')
        reject_budget_object(allocation, request.user, reason)
        log_budget_audit(request, 'budget_allocation_rejected', allocation, old=old, extra={'reason': reason})
        return Response(self.get_serializer(allocation).data)


class BudgetBorrowViewSet(viewsets.ModelViewSet):
    queryset = BudgetBorrow.objects.select_related(
        'from_allocation',
        'from_allocation__project',
        'from_allocation__wbs_node',
        'from_allocation__task',
        'from_allocation__org_unit',
        'to_allocation',
        'to_allocation__project',
        'to_allocation__wbs_node',
        'to_allocation__task',
        'to_allocation__org_unit',
        'destination_project',
        'destination_revision',
        'destination_wbs_node',
        'destination_task',
        'destination_org_unit',
    ).all()
    serializer_class = BudgetBorrowSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        accessible_ids = accessible_project_ids(self.request.user)
        queryset = super().get_queryset().filter(
            Q(from_allocation__project_id__in=accessible_ids) |
            Q(to_allocation__project_id__in=accessible_ids) |
            Q(destination_project_id__in=accessible_ids) |
            Q(from_allocation__project__isnull=True) |
            Q(to_allocation__project__isnull=True) |
            Q(destination_project__isnull=True)
        ).distinct()

        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        from_allocation_id = self.request.query_params.get('from_allocation_id')
        if from_allocation_id:
            queryset = queryset.filter(from_allocation_id=from_allocation_id)
        to_allocation_id = self.request.query_params.get('to_allocation_id')
        if to_allocation_id:
            queryset = queryset.filter(to_allocation_id=to_allocation_id)
        return queryset

    def perform_create(self, serializer):
        budget_borrow = serializer.save(requested_by=self.request.user)
        log_budget_audit(self.request, 'budget_borrow_created', budget_borrow)

    def perform_update(self, serializer):
        old = model_to_dict_safe(serializer.instance)
        budget_borrow = serializer.save()
        log_budget_audit(self.request, 'budget_borrow_updated', budget_borrow, old=old)

    def perform_destroy(self, instance):
        old = model_to_dict_safe(instance)
        log_budget_audit(self.request, 'budget_borrow_deleted', instance, old=old, extra={'deleted': old})
        instance.delete()

    def _create_destination_allocation(self, budget_borrow, user):
        destination = BudgetAllocation(
            funding_source=budget_borrow.from_allocation.funding_source,
            project=budget_borrow.destination_project,
            revision=budget_borrow.destination_revision,
            scope_type=budget_borrow.destination_scope_type,
            wbs_node=budget_borrow.destination_wbs_node,
            task=budget_borrow.destination_task,
            org_unit=budget_borrow.destination_org_unit,
            cost_type=budget_borrow.destination_cost_type,
            allocated_amount=Decimal('0.00'),
            is_borrow_sink=True,
            status='APPROVED',
            description=budget_borrow.destination_description or budget_borrow.reason,
            created_by=user,
            approved_by=user,
            approved_at=timezone.now(),
        )
        clean_budget_object(destination)
        destination.save()
        budget_borrow.to_allocation = destination
        budget_borrow.save(update_fields=['to_allocation'])
        return destination

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        budget_borrow = self.get_object()
        old = model_to_dict_safe(budget_borrow)
        submit_budget_object(budget_borrow, request.user)
        log_budget_audit(request, 'budget_borrow_submitted', budget_borrow, old=old)
        return Response(self.get_serializer(budget_borrow).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        budget_borrow = self.get_object()
        old = model_to_dict_safe(budget_borrow)
        created_destination = None
        with transaction.atomic():
            approve_budget_object(budget_borrow, request.user)
            if not budget_borrow.to_allocation_id:
                created_destination = self._create_destination_allocation(budget_borrow, request.user)
        log_budget_audit(
            request,
            'budget_borrow_approved',
            budget_borrow,
            old=old,
            extra={'created_destination_allocation_id': getattr(created_destination, 'id', None)},
        )
        if created_destination:
            log_budget_audit(
                request,
                'budget_allocation_created_from_borrow',
                created_destination,
                extra={'borrow_id': budget_borrow.id, 'from_allocation_id': budget_borrow.from_allocation_id},
            )
        return Response(self.get_serializer(budget_borrow).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        budget_borrow = self.get_object()
        old = model_to_dict_safe(budget_borrow)
        reason = request.data.get('reason', '')
        reject_budget_object(budget_borrow, request.user, reason)
        log_budget_audit(request, 'budget_borrow_rejected', budget_borrow, old=old, extra={'reason': reason})
        return Response(self.get_serializer(budget_borrow).data)

    @action(detail=True, methods=['post'])
    def settle(self, request, pk=None):
        budget_borrow = self.get_object()
        if not can_approve_budget(request.user):
            raise PermissionDenied('You do not have permission to settle budget borrows.')
        if budget_borrow.status != 'APPROVED':
            raise ValidationError({'status': 'Only approved budget borrows can be settled.'})
        old = model_to_dict_safe(budget_borrow)
        budget_borrow.status = 'SETTLED'
        budget_borrow.settled_by = request.user
        budget_borrow.settled_at = timezone.now()
        clean_budget_object(budget_borrow)
        budget_borrow.save(update_fields=['status', 'settled_by', 'settled_at'])
        log_budget_audit(request, 'budget_borrow_settled', budget_borrow, old=old)
        return Response(self.get_serializer(budget_borrow).data)


class UnfundedForecastCostViewSet(viewsets.ModelViewSet):
    queryset = UnfundedForecastCost.objects.select_related(
        'project',
        'revision',
        'wbs_node',
        'task',
        'org_unit',
        'linked_allocation',
        'linked_allocation__funding_source',
    ).all()
    serializer_class = UnfundedForecastCostSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        accessible_ids = accessible_project_ids(self.request.user)
        queryset = super().get_queryset().filter(
            Q(project_id__in=accessible_ids) |
            Q(project__isnull=True)
        ).distinct()

        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        scope_type = self.request.query_params.get('scope_type')
        if scope_type:
            queryset = queryset.filter(scope_type=scope_type)
        cost_type = self.request.query_params.get('cost_type')
        if cost_type:
            queryset = queryset.filter(cost_type=cost_type)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        date_from = self.request.query_params.get('date_from')
        if date_from:
            queryset = queryset.filter(forecast_date__gte=date_from)
        date_to = self.request.query_params.get('date_to')
        if date_to:
            queryset = queryset.filter(forecast_date__lte=date_to)
        return queryset

    def perform_create(self, serializer):
        forecast = serializer.save(created_by=self.request.user)
        log_budget_audit(self.request, 'unfunded_forecast_created', forecast)

    def perform_update(self, serializer):
        old = model_to_dict_safe(serializer.instance)
        forecast = serializer.save()
        log_budget_audit(self.request, 'unfunded_forecast_updated', forecast, old=old)

    def perform_destroy(self, instance):
        old = model_to_dict_safe(instance)
        log_budget_audit(self.request, 'unfunded_forecast_deleted', instance, old=old, extra={'deleted': old})
        instance.delete()


class CostTransactionViewSet(viewsets.ModelViewSet):
    """ظ…ط¯غŒط±غŒطھ طھط±ط§ع©ظ†ط´â€Œظ‡ط§غŒ ظ…ط§ظ„غŒ ظˆ ظ‡ط²غŒظ†ظ‡â€Œظ‡ط§"""
    queryset = CostTransaction.objects.all().order_by('-transaction_date', '-created_at')
    serializer_class = CostTransactionSerializer
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _parse_bool_param(value, name):
        if value is None or value == '':
            return None
        normalized = str(value).strip().lower()
        if normalized in {'true', '1'}:
            return True
        if normalized in {'false', '0'}:
            return False
        raise ValidationError({name: 'Use one of: true, 1, false, 0.'})

    def get_queryset(self):
        accessible_ids = accessible_project_ids(self.request.user)
        queryset = super().get_queryset().select_related(
            'project',
            'revision',
            'task',
            'financial_plan',
            'financial_plan__task',
            'assignment',
            'assignment__resource',
            'resource',
            'resource_rate',
            'resource_rate__resource',
            'budget_allocation',
        ).prefetch_related(
            'milestone_allocations__milestone',
        ).annotate(
            _has_financial_plan=Exists(TaskFinancialPlan.objects.filter(pk=OuterRef('financial_plan_id'))),
        )
        queryset = queryset.filter(project_id__in=accessible_ids)

        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        transaction_type = self.request.query_params.get('transaction_type')
        if transaction_type:
            queryset = queryset.filter(transaction_type=transaction_type)
        task_id = self.request.query_params.get('task_id')
        if task_id:
            queryset = queryset.filter(task_id=task_id)

        available = self._parse_bool_param(
            self.request.query_params.get('available_for_financial_plan'),
            'available_for_financial_plan',
        )
        if available is True:
            queryset = queryset.filter(
                financial_plan__isnull=True,
            )
        elif available is False:
            queryset = queryset.filter(financial_plan__isnull=False)
        return queryset

    def _allocation_remaining(self, allocation):
        consumed = allocation.consumptions.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        legacy_direct = allocation.transactions.filter(budget_consumptions__isnull=True).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0')
        child_allocated = allocation.child_allocations.aggregate(total=Sum('allocated_amount'))['total'] or Decimal('0')
        borrowed_in = allocation.borrowed_in_records.filter(status='APPROVED').aggregate(total=Sum('amount'))['total'] or Decimal('0')
        borrowed_out = allocation.borrowed_out_records.filter(status='APPROVED').aggregate(total=Sum('amount'))['total'] or Decimal('0')
        return allocation.allocated_amount + borrowed_in - consumed - legacy_direct - child_allocated - borrowed_out

    def _budget_consumption_audit_payload(self, cost_transaction):
        return [
            {
                'budget_consumption_id': consumption.id,
                'budget_allocation_id': consumption.budget_allocation_id,
                'funding_source_id': consumption.budget_allocation.funding_source_id,
                'amount': str(consumption.amount),
            }
            for consumption in cost_transaction.budget_consumptions.select_related(
                'budget_allocation',
                'budget_allocation__funding_source',
            ).order_by('id')
        ]

    def _task_wbs_chain(self, cost_transaction):
        if not cost_transaction.task_id:
            return []

        versions = cost_transaction.task.versions.filter(is_deleted=False)
        if cost_transaction.revision_id:
            task_version = versions.filter(revision_id=cost_transaction.revision_id).first()
        else:
            task_version = versions.filter(
                revision_id=cost_transaction.task.project.current_execution_revision_id
            ).first()
        if not task_version:
            return []

        return list(task_version.wbs_node.get_ancestors(include_self=True).order_by('-level'))

    def _allocation_tree_depth(self, allocation):
        depth = 0
        current = allocation.parent_allocation
        while current:
            depth += 1
            current = current.parent_allocation
        return depth

    def _eligible_budget_allocations(self, cost_transaction):
        base = BudgetAllocation.objects.select_for_update(of=('self',)).select_related('parent_allocation').filter(
            project=cost_transaction.project,
            cost_type=cost_transaction.transaction_type,
            status='APPROVED',
            funding_source__status='APPROVED',
        )

        ordered_allocations = []
        seen = set()

        def append_scope(queryset):
            scoped_allocations = sorted(
                list(queryset.order_by('created_at', 'id')),
                key=lambda allocation: (-self._allocation_tree_depth(allocation), allocation.created_at, allocation.id),
            )
            for allocation in scoped_allocations:
                if allocation.id in seen:
                    continue
                seen.add(allocation.id)
                ordered_allocations.append(allocation)

        if cost_transaction.task_id:
            append_scope(base.filter(scope_type='TASK', task=cost_transaction.task))
            for wbs_node in self._task_wbs_chain(cost_transaction):
                append_scope(base.filter(scope_type='WBS', wbs_node=wbs_node))

        append_scope(base.filter(scope_type='PROJECT'))
        return ordered_allocations

    def _allocate_budget_for_transaction(self, cost_transaction):
        required = Decimal(cost_transaction.amount)
        remaining_to_allocate = required
        first_allocation = None

        for allocation in self._eligible_budget_allocations(cost_transaction):
            available = self._allocation_remaining(allocation)
            if available <= 0:
                continue

            draw_amount = min(available, remaining_to_allocate)
            draw_amount = draw_amount.quantize(Decimal("0.01"))
            BudgetConsumption.objects.create(
                transaction=cost_transaction,
                budget_allocation=allocation,
                amount=draw_amount,
            )
            if first_allocation is None:
                first_allocation = allocation

            remaining_to_allocate -= draw_amount
            if remaining_to_allocate <= 0:
                break

        if remaining_to_allocate > 0:
            raise ValidationError({
                'budget': (
                    f'Insufficient allocated budget. Required {required}, '
                    f'missing {remaining_to_allocate}.'
                )
            })

        CostTransaction.objects.filter(pk=cost_transaction.pk).update(budget_allocation=first_allocation)
        cost_transaction.budget_allocation = first_allocation

    @transaction.atomic
    def perform_create(self, serializer):
        cost_transaction = serializer.save(created_by=self.request.user, budget_allocation=None)
        self._allocate_budget_for_transaction(cost_transaction)
        if cost_transaction.financial_plan_id:
            allocate_cost_transaction_to_milestones(cost_transaction)
        log_budget_audit(
            self.request,
            'cost_transaction_created',
            cost_transaction,
            extra={'budget_consumptions': self._budget_consumption_audit_payload(cost_transaction)},
        )

    @transaction.atomic
    def perform_update(self, serializer):
        cost_transaction = serializer.instance
        old = model_to_dict_safe(cost_transaction)
        old_consumptions = self._budget_consumption_audit_payload(cost_transaction)
        cost_transaction.budget_consumptions.all().delete()
        cost_transaction = serializer.save(budget_allocation=None)
        self._allocate_budget_for_transaction(cost_transaction)
        if cost_transaction.financial_plan_id:
            allocate_cost_transaction_to_milestones(cost_transaction)
        log_budget_audit(
            self.request,
            'cost_transaction_updated',
            cost_transaction,
            old=old,
            extra={
                'old_budget_consumptions': old_consumptions,
                'budget_consumptions': self._budget_consumption_audit_payload(cost_transaction),
            },
        )

    @transaction.atomic
    def perform_destroy(self, instance):
        old = model_to_dict_safe(instance)
        old_consumptions = self._budget_consumption_audit_payload(instance)
        log_budget_audit(
            self.request,
            'cost_transaction_deleted',
            instance,
            old=old,
            extra={'deleted': old, 'budget_consumptions': old_consumptions},
        )
        instance.delete()


class TaskFinancialPlanViewSet(viewsets.ModelViewSet):
    serializer_class = TaskFinancialPlanSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = TaskFinancialPlan.objects.select_related(
            'task', 'task__project', 'created_by',
        ).prefetch_related('milestones__transactions', 'milestones__cost_allocations', 'cost_transactions__milestone_allocations')
        queryset = queryset.filter(task__project_id__in=accessible_project_ids(self.request.user))
        task_id = self.request.query_params.get('task_id')
        if task_id:
            queryset = queryset.filter(task_id=task_id)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    def perform_create(self, serializer):
        task = serializer.validated_data.get('task')
        require_can_edit_project(self.request.user, task.project)
        plan = serializer.save(created_by=self.request.user)
        log_budget_audit(self.request, 'task_financial_plan_created', plan)

    def perform_update(self, serializer):
        plan = serializer.instance
        require_can_edit_project(self.request.user, plan.task.project)
        next_task = serializer.validated_data.get('task')
        if next_task and next_task.project_id != plan.task.project_id:
            require_can_edit_project(self.request.user, next_task.project)
        next_status = serializer.validated_data.get('status')
        if next_status == TaskFinancialPlan.STATUS_ACTIVE:
            raise ValidationError({'status': 'Use the activate action to activate financial plans.'})
        if plan.status == TaskFinancialPlan.STATUS_ACTIVE and next_status not in (None, TaskFinancialPlan.STATUS_SUSPENDED):
            raise ValidationError({'status': 'Use workflow actions to change active financial plans.'})
        old = model_to_dict_safe(plan)
        updated = serializer.save()
        log_budget_audit(self.request, 'task_financial_plan_updated', updated, old=old)

    def perform_destroy(self, instance):
        require_can_edit_project(self.request.user, instance.task.project)
        if instance.status == TaskFinancialPlan.STATUS_ACTIVE or PaymentTransaction.objects.filter(milestone__financial_plan=instance).exists():
            raise ValidationError({'detail': 'Active plans or plans with ledger transactions cannot be deleted.'})
        old = model_to_dict_safe(instance)
        log_budget_audit(self.request, 'task_financial_plan_deleted', instance, old=old, extra={'deleted': old})
        instance.delete()

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        plan = self.get_object()
        require_can_edit_project(request.user, plan.task.project)
        old = model_to_dict_safe(plan)
        plan = activate_plan(plan)
        log_budget_audit(request, 'task_financial_plan_activated', plan, old=old)
        return Response(self.get_serializer(plan).data)

    @action(detail=True, methods=['post'], url_path='transactions')
    def record_plan_payment(self, request, pk=None):
        plan = self.get_object()
        require_can_edit_project(request.user, plan.task.project)
        serializer = PlanPaymentAllocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transactions = allocate_plan_payment(
            plan,
            serializer.validated_data['amount'],
            serializer.validated_data['transaction_date'],
            user=request.user,
            reference_number=serializer.validated_data.get('reference_number', ''),
            description=serializer.validated_data.get('description', ''),
            currency=serializer.validated_data.get('currency') or None,
        )
        for tx in transactions:
            log_budget_audit(request, 'payment_transaction_allocated_created', tx)
        return Response({
            'transactions': PaymentTransactionSerializer(transactions, many=True).data,
            'financial_status': get_task_financial_status(plan.task, user=request.user),
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='refresh-cost-allocations')
    def refresh_cost_allocations(self, request, pk=None):
        plan = self.get_object()
        require_can_edit_project(request.user, plan.task.project)
        summary = refresh_plan_cost_allocations(plan)
        log_budget_audit(request, 'task_financial_plan_cost_allocations_refreshed', plan, extra=summary)
        plan.refresh_from_db()
        return Response({
            'summary': summary,
            'financial_plan': self.get_serializer(plan).data,
            'financial_status': get_task_financial_status(plan.task, user=request.user),
        })

    @action(detail=False, methods=['get'], url_path='status')
    def status_summary(self, request):
        task_id = request.query_params.get('task_id')
        if not task_id:
            raise ValidationError({'task_id': 'task_id is required.'})
        task = get_object_or_404(Task.objects.filter(project_id__in=accessible_project_ids(request.user)), pk=task_id)
        return Response(get_task_financial_status(task, user=request.user))


class TaskDeliveryViewSet(viewsets.ModelViewSet):
    serializer_class = TaskDeliverySerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = TaskDelivery.objects.select_related(
            'project', 'project__owner_unit', 'task',
            'created_by', 'submitted_by', 'approved_by', 'rejected_by', 'cancelled_by',
        ).annotate(
            _attachment_count=Count('attachments', distinct=True),
            _can_review_user=Exists(TaskRole.objects.filter(
                task=OuterRef('task_id'),
                user=self.request.user,
                role__in=['reviewer', 'project manager'],
            ))
        )
        if self.action in {'retrieve', 'submit', 'approve', 'reject', 'cancel'}:
            queryset = queryset.prefetch_related(
                Prefetch('attachments', queryset=TaskDeliveryAttachment.objects.select_related('uploaded_by'))
            )
        queryset = queryset.filter(project_id__in=accessible_project_ids(self.request.user))
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        task_id = self.request.query_params.get('task_id')
        if task_id:
            queryset = queryset.filter(task_id=task_id)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        needs_my_review = str(self.request.query_params.get('needs_my_review', '')).lower()
        if needs_my_review in {'1', 'true', 'yes'}:
            queryset = queryset.filter(status=TaskDelivery.STATUS_SUBMITTED)
            if not is_company_level(self.request.user):
                queryset = queryset.filter(_can_review_user=True)
        search = (self.request.query_params.get('search') or '').strip()
        if search:
            queryset = queryset.filter(
                Q(delivery_reference__icontains=search) |
                Q(description__icontains=search) |
                Q(project__name__icontains=search) |
                Q(task__versions__title__icontains=search) |
                Q(task__versions__wbs_node__wbs_code__icontains=search)
            ).distinct()
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['include_delivery_attachments'] = self.action in {'retrieve', 'submit', 'approve', 'reject', 'cancel'}
        return context

    def _can_review(self, delivery):
        return get_task_delivery_capabilities(delivery, self.request.user)['can_approve']

    def _require_review(self, delivery):
        if not self._can_review(delivery):
            raise PermissionDenied('Only project reviewers, project managers, or company-level users can review task deliveries.')

    def perform_create(self, serializer):
        task = serializer.validated_data.get('task')
        require_can_edit_project(self.request.user, task.project)
        delivery = serializer.save(created_by=self.request.user)
        log_budget_audit(self.request, 'task_delivery_created', delivery)

    def perform_update(self, serializer):
        delivery = serializer.instance
        if not get_task_delivery_capabilities(delivery, self.request.user)['can_edit']:
            raise ValidationError({'status': 'Only draft deliveries can be edited directly.'})
        old = model_to_dict_safe(delivery)
        updated = serializer.save()
        log_budget_audit(self.request, 'task_delivery_updated', updated, old=old)

    def perform_destroy(self, instance):
        require_can_edit_project(self.request.user, instance.project)
        if instance.status != TaskDelivery.STATUS_DRAFT:
            raise ValidationError({'status': 'Only draft deliveries can be deleted.'})
        old = model_to_dict_safe(instance)
        log_budget_audit(self.request, 'task_delivery_deleted', instance, old=old, extra={'deleted': old})
        instance.delete()

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        delivery = self.get_object()
        if not get_task_delivery_capabilities(delivery, request.user)['can_submit']:
            raise PermissionDenied('You do not have permission to submit this task delivery.')
        old = model_to_dict_safe(delivery)
        delivery = submit_task_delivery(delivery, user=request.user)
        log_budget_audit(request, 'task_delivery_submitted', delivery, old=old)
        return Response({
            'delivery': self.get_serializer(delivery).data,
            'financial_status': get_task_financial_status(delivery.task, user=request.user),
        })

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        delivery = self.get_object()
        if not get_task_delivery_capabilities(delivery, request.user)['can_approve']:
            raise PermissionDenied('Only project reviewers, project managers, or company-level users can review task deliveries.')
        old = model_to_dict_safe(delivery)
        delivery, summaries = approve_task_delivery(delivery, user=request.user)
        log_budget_audit(request, 'task_delivery_approved', delivery, old=old, extra={'cost_allocation_summaries': summaries})
        return Response({
            'delivery': self.get_serializer(delivery).data,
            'financial_status': get_task_financial_status(delivery.task, user=request.user),
            'cost_allocation_summaries': summaries,
        })

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        delivery = self.get_object()
        if not get_task_delivery_capabilities(delivery, request.user)['can_reject']:
            raise PermissionDenied('Only project reviewers, project managers, or company-level users can review task deliveries.')
        old = model_to_dict_safe(delivery)
        delivery = reject_task_delivery(delivery, user=request.user, reason=request.data.get('reason', ''))
        log_budget_audit(request, 'task_delivery_rejected', delivery, old=old)
        return Response({
            'delivery': self.get_serializer(delivery).data,
            'financial_status': get_task_financial_status(delivery.task, user=request.user),
        })

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        delivery = self.get_object()
        if not get_task_delivery_capabilities(delivery, request.user)['can_cancel']:
            raise PermissionDenied('You do not have permission to cancel this task delivery.')
        old = model_to_dict_safe(delivery)
        delivery = cancel_task_delivery(delivery, user=request.user)
        log_budget_audit(request, 'task_delivery_cancelled', delivery, old=old)
        return Response({
            'delivery': self.get_serializer(delivery).data,
            'financial_status': get_task_financial_status(delivery.task, user=request.user),
        })

    @action(detail=True, methods=['get', 'post'], url_path='attachments', parser_classes=[MultiPartParser, FormParser, JSONParser])
    def attachments(self, request, pk=None):
        delivery = self.get_object()
        if request.method.lower() == 'get':
            queryset = delivery.attachments.select_related('uploaded_by')
            serializer = TaskDeliveryAttachmentSerializer(queryset, many=True, context=self.get_serializer_context())
            return Response(serializer.data)
        if not get_task_delivery_attachment_capabilities(delivery, request.user)['can_upload']:
            raise PermissionDenied('You do not have permission to upload evidence for this delivery.')
        serializer = TaskDeliveryAttachmentSerializer(data=request.data, context={**self.get_serializer_context(), 'delivery': delivery})
        serializer.is_valid(raise_exception=True)
        attachment = serializer.save()
        log_budget_audit(request, 'task_delivery_attachment_uploaded', delivery, extra={'attachment_id': str(attachment.id)})
        delivery = self.get_queryset().get(pk=delivery.pk)
        response_serializer = self.get_serializer(delivery)
        return Response({
            'attachment': TaskDeliveryAttachmentSerializer(attachment, context=self.get_serializer_context()).data,
            'delivery': response_serializer.data,
        }, status=status.HTTP_201_CREATED)


class TaskDeliveryAttachmentViewSet(viewsets.GenericViewSet):
    serializer_class = TaskDeliveryAttachmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return TaskDeliveryAttachment.objects.select_related(
            'delivery', 'delivery__project', 'delivery__task', 'uploaded_by',
        ).filter(delivery__project_id__in=accessible_project_ids(self.request.user))

    def destroy(self, request, pk=None):
        attachment = self.get_object()
        delivery = attachment.delivery
        if not get_task_delivery_attachment_capabilities(delivery, request.user)['can_delete']:
            raise PermissionDenied('You do not have permission to delete evidence for this delivery.')
        old = model_to_dict_safe(attachment)
        log_budget_audit(request, 'task_delivery_attachment_deleted', delivery, old=old, extra={'attachment_id': str(attachment.id)})
        attachment.delete()
        delivery.refresh_from_db()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        attachment = self.get_object()
        if not can_view_project(request.user, attachment.delivery.project):
            raise Http404
        if not attachment.file:
            raise Http404
        try:
            opened = attachment.file.open('rb')
        except FileNotFoundError:
            raise Http404
        response = FileResponse(
            opened,
            as_attachment=True,
            filename=attachment.original_filename or attachment.file.name,
        )
        if attachment.content_type:
            response['Content-Type'] = attachment.content_type
        return response


class PaymentMilestoneViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentMilestoneSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = PaymentMilestone.objects.select_related('financial_plan', 'financial_plan__task', 'financial_plan__task__project').prefetch_related('transactions', 'cost_allocations')
        queryset = queryset.filter(financial_plan__task__project_id__in=accessible_project_ids(self.request.user))
        plan_id = self.request.query_params.get('financial_plan_id')
        if plan_id:
            queryset = queryset.filter(financial_plan_id=plan_id)
        return queryset

    def _require_manage(self, milestone_or_plan):
        plan = milestone_or_plan.financial_plan if hasattr(milestone_or_plan, 'financial_plan') else milestone_or_plan
        require_can_edit_project(self.request.user, plan.task.project)

    def perform_create(self, serializer):
        plan = serializer.validated_data.get('financial_plan')
        self._require_manage(plan)
        milestone = serializer.save()
        log_budget_audit(self.request, 'payment_milestone_created', milestone)

    def perform_update(self, serializer):
        milestone = serializer.instance
        self._require_manage(milestone)
        old = model_to_dict_safe(milestone)
        updated = serializer.save()
        log_budget_audit(self.request, 'payment_milestone_updated', updated, old=old)

    def perform_destroy(self, instance):
        self._require_manage(instance)
        if instance.transactions.exists() or instance.cost_allocations.exists():
            raise ValidationError({'detail': 'Milestones with ledger or cost allocations cannot be deleted.'})
        old = model_to_dict_safe(instance)
        log_budget_audit(self.request, 'payment_milestone_deleted', instance, old=old, extra={'deleted': old})
        instance.delete()

    @action(detail=True, methods=['post'], url_path='transactions')
    def record_transaction(self, request, pk=None):
        milestone = self.get_object()
        self._require_manage(milestone)
        serializer = PaymentTransactionSerializer(data={**request.data, 'milestone': milestone.pk})
        serializer.is_valid(raise_exception=True)
        tx = register_transaction(
            milestone,
            serializer.validated_data['transaction_type'],
            serializer.validated_data['amount'],
            serializer.validated_data['transaction_date'],
            user=request.user,
            reference_number=serializer.validated_data.get('reference_number', ''),
            description=serializer.validated_data.get('description', ''),
            currency=serializer.validated_data.get('currency') or None,
        )
        log_budget_audit(request, f"payment_transaction_{tx.transaction_type}_created", tx)
        return Response(PaymentTransactionSerializer(tx).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='approve-manual')
    def approve_manual(self, request, pk=None):
        milestone = self.get_object()
        self._require_manage(milestone)
        if milestone.trigger_type != PaymentMilestone.TRIGGER_MANUAL:
            raise ValidationError({'trigger_type': 'Only manual milestones can be manually approved.'})
        old = model_to_dict_safe(milestone)
        milestone.manual_approved = True
        milestone.manual_approved_at = timezone.now()
        milestone.manual_approved_by = request.user
        milestone.save(update_fields=['manual_approved', 'manual_approved_at', 'manual_approved_by', 'updated_at'])
        summary = refresh_plan_cost_allocations(milestone.financial_plan)
        log_budget_audit(request, 'payment_milestone_manual_approved', milestone, old=old, extra=summary)
        return Response({
            'milestone': self.get_serializer(milestone).data,
            'financial_status': get_task_financial_status(milestone.financial_plan.task, user=request.user),
            'cost_allocation_summary': summary,
        })


class PaymentTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PaymentTransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = PaymentTransaction.objects.select_related(
            'milestone',
            'milestone__financial_plan',
            'milestone__financial_plan__task',
            'milestone__financial_plan__task__project',
        )
        queryset = queryset.filter(milestone__financial_plan__task__project_id__in=accessible_project_ids(self.request.user))
        milestone_id = self.request.query_params.get('milestone_id')
        if milestone_id:
            queryset = queryset.filter(milestone_id=milestone_id)
        financial_plan_id = self.request.query_params.get('financial_plan_id')
        if financial_plan_id:
            queryset = queryset.filter(milestone__financial_plan_id=financial_plan_id)
        task_id = self.request.query_params.get('task_id')
        if task_id:
            queryset = queryset.filter(milestone__financial_plan__task_id=task_id)
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(milestone__financial_plan__task__project_id=project_id)
        return queryset
class TaskViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only dropdown tasks scoped to the project's official execution revision."""
    queryset = Task.objects.all()
    serializer_class = TaskDropdownSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        accessible_ids = accessible_project_ids(self.request.user)
        queryset = super().get_queryset().filter(project_id__in=accessible_ids)

        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        execution_revision_ids = Project.objects.filter(
            id__in=accessible_ids,
            current_execution_revision_id__isnull=False,
        ).values_list('current_execution_revision_id', flat=True)
        queryset = queryset.filter(
            versions__revision_id__in=execution_revision_ids,
            versions__is_deleted=False,
        )

        wbs_node_id = self.request.query_params.get('wbs_node_id')
        if wbs_node_id:
            queryset = queryset.filter(
                versions__wbs_node__node_id=wbs_node_id,
                versions__revision_id__in=execution_revision_ids,
                versions__is_deleted=False,
            )

        return queryset.distinct()

class ResourceLevelingPlanViewSet(viewsets.ModelViewSet):
    serializer_class = ResourceLevelingPlanSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        allowed = accessible_project_ids(self.request.user)
        return (
            GlobalLevelingRun.objects
            .filter(Q(executed_by=self.request.user) | Q(plan_projects__project_id__in=allowed))
            .select_related("executed_by")
            .prefetch_related("plan_projects__project", "plan_projects__revision")
            .distinct()
            .order_by("-updated_at")
        )

    @staticmethod
    def _validate_priority_rules(rules):
        valid = {item[0] for item in GlobalLevelingRun.PRIORITY_CRITERIA_CHOICES}
        normalized = []
        seen = set()
        for rule in rules or GlobalLevelingRun.DEFAULT_PRIORITY_RULES:
            criterion = rule.get("criterion")
            direction = rule.get("direction", "asc")
            if criterion not in valid:
                raise ValidationError({"priorityRules": f"Unknown criterion: {criterion}"})
            if criterion in seen:
                raise ValidationError({"priorityRules": f"Duplicate criterion: {criterion}"})
            if direction not in ("asc", "desc"):
                raise ValidationError({"priorityRules": "Direction must be asc or desc."})
            seen.add(criterion)
            normalized.append({"criterion": criterion, "direction": direction})
        return normalized

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        selections = request.data.get("projects") or []
        if not selections:
            raise ValidationError({"projects": "Select at least one project revision."})

        rules = self._validate_priority_rules(request.data.get("priorityRules"))
        parsed_data_date = parse_cpm_data_date(request.data.get("dataDate"))
        plan = GlobalLevelingRun.objects.create(
            name=(request.data.get("name") or "Untitled Leveling Plan").strip(),
            description=request.data.get("description") or "",
            executed_by=request.user,
            data_date=parsed_data_date,
            priority_rules=rules,
            settings=request.data.get("settings") or {},
            status=GlobalLevelingRun.STATUS_DRAFT,
        )

        selected_projects = []
        seen_projects = set()
        allowed = set(accessible_project_ids(request.user))
        for index, selection in enumerate(selections):
            project_id = str(selection.get("projectId") or "")
            revision_id = str(selection.get("revisionId") or "")
            if project_id in seen_projects:
                raise ValidationError({"projects": "A project can only be selected once."})
            if project_id not in {str(value) for value in allowed}:
                raise PermissionDenied("You do not have access to one of the selected projects.")
            revision = get_object_or_404(
                Revision.objects.select_related("project"),
                pk=revision_id,
                project_id=project_id,
                is_deleted=False,
            )
            entry = LevelingPlanProject(
                leveling_run=plan,
                project=revision.project,
                revision=revision,
                priority=int(selection.get("priority") or index + 1),
            )
            entry.full_clean()
            entry.save()
            selected_projects.append(revision.project)
            seen_projects.add(project_id)

        plan.participating_projects.set(selected_projects)
        return Response(
            self.get_serializer(plan).data,
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        plan = self.get_object()
        if plan.status not in (
            GlobalLevelingRun.STATUS_DRAFT,
            GlobalLevelingRun.STATUS_CALCULATED,
        ):
            raise ValidationError({"detail": "Only draft or calculated plans can be edited."})

        for current_entry in plan.plan_projects.select_related("project"):
            require_can_edit_project(request.user, current_entry.project)

        selections = request.data.get("projects")
        if not selections:
            raise ValidationError({"projects": "Select at least one project revision."})

        rules = self._validate_priority_rules(request.data.get("priorityRules"))
        allowed = {str(value) for value in accessible_project_ids(request.user)}
        selected_entries = []
        selected_projects = []
        seen_projects = set()

        for index, selection in enumerate(selections):
            project_id = str(selection.get("projectId") or "")
            revision_id = str(selection.get("revisionId") or "")
            if project_id in seen_projects:
                raise ValidationError({"projects": "A project can only be selected once."})
            if project_id not in allowed:
                raise PermissionDenied("You do not have access to one of the selected projects.")

            revision = get_object_or_404(
                Revision.objects.select_related("project"),
                pk=revision_id,
                project_id=project_id,
                is_deleted=False,
            )
            require_can_edit_project(request.user, revision.project)
            entry = LevelingPlanProject(
                leveling_run=plan,
                project=revision.project,
                revision=revision,
                priority=int(selection.get("priority") or index + 1),
            )
            entry.full_clean(validate_unique=False)
            selected_entries.append(entry)
            selected_projects.append(revision.project)
            seen_projects.add(project_id)

        plan.name = (request.data.get("name") or plan.name).strip()
        if not plan.name:
            raise ValidationError({"name": "Plan name cannot be empty."})
        plan.description = request.data.get("description") or ""
        plan.data_date = parse_cpm_data_date(request.data.get("dataDate"))
        plan.priority_rules = rules
        plan.settings = request.data.get("settings") or {}
        plan.status = GlobalLevelingRun.STATUS_DRAFT
        plan.is_committed = False
        plan.last_run_at = None
        plan.published_at = None
        plan.save(update_fields=[
            "name", "description", "data_date", "priority_rules", "settings",
            "status", "is_committed", "last_run_at", "published_at", "updated_at",
        ])

        plan.plan_projects.all().delete()
        LevelingPlanProject.objects.bulk_create(selected_entries)
        plan.participating_projects.set(selected_projects)
        TaskLevelingMetrics.objects.filter(leveling_run=plan).delete()
        ResourceUsage.objects.filter(leveling_run=plan).delete()

        plan.refresh_from_db()
        return Response(self.get_serializer(plan).data)
    def destroy(self, request, *args, **kwargs):
        plan = self.get_object()
        if plan.status == GlobalLevelingRun.STATUS_PUBLISHED:
            raise ValidationError({"detail": "Archive a published plan instead of deleting it."})
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["get"], url_path="priority-criteria")
    def priority_criteria(self, request):
        return Response([
            {"value": value, "label": label}
            for value, label in GlobalLevelingRun.PRIORITY_CRITERIA_CHOICES
        ])

    @action(detail=True, methods=["post"])
    def simulate(self, request, pk=None):
        plan = self.get_object()
        if plan.status == GlobalLevelingRun.STATUS_PUBLISHED:
            raise ValidationError({"detail": "Published plans are immutable."})
        for entry in plan.plan_projects.select_related("project"):
            require_can_edit_project(request.user, entry.project)

        from .cpmLeveling import MultiProjectLevelingEngine
        result = MultiProjectLevelingEngine(plan).run()
        plan.refresh_from_db()
        return Response({
            "run": result,
            "plan": self.get_serializer(plan).data,
            "result": self._result_payload(plan),
        })

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def publish(self, request, pk=None):
        plan = self.get_object()
        if plan.status != GlobalLevelingRun.STATUS_CALCULATED:
            raise ValidationError({"detail": "Calculate the plan before publishing it."})
        entries = list(plan.plan_projects.select_related("project"))
        for entry in entries:
            require_can_edit_project(request.user, entry.project)

        project_ids = [entry.project_id for entry in entries]
        GlobalLevelingRun.objects.filter(
            status=GlobalLevelingRun.STATUS_PUBLISHED,
            plan_projects__project_id__in=project_ids,
        ).exclude(pk=plan.pk).update(
            status=GlobalLevelingRun.STATUS_ARCHIVED,
            is_committed=False,
        )
        plan.status = GlobalLevelingRun.STATUS_PUBLISHED
        plan.is_committed = True
        plan.published_at = timezone.now()
        plan.save(update_fields=["status", "is_committed", "published_at", "updated_at"])
        return Response(self.get_serializer(plan).data)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        plan = self.get_object()
        plan.status = GlobalLevelingRun.STATUS_ARCHIVED
        plan.is_committed = False
        plan.save(update_fields=["status", "is_committed", "updated_at"])
        return Response(self.get_serializer(plan).data)

    @action(detail=False, methods=["get"], url_path="published-schedule")
    def published_schedule(self, request):
        role_pairs = set(
            TaskRole.objects.filter(user=request.user, role="executor")
            .values_list("revision_id", "task_id")
        )
        if not role_pairs:
            return Response([])

        revision_ids = {pair[0] for pair in role_pairs}
        task_ids = {pair[1] for pair in role_pairs}
        metrics = (
            TaskLevelingMetrics.objects
            .filter(
                leveling_run__status=GlobalLevelingRun.STATUS_PUBLISHED,
                task_version__revision_id__in=revision_ids,
                task_version__task_id__in=task_ids,
            )
            .select_related(
                "leveling_run",
                "task_version__task",
                "task_version__revision__project",
            )
            .order_by("-leveling_run__published_at")
        )

        schedule = []
        seen = set()
        for metric in metrics:
            version = metric.task_version
            key = (version.revision_id, version.task_id)
            if key not in role_pairs or key in seen:
                continue
            seen.add(key)
            schedule.append({
                "planId": str(metric.leveling_run_id),
                "planName": metric.leveling_run.name,
                "publishedAt": metric.leveling_run.published_at,
                "projectId": str(version.revision.project_id),
                "projectName": version.revision.project.name,
                "revisionId": str(version.revision_id),
                "taskId": str(version.task_id),
                "taskVersionId": version.id,
                "originalStart": metric.original_start,
                "originalFinish": metric.original_finish,
                "executionStart": metric.leveled_start,
                "executionFinish": metric.leveled_finish,
                "delayHours": float(metric.leveling_delay_hours),
            })
        return Response(schedule)
    @action(detail=True, methods=["get"])
    def result(self, request, pk=None):
        return Response(self._result_payload(self.get_object()))

    def _result_payload(self, plan):
        metrics = list(
            plan.task_metrics
            .select_related(
                "task_version__task",
                "task_version__revision__project",
            )
            .order_by("leveled_start", "task_version__sequence")
        )
        revision_ids = [entry.revision_id for entry in plan.plan_projects.all()]
        assignments = list(
            Assignment.objects.filter(revision_id__in=revision_ids)
            .select_related("resource")
        )
        assignment_map = defaultdict(list)
        for assignment in assignments:
            assignment_map[(assignment.revision_id, str(assignment.task_id))].append(assignment)

        resource_map = {}
        tasks = []
        project_summary = {}
        for metric in metrics:
            version = metric.task_version
            project = version.revision.project
            assigned = assignment_map.get((version.revision_id, str(version.task_id)), [])
            resources = [{
                "id": assignment.resource_id,
                "name": assignment.resource.name,
                "unitsPercent": float(assignment.units_percent),
                "plannedHours": float(assignment.planned_hours),
            } for assignment in assigned]
            task_payload = {
                "id": metric.id,
                "taskVersionId": version.id,
                "taskId": str(version.task_id),
                "title": version.title,
                "projectId": project.id,
                "projectName": project.name,
                "revisionId": version.revision_id,
                "revisionNumber": version.revision.number,
                "originalStart": metric.original_start,
                "originalFinish": metric.original_finish,
                "leveledStart": metric.leveled_start,
                "leveledFinish": metric.leveled_finish,
                "delayHours": float(metric.leveling_delay_hours),
                "decisionReason": metric.decision_reason,
                "resources": resources,
            }
            tasks.append(task_payload)
            summary = project_summary.setdefault(project.id, {
                "projectId": project.id,
                "projectName": project.name,
                "originalFinish": None,
                "leveledFinish": None,
                "delayedTasks": 0,
            })
            if metric.original_finish and (
                summary["originalFinish"] is None or metric.original_finish > summary["originalFinish"]
            ):
                summary["originalFinish"] = metric.original_finish
            if summary["leveledFinish"] is None or metric.leveled_finish > summary["leveledFinish"]:
                summary["leveledFinish"] = metric.leveled_finish
            if metric.leveling_delay_hours > 0:
                summary["delayedTasks"] += 1

            for resource in resources:
                row = resource_map.setdefault(resource["id"], {
                    "id": resource["id"],
                    "name": resource["name"],
                    "tasks": [],
                    "usage": [],
                })
                row["tasks"].append({
                    **task_payload,
                    "unitsPercent": resource["unitsPercent"],
                    "plannedHours": resource["plannedHours"],
                })

        for usage in plan.resource_usages.select_related("resource").order_by("usage_date"):
            row = resource_map.setdefault(usage.resource_id, {
                "id": usage.resource_id,
                "name": usage.resource.name,
                "tasks": [],
                "usage": [],
            })
            row["usage"].append({
                "date": usage.usage_date,
                "plannedHours": float(usage.planned_hours),
                "capacityHours": float(usage.capacity_hours),
                "remainingCapacity": float(usage.remaining_capacity),
                "loadPercent": (
                    float(usage.planned_hours) / float(usage.capacity_hours) * 100
                    if usage.capacity_hours else 0
                ),
            })

        delayed = [task for task in tasks if task["delayHours"] > 0]
        return {
            "planId": str(plan.id),
            "status": plan.status,
            "summary": {
                "taskCount": len(tasks),
                "resourceCount": len(resource_map),
                "delayedTaskCount": len(delayed),
                "maxDelayHours": max((task["delayHours"] for task in delayed), default=0),
            },
            "projects": list(project_summary.values()),
            "tasks": tasks,
            "resources": sorted(resource_map.values(), key=lambda row: row["name"]),
        }



