from django.contrib import admin

from .models import (
    ApprovalWorkflow,
    ApprovalWorkflowStep,
    Committee,
    CommitteeMembership,
    Meeting,
    MeetingAgendaItem,
    MeetingMinutesVersion,
    MeetingParticipant,
    MeetingSeries,
    MeetingType,
    MeetingUnitInvitation,
    ReminderEscalationPolicy,
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
