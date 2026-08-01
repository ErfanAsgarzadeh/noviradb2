from rest_framework import serializers

from .models import (
    ActionCompletionSubmission,
    ActionDeadlineChangeRequest,
    ActionDependency,
    ActionProgressReport,
    ApprovalWorkflow,
    ApprovalWorkflowStep,
    Committee,
    CommitteeMembership,
    Meeting,
    MeetingAgendaItem,
    MeetingDecision,
    MeetingMinutesVersion,
    MeetingNotification,
    MeetingParticipant,
    MeetingSeries,
    MeetingType,
    MeetingUnitInvitation,
    ReminderEscalationPolicy,
    Resolution,
    ResolutionAction,
    ResolutionActionRoleAssignment,
)


class MeetingTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingType
        fields = "__all__"


class CommitteeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Committee
        fields = "__all__"


class CommitteeMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommitteeMembership
        fields = "__all__"


class ApprovalWorkflowStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalWorkflowStep
        fields = "__all__"


class ApprovalWorkflowSerializer(serializers.ModelSerializer):
    steps = ApprovalWorkflowStepSerializer(many=True, read_only=True)

    class Meta:
        model = ApprovalWorkflow
        fields = "__all__"


class ReminderEscalationPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = ReminderEscalationPolicy
        fields = "__all__"


class MeetingSeriesSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingSeries
        fields = "__all__"


class MeetingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Meeting
        fields = "__all__"
        read_only_fields = ("meeting_number", "status", "created_at", "updated_at")


class MeetingUnitInvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingUnitInvitation
        fields = "__all__"


class MeetingParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingParticipant
        fields = "__all__"


class MeetingAgendaItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingAgendaItem
        fields = "__all__"


class MeetingMinutesVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingMinutesVersion
        fields = "__all__"
        read_only_fields = ("status", "submitted_by", "submitted_at", "approved_by", "approved_at", "locked_at", "created_at", "updated_at")


class MeetingDecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingDecision
        fields = "__all__"


class ResolutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resolution
        fields = "__all__"
        read_only_fields = ("closed_at",)


class ResolutionActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResolutionAction
        fields = "__all__"
        read_only_fields = ("status", "progress_percent", "closed_at", "closed_by", "created_at", "updated_at")


class ResolutionActionRoleAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResolutionActionRoleAssignment
        fields = "__all__"


class ActionDependencySerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionDependency
        fields = "__all__"


class ActionProgressReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionProgressReport
        fields = "__all__"
        read_only_fields = ("reporter", "created_at", "updated_at")


class ActionCompletionSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionCompletionSubmission
        fields = "__all__"
        read_only_fields = ("submitted_by", "submitted_at", "status", "reviewed_by", "reviewed_at", "review_comment", "created_at", "updated_at")


class ActionDeadlineChangeRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActionDeadlineChangeRequest
        fields = "__all__"
        read_only_fields = ("requested_by", "current_due_date_at_request", "status", "decided_by", "decided_at", "decision_reason", "created_at", "updated_at")


class MeetingNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeetingNotification
        fields = "__all__"
        read_only_fields = ("recipient", "idempotency_key", "created_at", "updated_at")
