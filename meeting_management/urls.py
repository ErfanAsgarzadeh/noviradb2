from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ActionCompletionSubmissionViewSet,
    ActionDeadlineChangeRequestViewSet,
    ActionDependencyViewSet,
    ActionProgressReportViewSet,
    ApprovalWorkflowViewSet,
    CommitteeMembershipViewSet,
    CommitteeViewSet,
    MeetingAgendaItemViewSet,
    MeetingDecisionViewSet,
    MeetingMinutesVersionViewSet,
    MeetingNotificationViewSet,
    MeetingParticipantViewSet,
    MeetingReportViewSet,
    MeetingSeriesViewSet,
    MeetingTypeViewSet,
    MeetingUnitInvitationViewSet,
    MeetingViewSet,
    ReminderEscalationPolicyViewSet,
    ResolutionActionRoleAssignmentViewSet,
    ResolutionActionViewSet,
    ResolutionViewSet,
)


class OptionalSlashRouter(DefaultRouter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trailing_slash = "/?"


router = OptionalSlashRouter()
router.register("meeting-types", MeetingTypeViewSet, basename="meeting-type")
router.register("committees", CommitteeViewSet, basename="committee")
router.register("committee-memberships", CommitteeMembershipViewSet, basename="committee-membership")
router.register("meeting-series", MeetingSeriesViewSet, basename="meeting-series")
router.register("meetings", MeetingViewSet, basename="meeting")
router.register("unit-invitations", MeetingUnitInvitationViewSet, basename="meeting-unit-invitation")
router.register("participants", MeetingParticipantViewSet, basename="meeting-participant")
router.register("agenda-items", MeetingAgendaItemViewSet, basename="meeting-agenda-item")
router.register("minute-versions", MeetingMinutesVersionViewSet, basename="meeting-minute-version")
router.register("decisions", MeetingDecisionViewSet, basename="meeting-decision")
router.register("resolutions", ResolutionViewSet, basename="meeting-resolution")
router.register("actions", ResolutionActionViewSet, basename="meeting-action")
router.register("role-assignments", ResolutionActionRoleAssignmentViewSet, basename="meeting-action-role")
router.register("dependencies", ActionDependencyViewSet, basename="meeting-action-dependency")
router.register("progress-reports", ActionProgressReportViewSet, basename="meeting-progress-report")
router.register("completion-submissions", ActionCompletionSubmissionViewSet, basename="meeting-completion-submission")
router.register("deadline-change-requests", ActionDeadlineChangeRequestViewSet, basename="meeting-deadline-change")
router.register("workflows", ApprovalWorkflowViewSet, basename="meeting-workflow")
router.register("reminder-policies", ReminderEscalationPolicyViewSet, basename="meeting-reminder-policy")
router.register("notifications", MeetingNotificationViewSet, basename="meeting-notification")
router.register("reports", MeetingReportViewSet, basename="meeting-report")

urlpatterns = [
    path("", include(router.urls)),
]
