from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .models import (
    ActionCompletionSubmission,
    ActionDeadlineChangeRequest,
    ActionDependency,
    ActionProgressReport,
    ActionStatus,
    ApprovalWorkflow,
    Committee,
    CommitteeMembership,
    DependencyType,
    Meeting,
    MeetingAgendaItem,
    MeetingDecision,
    MeetingMinutesVersion,
    MeetingNotification,
    MeetingParticipant,
    MeetingSeries,
    MeetingStatus,
    MeetingType,
    MeetingUnitInvitation,
    ReminderEscalationPolicy,
    Resolution,
    ResolutionAction,
    ResolutionActionRoleAssignment,
)
from .permissions import MeetingObjectPermission, filter_visible_actions, filter_visible_meetings, is_company_admin
from .selectors import executive_dashboard, individual_dashboard, unit_dashboard
from .serializers import (
    ActionCompletionSubmissionSerializer,
    ActionDeadlineChangeRequestSerializer,
    ActionDependencySerializer,
    ActionProgressReportSerializer,
    ApprovalWorkflowSerializer,
    CommitteeMembershipSerializer,
    CommitteeSerializer,
    MeetingAgendaItemSerializer,
    MeetingDecisionSerializer,
    MeetingMinutesVersionSerializer,
    MeetingNotificationSerializer,
    MeetingParticipantSerializer,
    MeetingSeriesSerializer,
    MeetingSerializer,
    MeetingTypeSerializer,
    MeetingUnitInvitationSerializer,
    ReminderEscalationPolicySerializer,
    ResolutionActionRoleAssignmentSerializer,
    ResolutionActionSerializer,
    ResolutionSerializer,
)
from .services import (
    accept_completion,
    add_dependency,
    approve_minutes,
    decide_deadline_change,
    request_completion_revision,
    request_deadline_change,
    request_minutes_revision,
    submit_completion,
    submit_minutes,
    submit_progress,
    transition_meeting,
)


class StandardPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class MeetingBaseViewSet(viewsets.ModelViewSet):
    permission_classes = [MeetingObjectPermission]
    pagination_class = StandardPagination


class ConfigurationViewSet(MeetingBaseViewSet):
    def get_queryset(self):
        qs = self.queryset.all()
        if not is_company_admin(self.request.user) and self.request.method not in ("GET", "HEAD", "OPTIONS"):
            return qs.none()
        return qs


class MeetingTypeViewSet(ConfigurationViewSet):
    queryset = MeetingType.objects.all()
    serializer_class = MeetingTypeSerializer
    search_fields = ("code", "name")


class CommitteeViewSet(ConfigurationViewSet):
    queryset = Committee.objects.select_related("owning_unit", "chairperson", "secretary")
    serializer_class = CommitteeSerializer


class CommitteeMembershipViewSet(ConfigurationViewSet):
    queryset = CommitteeMembership.objects.select_related("committee", "user", "unit")
    serializer_class = CommitteeMembershipSerializer


class ApprovalWorkflowViewSet(ConfigurationViewSet):
    queryset = ApprovalWorkflow.objects.prefetch_related("steps")
    serializer_class = ApprovalWorkflowSerializer


class ReminderEscalationPolicyViewSet(ConfigurationViewSet):
    queryset = ReminderEscalationPolicy.objects.all()
    serializer_class = ReminderEscalationPolicySerializer


class MeetingSeriesViewSet(MeetingBaseViewSet):
    queryset = MeetingSeries.objects.select_related("meeting_type", "committee", "owning_unit", "organizer")
    serializer_class = MeetingSeriesSerializer


class MeetingViewSet(MeetingBaseViewSet):
    serializer_class = MeetingSerializer

    def get_queryset(self):
        qs = Meeting.objects.select_related("meeting_type", "committee", "owning_unit", "project", "organizer", "chairperson", "secretary")
        qs = filter_visible_meetings(qs, self.request.user)
        return _filter_meetings(qs, self.request.query_params)

    @action(detail=True, methods=["post"], url_path="schedule")
    def schedule(self, request, pk=None):
        meeting = transition_meeting(self.get_object(), actor=request.user, target_status=MeetingStatus.SCHEDULED)
        return Response(self.get_serializer(meeting).data)

    @action(detail=True, methods=["post"], url_path="mark-held")
    def mark_held(self, request, pk=None):
        meeting = transition_meeting(self.get_object(), actor=request.user, target_status=MeetingStatus.HELD)
        return Response(self.get_serializer(meeting).data)

    @action(detail=True, methods=["post"], url_path="start-minutes")
    def start_minutes(self, request, pk=None):
        meeting = transition_meeting(self.get_object(), actor=request.user, target_status=MeetingStatus.MINUTES_DRAFTING)
        return Response(self.get_serializer(meeting).data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        meeting = transition_meeting(self.get_object(), actor=request.user, target_status=MeetingStatus.CANCELLED, reason=request.data.get("reason", ""))
        return Response(self.get_serializer(meeting).data)

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        meeting = transition_meeting(self.get_object(), actor=request.user, target_status=MeetingStatus.ARCHIVED)
        return Response(self.get_serializer(meeting).data)


class MeetingChildViewSet(MeetingBaseViewSet):
    parent_field = "meeting"

    def get_queryset(self):
        qs = self.queryset.all()
        meeting_ids = filter_visible_meetings(Meeting.objects.all(), self.request.user).values("id")
        return qs.filter(**{f"{self.parent_field}_id__in": meeting_ids})


class MeetingUnitInvitationViewSet(MeetingChildViewSet):
    queryset = MeetingUnitInvitation.objects.select_related("meeting", "unit", "invited_by")
    serializer_class = MeetingUnitInvitationSerializer


class MeetingParticipantViewSet(MeetingChildViewSet):
    queryset = MeetingParticipant.objects.select_related("meeting", "user", "represented_unit")
    serializer_class = MeetingParticipantSerializer


class MeetingAgendaItemViewSet(MeetingChildViewSet):
    queryset = MeetingAgendaItem.objects.select_related("meeting", "presenter")
    serializer_class = MeetingAgendaItemSerializer


class MeetingMinutesVersionViewSet(MeetingChildViewSet):
    queryset = MeetingMinutesVersion.objects.select_related("meeting", "author", "submitted_by", "approved_by")
    serializer_class = MeetingMinutesVersionSerializer

    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        minute = submit_minutes(self.get_object(), actor=request.user)
        return Response(self.get_serializer(minute).data)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        minute = approve_minutes(self.get_object(), actor=request.user)
        return Response(self.get_serializer(minute).data)

    @action(detail=True, methods=["post"], url_path="request-revision")
    def request_revision(self, request, pk=None):
        minute = request_minutes_revision(self.get_object(), actor=request.user, reason=request.data.get("reason", ""))
        return Response(self.get_serializer(minute).data)


class MeetingDecisionViewSet(MeetingChildViewSet):
    queryset = MeetingDecision.objects.select_related("meeting", "agenda_item", "owner")
    serializer_class = MeetingDecisionSerializer


class ResolutionViewSet(MeetingChildViewSet):
    queryset = Resolution.objects.select_related("meeting", "decision", "owner_unit", "owner")
    serializer_class = ResolutionSerializer


class ResolutionActionViewSet(MeetingBaseViewSet):
    serializer_class = ResolutionActionSerializer

    def get_queryset(self):
        qs = ResolutionAction.objects.select_related("resolution__meeting", "accountable_unit", "accountable_user", "responsible_user", "reviewer", "approver", "project", "task")
        qs = filter_visible_actions(qs, self.request.user)
        return _filter_actions(qs, self.request.query_params)

    @action(detail=True, methods=["post"], url_path="submit-progress")
    def submit_progress(self, request, pk=None):
        report = submit_progress(
            self.get_object(),
            actor=request.user,
            progress_percent=int(request.data.get("progress_percent", 0)),
            work_completed=request.data.get("work_completed", ""),
            next_steps=request.data.get("next_steps", ""),
            blockers=request.data.get("blockers", ""),
            support_required=request.data.get("support_required", ""),
            forecast_completion_date=request.data.get("forecast_completion_date") or None,
        )
        return Response(ActionProgressReportSerializer(report).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="submit-completion")
    def submit_completion(self, request, pk=None):
        submission = submit_completion(self.get_object(), actor=request.user, summary=request.data.get("summary", ""))
        return Response(ActionCompletionSubmissionSerializer(submission).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="convert-to-project-task")
    def convert_to_project_task(self, request, pk=None):
        action_obj = self.get_object()
        action_obj.task_id = request.data.get("task")
        action_obj.execution_mode = "project_task"
        action_obj.full_clean()
        action_obj.save()
        return Response(self.get_serializer(action_obj).data)


class ResolutionActionRoleAssignmentViewSet(MeetingBaseViewSet):
    queryset = ResolutionActionRoleAssignment.objects.select_related("action", "user", "unit")
    serializer_class = ResolutionActionRoleAssignmentSerializer

    def get_queryset(self):
        action_ids = filter_visible_actions(ResolutionAction.objects.all(), self.request.user).values("id")
        return self.queryset.filter(action_id__in=action_ids)


class ActionDependencyViewSet(MeetingBaseViewSet):
    queryset = ActionDependency.objects.select_related("predecessor", "successor")
    serializer_class = ActionDependencySerializer

    def get_queryset(self):
        action_ids = filter_visible_actions(ResolutionAction.objects.all(), self.request.user).values("id")
        return self.queryset.filter(Q(predecessor_id__in=action_ids) | Q(successor_id__in=action_ids))

    def perform_create(self, serializer):
        visible_actions = filter_visible_actions(ResolutionAction.objects.all(), self.request.user)
        predecessor = get_object_or_404(visible_actions, pk=serializer.validated_data["predecessor"].pk)
        successor = get_object_or_404(visible_actions, pk=serializer.validated_data["successor"].pk)
        serializer.instance = add_dependency(
            predecessor=predecessor,
            successor=successor,
            dependency_type=serializer.validated_data.get("dependency_type", DependencyType.FINISH_TO_START),
            actor=self.request.user,
            description=serializer.validated_data.get("description", ""),
        )


class ActionProgressReportViewSet(MeetingBaseViewSet):
    queryset = ActionProgressReport.objects.select_related("action", "reporter")
    serializer_class = ActionProgressReportSerializer

    def get_queryset(self):
        action_ids = filter_visible_actions(ResolutionAction.objects.all(), self.request.user).values("id")
        return self.queryset.filter(action_id__in=action_ids)


class ActionCompletionSubmissionViewSet(MeetingBaseViewSet):
    queryset = ActionCompletionSubmission.objects.select_related("action", "submitted_by", "reviewed_by")
    serializer_class = ActionCompletionSubmissionSerializer

    def get_queryset(self):
        action_ids = filter_visible_actions(ResolutionAction.objects.all(), self.request.user).values("id")
        return self.queryset.filter(action_id__in=action_ids)

    @action(detail=True, methods=["post"], url_path="accept")
    def accept(self, request, pk=None):
        submission = accept_completion(self.get_object(), actor=request.user, comment=request.data.get("comment", ""))
        return Response(self.get_serializer(submission).data)

    @action(detail=True, methods=["post"], url_path="request-revision")
    def request_revision(self, request, pk=None):
        submission = request_completion_revision(self.get_object(), actor=request.user, comment=request.data.get("comment", ""))
        return Response(self.get_serializer(submission).data)


class ActionDeadlineChangeRequestViewSet(MeetingBaseViewSet):
    queryset = ActionDeadlineChangeRequest.objects.select_related("action", "requested_by", "decided_by")
    serializer_class = ActionDeadlineChangeRequestSerializer

    def get_queryset(self):
        action_ids = filter_visible_actions(ResolutionAction.objects.all(), self.request.user).values("id")
        return self.queryset.filter(action_id__in=action_ids)

    def create(self, request, *args, **kwargs):
        action_obj = get_object_or_404(filter_visible_actions(ResolutionAction.objects.all(), request.user), pk=request.data["action"])
        req = request_deadline_change(action_obj, actor=request.user, requested_due_date=request.data["requested_due_date"], reason=request.data.get("reason", ""))
        return Response(self.get_serializer(req).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        req = decide_deadline_change(self.get_object(), actor=request.user, approved=True, reason=request.data.get("reason", ""))
        return Response(self.get_serializer(req).data)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        req = decide_deadline_change(self.get_object(), actor=request.user, approved=False, reason=request.data.get("reason", ""))
        return Response(self.get_serializer(req).data)


class MeetingNotificationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = MeetingNotificationSerializer
    permission_classes = [MeetingObjectPermission]
    pagination_class = StandardPagination

    def get_queryset(self):
        return MeetingNotification.objects.filter(recipient=self.request.user)


class MeetingReportViewSet(viewsets.ViewSet):
    permission_classes = [MeetingObjectPermission]

    @action(detail=False, methods=["get"], url_path="individual")
    def individual(self, request):
        return Response(individual_dashboard(request.user))

    @action(detail=False, methods=["get"], url_path="unit")
    def unit(self, request):
        return Response(unit_dashboard(request.user, unit_id=request.query_params.get("unit")))

    @action(detail=False, methods=["get"], url_path="executive")
    def executive(self, request):
        return Response(executive_dashboard(request.user))


def _filter_meetings(qs, params):
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("meeting_type"):
        qs = qs.filter(meeting_type_id=params["meeting_type"])
    if params.get("committee"):
        qs = qs.filter(committee_id=params["committee"])
    if params.get("project"):
        qs = qs.filter(project_id=params["project"])
    if params.get("unit"):
        qs = qs.filter(owning_unit_id=params["unit"])
    if params.get("confidentiality"):
        qs = qs.filter(confidentiality=params["confidentiality"])
    if params.get("date_from"):
        qs = qs.filter(planned_start__date__gte=params["date_from"])
    if params.get("date_to"):
        qs = qs.filter(planned_start__date__lte=params["date_to"])
    if params.get("search"):
        term = params["search"]
        qs = qs.filter(Q(title__icontains=term) | Q(meeting_number__icontains=term))
    return qs


def _filter_actions(qs, params):
    today = timezone.localdate()
    for key in ("status", "priority", "confidentiality"):
        if params.get(key):
            qs = qs.filter(**{key: params[key]})
    if params.get("unit"):
        qs = qs.filter(accountable_unit_id=params["unit"])
    if params.get("user"):
        qs = qs.filter(Q(accountable_user_id=params["user"]) | Q(responsible_user_id=params["user"]))
    if params.get("project"):
        qs = qs.filter(project_id=params["project"])
    if params.get("overdue") == "true":
        qs = qs.exclude(status__in=[ActionStatus.CLOSED, ActionStatus.CANCELLED]).filter(current_due_date__lt=today)
    if params.get("blocked") == "true":
        qs = qs.filter(status=ActionStatus.BLOCKED)
    if params.get("due_from"):
        qs = qs.filter(current_due_date__gte=params["due_from"])
    if params.get("due_to"):
        qs = qs.filter(current_due_date__lte=params["due_to"])
    if params.get("search"):
        qs = qs.filter(Q(title__icontains=params["search"]) | Q(description__icontains=params["search"]))
    return qs
