from os import name

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from django.http import FileResponse, HttpResponse
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.db import transaction
from django.db.models import Q, Sum, Prefetch, F
from django.core.exceptions import ValidationError as DjangoValidationError
from datetime import date, timedelta, datetime
from decimal import Decimal
from collections import defaultdict

from rest_framework.views import APIView

from .cpm import CPMCycleError, CPMEngine
# ایمپورت تمامی مدل‌های مورد نیاز
from .models import Project, Revision, WBSNodeVersion, TaskVersion, Dependency, SubprojectDependency, TaskRole, Task, WBSNode, TaskReportLog, \
    TaskActual, TaskChatMessage, Assignment, Resource, ResourcePool, ResourceRole, ResourceSkill, ResourceSkillMapping, \
    ResourceException, ResourceRate, VarianceReport, Calendar, ProjectViewer, SystemSettings, UnitOfMeasure, \
    ExpenseType, FundingSource, BudgetAllocation, BudgetBorrow, UnfundedForecastCost, CostTransaction, TaskReportAttachment, BudgetConsumption, \
    GlobalLevelingRun, LevelingPlanProject, TaskLevelingMetrics, ResourceUsage
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
    CostTransactionSerializer, TaskDropdownSerializer, ResourceLevelingPlanSerializer
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
from .permissions import (
    can_create_project, can_edit_project, require_can_create_project,
    require_can_edit_project, is_company_level, is_system_admin,
    accessible_project_ids, accessible_projects, can_view_project,
    require_can_manage_viewers,
)
from auditlog.services import diff_dicts, log_event, model_to_dict_safe
from django.contrib.auth import get_user_model
User = get_user_model()


def can_approve_budget(user):
    role = getattr(user, 'org_role', '') or ''
    return user.is_superuser or user.is_staff or role in {'company_admin', 'company_pm'}


def clean_budget_object(obj):
    try:
        obj.full_clean()
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)


def get_active_revision(project):
    return (
        Revision.objects.filter(project=project, is_deleted=False, approved_at__isnull=True).order_by('-number').first()
        or Revision.objects.filter(project=project, is_deleted=False).order_by('-number').first()
    )


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
    گارد ترکیبی برای ویرایش زمان‌بندی:
    1) نسخه نباید قفل (approved) باشد.
    2) اگر کاربر داده شود، باید مجوز ویرایش پروژه را داشته باشد.

    رفتار قدیمی (فقط با revision) برای حفظ سازگاری حفظ شده است.
    """
    if revision.approved_at is not None:
        raise PermissionDenied("این نسخه قفل شده است و قابل تغییر نیست.")
    if user is not None:
        require_can_edit_project(user, revision.project)


def check_can_edit_revision(user, revision):
    """نسخه‌ی صریح‌تر برای استفاده‌های جدید."""
    check_revision_is_open(revision, user)


class ProjectViewSet(viewsets.ModelViewSet):
    """مدیریت پروژه‌ها"""
    queryset = Project.objects.filter(is_deleted=False).exclude(name='System-Personal-Tasks')
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # فقط پروژه‌هایی که کاربر اجازهٔ مشاهده دارد (سطحِ شرکت → همه).
        return super().get_queryset().filter(
            id__in=accessible_project_ids(self.request.user)
        )

    def perform_create(self, serializer):
        user = self.request.user
        require_can_create_project(user)
        # مدیر واحد → پروژه به واحد خودش گره می‌خورد
        owner_unit = getattr(user, 'unit', None)
        serializer.save(created_by=user, owner_unit=owner_unit)

    def perform_update(self, serializer):
        require_can_edit_project(self.request.user, serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        require_can_edit_project(self.request.user, instance)
        instance.is_deleted = True
        instance.save()


class ProjectViewerViewSet(viewsets.ModelViewSet):
    """
    مدیریتِ مشاهده‌گرهای پروژه (Project Viewers).
    افزودن/حذفِ مشاهده‌گر فقط توسطِ سازندهٔ پروژه (و سطحِ شرکت) مجاز است.
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
    """تعریف و مدیریت تقویم‌های کاری مستقل (ساعات کاری + تعطیلات)"""
    queryset = Calendar.objects.all().prefetch_related('intervals', 'exceptions')
    serializer_class = CalendarSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        # فقط قالب‌های مستقل (بدون پروژه) در صورت درخواست
        if self.request.query_params.get('templates') == 'true':
            queryset = queryset.filter(project__isnull=True)
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset


class RevisionViewSet(viewsets.ModelViewSet):
    """مدیریت نسخه‌ها (Revisions) با قابلیت فیلتر بر اساس پروژه"""
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
        instance.is_deleted = True
        instance.save()
    # --- متد قفل کردن نسخه ---
    @action(detail=True, methods=['post'], url_path='approve')
    def approve_revision(self, request, pk=None):
        revision = self.get_object()

        if revision.approved_at:
            return Response({"detail": "این نسخه قبلاً تایید و قفل شده است."}, status=status.HTTP_400_BAD_REQUEST)

        # فقط تاییدکننده‌ی تعیین‌شده (یا admin) می‌تواند تایید کند
        from .permissions import require_can_approve_revision
        require_can_approve_revision(request.user, revision)

        revision.approved_by = request.user
        revision.approved_at = timezone.now()
        revision.save()

        return Response({"detail": "نسخه با موفقیت قفل شد."}, status=status.HTTP_200_OK)

    # --- ارسال اطلاعات به گانت‌چارت ---
    @action(detail=True, methods=['get'], url_path='gantt-data')
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

    # --- ساخت پیش‌نویس (Draft) از یک نسخه ---
    @action(detail=True, methods=['post'], url_path='create-draft')
    @transaction.atomic
    def create_draft_from_revision(self, request, pk=None):
        base_revision = self.get_object()

        # فقط کسی که اجازه ویرایش پروژه را دارد می‌تواند پیش‌نویس بسازد
        require_can_edit_project(request.user, base_revision.project)

        if not base_revision.approved_at:
            return Response(
                {"detail": "نسخه پایه هنوز باز است. ابتدا آن را قفل کنید."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # دریافت و اعتبارسنجی توضیحات (اجباری)
        description = request.data.get('description', '').strip()
        if not description:
            return Response(
                {"detail": "وارد کردن توضیحات (دلیل ساخت پیش‌نویس) الزامی است."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # تعیینِ Approver در زمانِ ساختِ نسخه — scope-aware:
        # شرکتی → پیش‌فرض = مدیرِ برنامه‌ریزی
        # درون‌واحدی → پیش‌فرض = مدیرِ واحدِ صاحبِ پروژه
        # override دستی همیشه ممکن است (approverId / approver_id)
        from .permissions import get_planning_manager as _get_pm
        approver_id = None
        if approver_id:
            approver = get_object_or_404(User, pk=approver_id)
        elif getattr(base_revision.project, 'scope', 'intra_unit') == 'company':
            approver = _get_pm() or request.user
        else:
            # درون‌واحدی: مدیرِ واحدِ صاحبِ پروژه → fallback به سازنده
            ou = getattr(base_revision.project, 'owner_unit', None)
            approver = (ou.manager if ou and ou.manager else request.user)

        approver = base_revision.project.get_default_approver()

        new_revision_number = Revision.objects.filter(project=base_revision.project).count() + 1
        new_revision = Revision.objects.create(
            project=base_revision.project,
            number=new_revision_number,
            description=description,
            project_start=base_revision.project_start,
            created_by=request.user,
            designated_approver=approver,
        )

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

        old_tasks = TaskVersion.objects.filter(revision=base_revision, is_deleted=False)
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

    @action(detail=True, methods=['post'], url_path='run-cpm')
    def run_cpm_engine(self, request, pk=None):
        """
        اجرای موتور محاسباتی زمان‌بندی (CPM) روی یک نسخه خاص
        """
        revision = self.get_object()

        # بررسی اینکه آیا نسخه باز است و قابلیت ویرایش دارد یا خیر
        check_revision_is_open(revision, request.user)

        try:
            # اجرای موتور CPM که Early/Late start و finish ها را حساب و ذخیره می‌کند

            data_date = parse_cpm_data_date(request.data.get("dataDate"))
            engine = CPMEngine(revision, data_date=data_date)
            cpm_result = engine.run()

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

            # پس از محاسبه، مستقیماً داده‌های آپدیت‌شده گانت‌چارت را استخراج کرده و برمی‌گردانیم
            # این کار باعث می‌شود فرانت‌اند نیاز به Request دوم نداشته باشد
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
            # این خطا معمولاً به خاطر وجود حلقه (Cycle) در گراف وابستگی‌ها پرتاب می‌شود
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"detail": f"خطای پیش‌بینی نشده در محاسبات CPM: {str(e)}"},
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

        # گرفتن ریویژن از آدرس در صورت وجود
        revision_id = self.request.query_params.get('revision_id')

        filter_kwargs = {self.lookup_field: lookup_value}
        if revision_id:
            filter_kwargs['revision_id'] = revision_id
        else:
            # پیدا کردن ردیف در نسخه‌ای که هنوز تایید و قفل نشده است
            filter_kwargs['revision__approved_at__isnull'] = True

        # استفاده از first() برای جلوگیری از ارور تعدد ردیف
        obj = queryset.filter(**filter_kwargs).first()

        if not obj:
            from django.http import Http404
            raise Http404("گره WBS در نسخه فعال یافت نشد.")

        self.check_object_permissions(self.request, obj)
        return obj
    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(revision__project_id__in=accessible_project_ids(self.request.user))
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(revision__project_id=project_id)
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        elif project_id:
            revision = (
                Revision.objects.filter(project_id=project_id, approved_at__isnull=True).order_by('-number').first()
                or Revision.objects.filter(project_id=project_id).order_by('-number').first()
            )
            if revision:
                queryset = queryset.filter(revision=revision)
        else:
            queryset = queryset.filter(revision__approved_at__isnull=True)
        return queryset

    # --- هندل کردن ساخت صحیح گره WBS ---
    def perform_create(self, serializer):
        revision_id = self.request.data.get('revisionId') or self.request.query_params.get('revision_id')
        if not revision_id:
            raise ValidationError({"revisionId": "آیدی نسخه برای ساخت گره الزامی است."})

        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision, self.request.user)

        # پیدا کردن گره والد (در صورت وجود)
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
        # بررسی قفل نبودن نسخه
        check_revision_is_open(instance.revision, self.request.user)
        if instance.parent_id is None:
            raise ValidationError({"detail": "The root WBS node cannot be deleted."})

        # ۱. گرفتن خود گره و تمامی زیرمجموعه‌های آن (فرزندان، نوه‌ها و...) به کمک MPTT
        descendants = instance.get_descendants(include_self=True)

        # ۲. مخفی کردن تمام تسک‌هایی که به این گره‌ها (والد یا فرزندان) متصل هستند
        TaskVersion.objects.filter(
            wbs_node__in=descendants,
            revision=instance.revision
        ).update(is_deleted=True)

        # ۳. مخفی کردن خود گره WBS و تمامی گره‌های فرزند آن به صورت یکجا
        descendants.update(is_deleted=True)

    # --- مرتب‌سازی مجدد نودهای WBS (drag & drop) ---
    @action(detail=False, methods=['post'], url_path='reorder')
    @transaction.atomic
    def reorder(self, request):
        """
        ترتیب نمایش نودهای WBS هم‌نیا (زیر یک والد) را تغییر می‌دهد.
        ورودی: revisionId و orderedIds (لیست node.id ها به ترتیب جدید).
        به دلیل محدودیت یکتایی (revision, parent, sequence) از روش دو مرحله‌ای
        (آفست موقت سپس مقدار نهایی) استفاده می‌شود تا تداخل پیش نیاید.
        """
        revision_id = request.data.get('revisionId')
        ordered_ids = request.data.get('orderedIds', [])

        if not revision_id or not ordered_ids:
            return Response(
                {"detail": "revisionId و orderedIds الزامی هستند."},
                status=status.HTTP_400_BAD_REQUEST
            )

        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision, request.user)

        # نگاشت node.id → pk نسخه WBS در این ریویژن
        pk_map = {
            str(v.node_id): v.pk
            for v in WBSNodeVersion.objects.filter(revision=revision, node_id__in=ordered_ids)
        }

        # نکته مهم: از .update() استفاده می‌کنیم نه .save()
        # چون مدل MPTT با order_insertion_by=['sequence'] است و save() باعث
        # جابجایی نود در درخت و خطای _make_sibling_of_root_node می‌شود.
        # .update() فقط ستون sequence را آپدیت می‌کند و به ساختار درخت کاری ندارد.

        # مرحله ۱: آفست موقت برای دور زدن محدودیت یکتایی (revision, parent, sequence)
        for i, nid in enumerate(ordered_ids):
            pk = pk_map.get(str(nid))
            if pk:
                WBSNodeVersion.objects.filter(pk=pk).update(sequence=100000 + i)

        # مرحله ۲: مقادیر نهایی ۱..N
        for i, nid in enumerate(ordered_ids):
            pk = pk_map.get(str(nid))
            if pk:
                WBSNodeVersion.objects.filter(pk=pk).update(sequence=i + 1)

        return Response({"detail": "ترتیب نودهای WBS به‌روزرسانی شد."}, status=status.HTTP_200_OK)


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
            filter_kwargs['revision__approved_at__isnull'] = True

        # انتخاب دقیق همان ردیفی که متعلق به نسخه باز است
        obj = queryset.filter(**filter_kwargs).first()

        if not obj:
            from django.http import Http404
            raise Http404("تسک مورد نظر در نسخه فعال یافت نشد.")

        self.check_object_permissions(self.request, obj)
        return obj

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(revision__project_id__in=accessible_project_ids(self.request.user))
        revision_id = self.request.query_params.get('revision_id')
        user_id = self.request.query_params.get('user_id')  # <--- فیلتر جدید

        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)

        # فیلتر کردن تسک‌هایی که این کاربر در آن‌ها نقش دارد
        if user_id:
            queryset = queryset.filter(task__roles__user_id=user_id).distinct()

        return queryset

    # --- هندل کردن ساخت صحیح تسک (گرفتن والد از ریکوئست) ---
    def perform_create(self, serializer):
        revision_id = self.request.data.get('revision_id')
        print(self.request.data)
        print(revision_id)
        if not revision_id:
            raise ValidationError({"revision_id": "آیدی نسخه برای ساخت تسک الزامی است."})

        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision, self.request.user)

        # تسک باید حتما به یک WBS متصل شود
        parent_id = self.request.data.get('parentId')
        if not parent_id:
            raise ValidationError({"parentId": "مشخص کردن گره والد (WBS) برای ساخت تسک الزامی است."})

        wbs_node = get_object_or_404(WBSNodeVersion, node_id=parent_id, revision=revision)

        # تخصیص sequence بر اساس ترتیب ساخت (آخرین + ۱) در همان گره WBS
        # تا ترتیب پیش‌فرض نمایش، ترتیب ایجاد تسک‌ها باشد
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
        # اضافه کردن چک باز بودن نسخه هنگام ایجاد یک Dependency
        revision_id = self.request.data.get('revisionId')
        if not revision_id:
            raise ValidationError({"revisionId": "آیدی نسخه الزامی است."})
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
        instance.delete()  # وابستگی‌ها می‌توانند فیزیکی حذف شوند


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

        if task_id:
            queryset = queryset.filter(task_id=task_id)

        if for_approval == 'true':
            user = self.request.user
            from .models import SystemSettings
            from .permissions import is_planning_manager as _is_pm
            from django.db.models import Q

            # صف بررسی‌کننده: گزارش‌هایی با وضعیت pending که کاربر روی تسکشان reviewer/PM است
            reviewer_q = Q(
                approval_status='pending',
                task__roles__user=user,
                task__roles__role__in=['reviewer', 'project manager'],
            )
            # صف مدیر برنامه‌ریزی: گزارش‌های reviewer_approved از پروژه‌های شرکتی
            planning_q = Q(
                approval_status='reviewer_approved',
                task__project__scope='company',
            ) if _is_pm(user) or is_company_level(user) else Q(pk=None)  # empty

            queryset = queryset.filter(reviewer_q | planning_q).distinct()

        return queryset

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
        # جلوگیری از ویرایش پس از تایید (هر مرحله)
        if report.approval_status != 'pending':
            raise PermissionDenied("این گزارش در حال بررسی یا تایید شده و دیگر قابل ویرایش نیست.")
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

    @action(detail=True, methods=['post'], url_path='approve')
    def approve_report(self, request, pk=None):
        """
        تاییدِ گزارش (دو‌مرحله‌ای):
        - مرحلهٔ ۱: بررسی‌کننده (reviewer / project manager روی تسک) → reviewer_approved
          برای پروژهٔ درون‌واحدی: auto-collapse به final_approved.
        - مرحلهٔ ۲: مدیرِ برنامه‌ریزی (یا company-level) → final_approved (فقط شرکتی).
        - Bypass: اگر SystemSettings.allow_planning_manager_bypass_reviewer فعال باشد،
          مدیرِ برنامه‌ریزی می‌تواند مستقیماً از pending به final_approved ببرد.
        پیشرفت در TaskActual فقط هنگامِ final_approved ثبت می‌شود.
        """
        from .models import SystemSettings
        from .permissions import is_planning_manager as _is_pm

        report = self.get_object()
        user = request.user
        project = report.task.project
        now = timezone.now()

        if report.approval_status == 'final_approved':
            return Response({"detail": "این گزارش قبلاً تایید نهایی شده است."}, status=status.HTTP_400_BAD_REQUEST)
        if report.approval_status == 'rejected':
            return Response({"detail": "این گزارش رد شده و قابلِ تایید نیست."}, status=status.HTTP_400_BAD_REQUEST)

        # ────────── Bypass path ──────────
        if (report.approval_status == 'pending'
                and project.scope == 'company'
                and (_is_pm(user) or is_company_level(user))
                and SystemSettings.current().allow_planning_manager_bypass_reviewer):
            report.approval_status = 'final_approved'
            report.reviewer_approved_by = user
            report.reviewer_approved_at = now
            report.final_approved_by = user
            report.final_approved_at = now
            # سازگاری legacy
            report.is_approved = True
            report.approved_by = user
            report.approved_at = now
            report.save()
            self._commit_progress(report, user)
            return Response({
                "detail": "گزارش با bypass مستقیماً تایید نهایی شد.",
                "approvalStatus": "final_approved",
                "viaBypass": True,
            }, status=status.HTTP_200_OK)

        # ────────── مرحلهٔ ۱: تاییدِ بررسی‌کننده ──────────
        if report.approval_status == 'pending':
            is_reviewer = TaskRole.objects.filter(
                task=report.task, user=user,
                role__in=['reviewer', 'project manager']
            ).exists()
            if not (is_reviewer or is_company_level(user)):
                raise PermissionDenied("فقط بررسی‌کنندهٔ تسک می‌تواند تاییدِ مرحلهٔ اول بدهد.")

            report.reviewer_approved_by = user
            report.reviewer_approved_at = now

            # درون‌واحدی → auto-collapse: همین مرحله نهایی است
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
                    "detail": "گزارش تایید شد و پیشرفت تسک به‌روزرسانی گردید.",
                    "approvalStatus": "final_approved",
                }, status=status.HTTP_200_OK)
            else:
                # شرکتی → منتظرِ تاییدِ نهاییِ مدیرِ برنامه‌ریزی
                report.approval_status = 'reviewer_approved'
                report.save()
                return Response({
                    "detail": "گزارش توسط بررسی‌کننده تایید شد. در انتظار تایید نهایی مدیر برنامه‌ریزی.",
                    "approvalStatus": "reviewer_approved",
                }, status=status.HTTP_200_OK)

        # ────────── مرحلهٔ ۲: تایید نهاییِ مدیر برنامه‌ریزی ──────────
        elif report.approval_status == 'reviewer_approved':
            if project.scope != 'company':
                return Response(
                    {"detail": "این پروژه درون‌واحدی است و نیازی به تایید نهایی جداگانه ندارد."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if not (_is_pm(user) or is_company_level(user)):
                raise PermissionDenied(
                    "تاییدِ نهاییِ گزارش‌های پروژه‌های شرکتی فقط توسط مدیرِ واحدِ برنامه‌ریزی مجاز است."
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
                "detail": "گزارش تایید نهایی شد و پیشرفت تسک به‌روزرسانی گردید.",
                "approvalStatus": "final_approved",
            }, status=status.HTTP_200_OK)

        return Response({"detail": "وضعیت نامعتبر."}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject_report(self, request, pk=None):
        """ردِ گزارش توسط بررسی‌کننده یا مدیر برنامه‌ریزی."""
        report = self.get_object()
        user = request.user

        if report.approval_status == 'final_approved':
            return Response({"detail": "این گزارش قبلاً تایید نهایی شده و قابلِ رد نیست."}, status=status.HTTP_400_BAD_REQUEST)

        reason = request.data.get('reason', '').strip()

        is_reviewer = TaskRole.objects.filter(
            task=report.task, user=user,
            role__in=['reviewer', 'project manager']
        ).exists()
        from .permissions import is_planning_manager as _is_pm
        if not (is_reviewer or _is_pm(user) or is_company_level(user)):
            raise PermissionDenied("شما اجازهٔ رد کردن این گزارش را ندارید.")

        report.approval_status = 'rejected'
        if reason:
            report.notes = f"REJECTED: {reason}\n---\n{report.notes}"
        report.save()
        return Response({"detail": "گزارش رد شد.", "approvalStatus": "rejected"}, status=status.HTTP_200_OK)

    # ────────── Helper: ثبتِ پیشرفت در TaskActual ──────────
    def _commit_progress(self, report, user):
        """ثبتِ پیشرفت فقط هنگامِ final_approved — فراخوانی خارج از این حالت مجاز نیست."""
        active_task_version = TaskVersion.objects.filter(
            task=report.task,
            revision__approved_at__isnull=True,
            is_deleted=False
        ).first()

        if not active_task_version:
            return

        task_actual, _ = TaskActual.objects.get_or_create(
            task_version=active_task_version,
            defaults={'updated_by': user}
        )
        task_actual.progress = report.progress_percent

        # محاسبه خودکار actual_start/finish
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
    """مدیریت نقش‌های تخصیص داده شده به تسک‌ها (Task Roles)"""
    queryset = TaskRole.objects.all()
    serializer_class = TaskRoleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(revision__project_id__in=accessible_project_ids(self.request.user))

        # امکان فیلتر کردن دیتای برگشتی
        revision_id = self.request.query_params.get('revision_id')
        task_id = self.request.query_params.get('taskId')
        user_id = self.request.query_params.get('userId')

        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
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
        # حذف نقش هم با همان منطق نقش (فقط کسی که می‌توانسته بسازد می‌تواند حذف کند)
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
                {"detail": "revisionId، wbsNodeId و userId الزامی هستند."},
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
                {"detail": "در این نود WBS تسکی برای تخصیص reviewer وجود ندارد."},
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
        revision_qs = Revision.objects.filter(project_id__in=accessible_project_ids(request.user), project__is_deleted=False, is_deleted=False, approved_at__isnull=True)
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
        versions = TaskVersion.objects.filter(is_deleted=False, wbs_node__is_deleted=False, revision__is_deleted=False, revision__project__is_deleted=False, revision__approved_at__isnull=True)
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
        افراد قابل انتخاب برای یک نقش روی یک تسک خاص — برای dropdown فرانت.
        پارامترها: ?taskId=<task_id>&role=<reviewer|executor>
        """
        from .permissions import _role as get_role, can_edit_project, is_task_reviewer
        task_id = request.query_params.get('taskId')
        role = request.query_params.get('role', 'reviewer')

        if not task_id:
            return Response({"detail": "taskId الزامی است."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            task = Task.objects.get(pk=task_id)
        except Task.DoesNotExist:
            return Response({"detail": "تسک یافت نشد."}, status=status.HTTP_404_NOT_FOUND)

        actor = request.user
        users = User.objects.none()

        if actor.is_superuser or get_role(actor) == 'company_admin':
            users = User.objects.all()
        elif role == 'reviewer' and can_edit_project(actor, task.project):
            users = User.objects.all()
        elif role == 'executor' and is_task_reviewer(actor, task) and getattr(actor, 'unit_id', None):
            # Executor فقط از واحد مستقیم خود Reviewer (= actor)
            users = User.objects.filter(unit_id=actor.unit_id)

        from CustomUser.serializers import CustomUserSerializer
        return Response(CustomUserSerializer(users.order_by('id'), many=True).data)

    @action(detail=False, methods=['get'], url_path='my-reviewer-tasks')
    def my_reviewer_tasks(self, request):
        """
        لیستِ تمامِ تسک‌هایی که کاربر جاری روی آن‌ها reviewer (یا project manager) است
        — برای صفحهٔ «انتخاب انجام‌دهنده» (Assign Executors).

        پاسخ شامل:
          - tasks: لیست تسک‌ها با اطلاعاتِ پروژه، WBS، تاریخ‌ها و executors فعلی
          - unitMembers: اعضای واحدِ کاربر (برای dropdown انتخاب executor)
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
            revision__approved_at__isnull=True,
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
            node = WBSNodeVersion.objects.filter(node_id=node_id, revision__approved_at__isnull=True, is_deleted=False).first()
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

        # گروه‌بندی بر اساس (revision_id, task_id)
        executors_by_task = {}
        for tr in executor_roles:
            key = (tr.revision_id, tr.task_id)
            executors_by_task.setdefault(key, []).append({
                'taskRoleId': tr.id,
                'userId': tr.user_id,
                'username': tr.user.username,
                'jobTitle': getattr(tr.user, 'job_title', '') or '',
            })

        # ساخت پاسخ هر تسک
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

        # اعضای واحدِ کاربر — dropdown ها از این لیست پر می‌شوند
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
    """Count Mon–Fri days in a list of dates (simplistic; ignores CalendarExceptions)."""
    return sum(1 for d in bucket_dates if d.weekday() < 5)


# ─── view ─────────────────────────────────────────────────────────────────────

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

    UNDERLOAD_THRESHOLD = 50    # % below this → underload
    OVERLOAD_THRESHOLD  = 100   # % above this → overload

    def get(self, request, revision_id):
        # ── 1. Fetch revision ──────────────────────────────────────────────
        try:
            revision = Revision.objects.get(pk=revision_id)
        except Revision.DoesNotExist:
            return Response({"detail": "Revision not found."}, status=status.HTTP_404_NOT_FOUND)

        # محدودسازیِ خواندن: کاربر باید به پروژهٔ این نسخه دسترسیِ مشاهده داشته باشد.
        if not can_view_project(request.user, revision.project):
            return Response({"detail": "شما به این پروژه دسترسی ندارید."}, status=status.HTTP_403_FORBIDDEN)

        granularity = request.query_params.get("granularity", "day")
        if granularity not in ("day", "week", "month"):
            return Response({"detail": "granularity must be day|week|month."}, status=status.HTTP_400_BAD_REQUEST)

        # ── 2. Pull all task versions for this revision ───────────────────
        task_versions = (
            TaskVersion.objects
            .filter(revision=revision, is_deleted=False)
            .exclude(planned_start=None)
            .exclude(planned_finish=None)
            .select_related("task")
        )

        # ── 3. Pull assignments for this revision ─────────────────────────
        assignments = (
            Assignment.objects
            .filter(revision=revision)
            .select_related("resource", "task")
        )

        # Map task_id → TaskVersion for quick lookup
        tv_by_task = {str(tv.task_id): tv for tv in task_versions}

        # ── 4. Determine global window ────────────────────────────────────
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

        # ── 5. Build bucket → list[date] mapping ──────────────────────────
        bucket_dates: dict[str, list[date]] = defaultdict(list)
        for d in all_dates:
            bucket_dates[_bucket_key(d, granularity)].append(d)

        ordered_buckets = list(dict.fromkeys(_bucket_key(d, granularity) for d in all_dates))

        # ── 6. Build per-resource, per-bucket load ────────────────────────
        # Structure: resource_id → bucket_key → { allocated_hours, tasks }
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

        # ── 7. Deduplicate task entries per bucket ────────────────────────
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

        # ── 8. Assemble response ──────────────────────────────────────────
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
        # دریافت project_id و revision_id از درخواست
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
            # فراخوانی تابع اصلاح شده در msp_importer.py
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

        # فیلتر امنیتی/دسترسی (اگر داری)
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
        queryset = queryset.filter(revision__project_id__in=accessible_project_ids(self.request.user))

        revision_id = self.request.query_params.get('revision_id')
        task_id = self.request.query_params.get('task_id')

        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        if task_id:
            queryset = queryset.filter(task_id=task_id)

        return queryset


from django.contrib.auth import get_user_model
User = get_user_model()
class PersonalTaskViewSet(viewsets.ViewSet):
    """
    مدیریت تسک‌های شخصی کاربران که به عنوان یک پروژه سیستمی در بک‌اند ثبت می‌شوند.
    """
    permission_classes = [IsAuthenticated]

    # متد GET برای گرفتن لیست تسک‌های شخصی از سمت فرانت‌اند
    def list(self, request):
        sys_project = Project.objects.filter(name="System-Personal-Tasks").first()
        if not sys_project:
            # اگر پروژه هنوز ساخته نشده، یعنی کاربر هنوز تسکی ایجاد نکرده است
            return Response([], status=status.HTTP_200_OK)

        # پیدا کردن ریویژن فعال و تمام تسک‌هایی که حذف نشده‌اند
        revision = Revision.objects.filter(project=sys_project).latest('created_at')
        tasks = TaskVersion.objects.filter(revision=revision, is_deleted=False)

        # استفاده از سریالایزر گانت‌چارت برای همخوانی ساختار دیتا با فرانت‌اند
        serializer = ActivityNodeSerializer(tasks, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # متد POST برای ایجاد تسک شخصی جدید
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
            return Response({"detail": "تمامی فیلدها (عنوان، تاریخ، مدت‌زمان و کاربر) الزامی است."},
                            status=status.HTTP_400_BAD_REQUEST)

        # ۱. ساخت یا دریافت پروژه سیستمی
        sys_project, created = Project.objects.get_or_create(
            name="System-Personal-Tasks",
            defaults={'created_by': request.user}
        )

        # ۲. دریافت ریویژن (طبق مدل‌های شما، ریویژن صفر خودکار با ساخت پروژه ایجاد می‌شود)
        revision = Revision.objects.filter(project=sys_project).latest('created_at')

        # ۳. مدیریت ساختار WBS برای تسک‌های شخصی
        # مدل WBSNode فیلد نام ندارد، نام در WBSNodeVersion ذخیره می‌شود
        wbs_node_version = WBSNodeVersion.objects.filter(revision=revision, title="My Personal Tasks").first()

        if not wbs_node_version:
            # پیدا کردن گره ریشه که با سیگنال ایجاد شده
            root_wbs = WBSNodeVersion.objects.get(revision=revision, parent__isnull=True)

            # ساخت گره WBS فرزند برای کارهای شخصی
            base_node = WBSNode.objects.create(project=sys_project)
            wbs_node_version = WBSNodeVersion.objects.create(
                node=base_node,
                revision=revision,
                parent=root_wbs,
                title="My Personal Tasks",
                sequence=1
            )

        # ۴. ساخت تسک فیزیکی و نسخه آن
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

        # ۵. ایجاد نقش مجری
        # این کار باعث می‌شود سیگنالی که در signals.py دارید، فوراً کاربر را به جدول Assignment
        # اضافه کند تا برای لولینگ آماده شود.
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


        # ۶. بازگرداندن دیتای تسک با فرمت استاندارد برای نمایش سریع در لیست فرانت‌اند
        serializer = ActivityNodeSerializer(task_ver)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # متد DELETE برای لغو یا پاک کردن تسک شخصی
    def destroy(self, request, pk=None):
        try:
            task_ver = TaskVersion.objects.get(task__id=pk)

            # استفاده از ویژگی Soft Delete که در سیستم شما پیاده‌سازی شده است
            task_ver.is_deleted = True
            task_ver.save()

            # حذف نقش کاربر تا سیگنال remove_executor_assignment در signals.py
            # تریگر شود و منبع را از Assignment پاک کند، تا ظرفیت آزاد شود.
            TaskRole.objects.filter(task__id=pk).delete()

            return Response(status=status.HTTP_204_NO_CONTENT)
        except TaskVersion.DoesNotExist:
            return Response({"detail": "تسک یافت نشد."}, status=status.HTTP_404_NOT_FOUND)


    def partial_update(self, request, pk=None):
        try:
            # پیدا کردن تسک فعلی که حذف نشده باشد
            task_ver = TaskVersion.objects.get(task__id=pk, is_deleted=False)

            # دریافت فیلدهای ارسال شده از سمت کلاینت
            title = request.data.get('title')
            start_date = request.data.get('start_date')
            duration_hours = request.data.get('duration_hours')
            description = request.data.get('description')
            user_id = request.data.get('user_id')

            # اعمال تغییرات روی تسک (در صورت وجود هر فیلد در ریکوئست)
            if title:
                task_ver.title = title
            if start_date:
                task_ver.planned_start = start_date
            if duration_hours:
                task_ver.duration_hours = duration_hours
            if description is not None:  # توضیحات می‌تواند خالی باشد
                task_ver.description = description

            task_ver.save()

            # در صورتی که کاربر مجری تغییر کرده باشد، نقش او را آپدیت می‌کنیم
            if user_id:
                task_role = TaskRole.objects.filter(task=task_ver.task, role='executor').first()
                if task_role:
                    if str(task_role.user_id) != str(user_id):
                        task_role.user_id = user_id
                        task_role.save()
                else:
                    # اگر نقشی از قبل نبود، یکی می‌سازیم
                    TaskRole.objects.create(
                        revision=task_ver.revision,
                        task=task_ver.task,
                        user_id=user_id,
                        role='executor'
                    )

            # استفاده از همان سریالایزری که در لیست و ساخت استفاده کردید
            serializer = ActivityNodeSerializer(task_ver)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except TaskVersion.DoesNotExist:
            return Response({"detail": "تسک یافت نشد."}, status=status.HTTP_404_NOT_FOUND)


class VarianceReportViewSet(viewsets.ModelViewSet):
    """مدیریت گزارش‌های انحراف و اتصال به موتور EVM"""
    queryset = VarianceReport.objects.all()
    serializer_class = VarianceReportSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(revision__project_id__in=accessible_project_ids(self.request.user))
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        return queryset

    @action(detail=False, methods=['post'], url_path='calculate')
    def trigger_calculation(self, request):
        """اجرای دستی موتور محاسباتی برای یک پروژه"""
        project_id = request.data.get('project_id')
        if not project_id:
            return Response({"error": "project_id الزامی است."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # اجرای انجین
            engine = EVMEngine(project_id=project_id)
            engine.run_task_level_variances()
            return Response({"status": "محاسبات با موفقیت انجام شد و دیتابیس به‌روزرسانی گردید."},
                            status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



# ─── SystemSettings endpoint (singleton) ──────────────────────────────────────

class SystemSettingsView(APIView):
    """
    GET: خواندنِ تنظیماتِ کلیِ سیستم (هر کاربرِ احرازشده).
    PUT/PATCH: ویرایش فقط توسطِ سطحِ شرکت (company_admin / company_pm / superuser).
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
            raise PermissionDenied("ویرایشِ تنظیماتِ سیستم فقط برای کاربرانِ سطحِ شرکت مجاز است.")
        settings_obj = SystemSettings.current()
        serializer = SystemSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class UnitOfMeasureViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ویوست برای واحدهای اندازه‌گیری.
    معمولاً واحدها فقط خواندنی (ReadOnly) هستند و از طریق پنل ادمین یا شل اضافه می‌شوند.
    اگر می‌خواهید از طریق API هم قابلیت اضافه کردن داشته باشید، از ModelViewSet استفاده کنید.
    """
    queryset = UnitOfMeasure.objects.all().order_by('name')
    serializer_class = UnitOfMeasureSerializer
    permission_classes = [IsAuthenticated] # در صورت نیاز به احراز هویت

class ExpenseTypeViewSet(viewsets.ModelViewSet):
    """
    ویوست کامل برای مدیریت انواع هزینه‌ها (Expense Types).
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
    """مدیریت تراکنش‌های مالی و هزینه‌ها"""
    queryset = CostTransaction.objects.all().order_by('-transaction_date', '-created_at')
    serializer_class = CostTransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        # فیلتر کردن هزینه‌ها بر اساس پروژه‌هایی که کاربر دسترسی دارد
        queryset = queryset.filter(project_id__in=accessible_project_ids(self.request.user))

        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        transaction_type = self.request.query_params.get('transaction_type')
        if transaction_type:
            queryset = queryset.filter(transaction_type=transaction_type)
        task_id = self.request.query_params.get('task_id')
        if task_id:
            queryset = queryset.filter(task_id=task_id)
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
            task_version = versions.filter(revision__approved_at__isnull=True).order_by('-revision__number').first()
            if not task_version:
                task_version = versions.order_by('-revision__number').first()

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
        base = BudgetAllocation.objects.select_for_update().select_related('parent_allocation').filter(
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


class TaskViewSet(viewsets.ReadOnlyModelViewSet):
    """ویوست فقط‌خواندنی برای تغذیهٔ دراپ‌داونِ تسک‌ها در فرانت‌اند"""
    queryset = Task.objects.all()
    serializer_class = TaskDropdownSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()

        # فیلتر امنیتی: فقط پروژه‌هایی که کاربر به آن‌ها دسترسی دارد
        queryset = queryset.filter(project_id__in=accessible_project_ids(self.request.user))

        # فیلتر بر اساس پروژه انتخابی در فرانت‌اند
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        wbs_node_id = self.request.query_params.get('wbs_node_id')
        if wbs_node_id:
            queryset = queryset.filter(
                versions__wbs_node__node_id=wbs_node_id,
                versions__is_deleted=False,
            ).distinct()

        return queryset


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
