from django.contrib import admin

from .models import (
    ActionCompletionSubmission,
    ActionDeadlineChangeRequest,
    ActionDependency,
    ActionProgressReport,
    ApprovalWorkflow,
    ApprovalWorkflowStep,
    Committee,
    CommitteeMembership,
    MeetingDecision,
    Meeting,
    MeetingAgendaItem,
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


@admin.register(MeetingType, ReminderEscalationPolicy)
class CodedConfigurationAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_at")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


class ApprovalWorkflowStepInline(admin.TabularInline):
    model = ApprovalWorkflowStep
    extra = 0


@admin.register(ApprovalWorkflow)
class ApprovalWorkflowAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "meeting_type", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active", "meeting_type")
    inlines = [ApprovalWorkflowStepInline]


class CommitteeMembershipInline(admin.TabularInline):
    model = CommitteeMembership
    extra = 0


@admin.register(Committee)
class CommitteeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "owning_unit", "chairperson", "secretary", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active", "confidentiality")
    inlines = [CommitteeMembershipInline]


class MeetingParticipantInline(admin.TabularInline):
    model = MeetingParticipant
    extra = 0


class MeetingUnitInvitationInline(admin.TabularInline):
    model = MeetingUnitInvitation
    extra = 0


class MeetingAgendaItemInline(admin.TabularInline):
    model = MeetingAgendaItem
    extra = 0


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("meeting_number", "title", "status", "meeting_type", "owning_unit", "planned_start")
    search_fields = ("meeting_number", "title")
    list_filter = ("status", "meeting_type", "confidentiality")
    date_hierarchy = "planned_start"
    inlines = [MeetingUnitInvitationInline, MeetingParticipantInline, MeetingAgendaItemInline]


@admin.register(MeetingSeries)
class MeetingSeriesAdmin(admin.ModelAdmin):
    list_display = ("title", "meeting_type", "committee", "owning_unit", "organizer", "is_active")
    search_fields = ("title",)
    list_filter = ("meeting_type", "is_active")


@admin.register(MeetingMinutesVersion)
class MeetingMinutesVersionAdmin(admin.ModelAdmin):
    list_display = ("meeting", "version_number", "status", "is_current", "submitted_at", "approved_at")
    search_fields = ("meeting__meeting_number", "meeting__title", "title")
    list_filter = ("status", "is_current")


@admin.register(MeetingDecision)
class MeetingDecisionAdmin(admin.ModelAdmin):
    list_display = ("meeting", "decision_number", "title", "owner", "decided_at")
    search_fields = ("title", "meeting__meeting_number")
    list_filter = ("confidentiality",)


class ResolutionActionInline(admin.TabularInline):
    model = ResolutionAction
    extra = 0


@admin.register(Resolution)
class ResolutionAdmin(admin.ModelAdmin):
    list_display = ("meeting", "resolution_number", "title", "status", "priority", "owner_unit")
    search_fields = ("title", "meeting__meeting_number")
    list_filter = ("status", "priority", "confidentiality")
    inlines = [ResolutionActionInline]


class ResolutionActionRoleAssignmentInline(admin.TabularInline):
    model = ResolutionActionRoleAssignment
    extra = 0


@admin.register(ResolutionAction)
class ResolutionActionAdmin(admin.ModelAdmin):
    list_display = ("title", "resolution", "action_number", "status", "priority", "accountable_unit", "responsible_user", "current_due_date")
    search_fields = ("title", "resolution__title")
    list_filter = ("status", "priority", "confidentiality")
    inlines = [ResolutionActionRoleAssignmentInline]


@admin.register(ActionProgressReport)
class ActionProgressReportAdmin(admin.ModelAdmin):
    list_display = ("action", "reporter", "reporting_date", "progress_percent")
    search_fields = ("action__title", "reporter__username")
    list_filter = ("reporting_date",)


@admin.register(ActionCompletionSubmission)
class ActionCompletionSubmissionAdmin(admin.ModelAdmin):
    list_display = ("action", "submitted_by", "status", "submitted_at", "reviewed_by")
    search_fields = ("action__title", "submitted_by__username")
    list_filter = ("status",)


@admin.register(ActionDeadlineChangeRequest)
class ActionDeadlineChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("action", "requested_by", "requested_due_date", "status", "decided_by")
    search_fields = ("action__title", "requested_by__username")
    list_filter = ("status",)


@admin.register(ActionDependency)
class ActionDependencyAdmin(admin.ModelAdmin):
    list_display = ("predecessor", "successor", "dependency_type")
    list_filter = ("dependency_type",)


@admin.register(MeetingNotification)
class MeetingNotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "notification_type", "title", "status", "created_at")
    search_fields = ("recipient__username", "title", "idempotency_key")
    list_filter = ("notification_type", "status")
