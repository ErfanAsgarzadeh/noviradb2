import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ConfidentialityLevel(models.TextChoices):
    PUBLIC = "public", "Public"
    INTERNAL = "internal", "Internal"
    CONFIDENTIAL = "confidential", "Confidential"
    RESTRICTED = "restricted", "Restricted"


class MeetingStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SCHEDULED = "scheduled", "Scheduled"
    HELD = "held", "Held"
    MINUTES_DRAFTING = "minutes_drafting", "Minutes drafting"
    IN_REVIEW = "in_review", "In review"
    APPROVED = "approved", "Approved"
    ARCHIVED = "archived", "Archived"
    CANCELLED = "cancelled", "Cancelled"


class ParticipationMode(models.TextChoices):
    MANAGER = "manager", "Manager"
    REPRESENTATIVE = "representative", "Representative"
    ALL_MEMBERS = "all_members", "All members"


class InvitationStatus(models.TextChoices):
    INVITED = "invited", "Invited"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    DELEGATED = "delegated", "Delegated"
    CANCELLED = "cancelled", "Cancelled"


class AttendanceStatus(models.TextChoices):
    UNKNOWN = "unknown", "Unknown"
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"
    LATE = "late", "Late"
    LEFT_EARLY = "left_early", "Left early"
    ONLINE = "online", "Online"


class MinuteVersionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REVISION_REQUESTED = "revision_requested", "Revision requested"
    SUPERSEDED = "superseded", "Superseded"


class MeetingType(TimeStampedModel):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    default_confidentiality = models.CharField(
        max_length=20,
        choices=ConfidentialityLevel.choices,
        default=ConfidentialityLevel.INTERNAL,
    )
    requires_minutes_approval = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["is_active", "code"])]

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Committee(TimeStampedModel):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    owning_unit = models.ForeignKey(
        "CustomUser.OrgUnit",
        on_delete=models.PROTECT,
        related_name="meeting_committees",
    )
    chairperson = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chaired_committees",
    )
    secretary = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="secretary_committees",
    )
    confidentiality = models.CharField(
        max_length=20,
        choices=ConfidentialityLevel.choices,
        default=ConfidentialityLevel.INTERNAL,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["owning_unit", "is_active"]),
            models.Index(fields=["confidentiality"]),
        ]

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class CommitteeMembership(TimeStampedModel):
    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="committee_memberships")
    unit = models.ForeignKey("CustomUser.OrgUnit", null=True, blank=True, on_delete=models.PROTECT)
    role = models.CharField(max_length=50, default="member")
    can_view_confidential = models.BooleanField(default=False)
    starts_on = models.DateField(default=timezone.localdate)
    ends_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["committee", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["committee", "user"],
                condition=Q(is_active=True),
                name="mm_unique_active_committee_member",
            ),
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(ends_on__gte=models.F("starts_on")),
                name="mm_committee_member_dates_ordered",
            ),
        ]
        indexes = [models.Index(fields=["committee", "is_active"])]

    def __str__(self):
        return f"{self.user} on {self.committee}"


class ApprovalWorkflow(TimeStampedModel):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    meeting_type = models.ForeignKey(MeetingType, null=True, blank=True, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ApprovalWorkflowStep(TimeStampedModel):
    workflow = models.ForeignKey(ApprovalWorkflow, on_delete=models.CASCADE, related_name="steps")
    sequence = models.PositiveIntegerField()
    role = models.CharField(max_length=50)
    required = models.BooleanField(default=True)
    escalation_after_hours = models.PositiveIntegerField(default=48)

    class Meta:
        ordering = ["workflow", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["workflow", "sequence"], name="mm_unique_workflow_step_sequence"),
        ]

    def __str__(self):
        return f"{self.workflow}: {self.sequence} {self.role}"


class ReminderEscalationPolicy(TimeStampedModel):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    upcoming_deadline_days = models.PositiveIntegerField(default=3)
    progress_report_due_days = models.PositiveIntegerField(default=7)
    overdue_escalation_days = models.PositiveIntegerField(default=1)
    higher_escalation_days = models.PositiveIntegerField(default=7)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class MeetingSeries(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    meeting_type = models.ForeignKey(MeetingType, on_delete=models.PROTECT, related_name="series")
    committee = models.ForeignKey(Committee, null=True, blank=True, on_delete=models.PROTECT, related_name="series")
    owning_unit = models.ForeignKey("CustomUser.OrgUnit", on_delete=models.PROTECT, related_name="meeting_series")
    organizer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="organized_meeting_series")
    recurrence_rule = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["meeting_type", "is_active"]),
            models.Index(fields=["owning_unit", "is_active"]),
        ]

    def __str__(self):
        return self.title


class Meeting(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meeting_number = models.CharField(max_length=80, unique=True, blank=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    meeting_type = models.ForeignKey(MeetingType, on_delete=models.PROTECT, related_name="meetings")
    series = models.ForeignKey(MeetingSeries, null=True, blank=True, on_delete=models.PROTECT, related_name="meetings")
    committee = models.ForeignKey(Committee, null=True, blank=True, on_delete=models.PROTECT, related_name="meetings")
    owning_unit = models.ForeignKey("CustomUser.OrgUnit", on_delete=models.PROTECT, related_name="owned_meetings")
    project = models.ForeignKey("ktcPlanning.Project", null=True, blank=True, on_delete=models.PROTECT, related_name="meetings")
    organizer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="organized_meetings")
    chairperson = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="chaired_meetings")
    secretary = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="secretary_meetings")
    planned_start = models.DateTimeField()
    planned_end = models.DateTimeField()
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=255, blank=True)
    online_link = models.URLField(blank=True)
    status = models.CharField(max_length=30, choices=MeetingStatus.choices, default=MeetingStatus.DRAFT)
    confidentiality = models.CharField(
        max_length=20,
        choices=ConfidentialityLevel.choices,
        default=ConfidentialityLevel.INTERNAL,
    )
    cancellation_reason = models.TextField(blank=True)
    approval_workflow = models.ForeignKey(ApprovalWorkflow, null=True, blank=True, on_delete=models.PROTECT)
    reminder_policy = models.ForeignKey(ReminderEscalationPolicy, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-planned_start", "title"]
        indexes = [
            models.Index(fields=["status", "planned_start"]),
            models.Index(fields=["meeting_type", "planned_start"]),
            models.Index(fields=["committee", "planned_start"]),
            models.Index(fields=["project", "planned_start"]),
            models.Index(fields=["owning_unit", "planned_start"]),
            models.Index(fields=["confidentiality"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(planned_end__gt=models.F("planned_start")), name="mm_meeting_planned_end_after_start"),
            models.CheckConstraint(
                condition=Q(actual_end__isnull=True) | Q(actual_start__isnull=True) | Q(actual_end__gt=models.F("actual_start")),
                name="mm_meeting_actual_end_after_start",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.planned_start and self.planned_end and self.planned_end <= self.planned_start:
            errors["planned_end"] = "Planned end must be after planned start."
        if self.actual_start and self.actual_end and self.actual_end <= self.actual_start:
            errors["actual_end"] = "Actual end must be after actual start."
        if self.status == MeetingStatus.CANCELLED and not self.cancellation_reason:
            errors["cancellation_reason"] = "Cancellation reason is required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.meeting_number:
            prefix = f"MTG-{timezone.now():%Y}-"
            latest = (
                Meeting.objects.filter(meeting_number__startswith=prefix)
                .order_by("-meeting_number")
                .values_list("meeting_number", flat=True)
                .first()
            )
            sequence = int(str(latest).split("-")[-1]) + 1 if latest else 1
            self.meeting_number = f"{prefix}{sequence:06d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.meeting_number} - {self.title}"


class MeetingUnitInvitation(TimeStampedModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="unit_invitations")
    unit = models.ForeignKey("CustomUser.OrgUnit", on_delete=models.PROTECT, related_name="meeting_invitations")
    participation_mode = models.CharField(max_length=20, choices=ParticipationMode.choices, default=ParticipationMode.REPRESENTATIVE)
    status = models.CharField(max_length=20, choices=InvitationStatus.choices, default=InvitationStatus.INVITED)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    represented_unit_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["meeting", "unit_id"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "unit"], name="mm_unique_meeting_unit_invitation"),
        ]
        indexes = [models.Index(fields=["unit", "status"])]

    def save(self, *args, **kwargs):
        if self.unit and not self.represented_unit_name:
            self.represented_unit_name = self.unit.name
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.unit} invited to {self.meeting}"


class MeetingParticipant(TimeStampedModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="meeting_participations")
    external_name = models.CharField(max_length=255, blank=True)
    external_email = models.EmailField(blank=True)
    invited_unit = models.ForeignKey(MeetingUnitInvitation, null=True, blank=True, on_delete=models.SET_NULL, related_name="participants")
    represented_unit = models.ForeignKey("CustomUser.OrgUnit", null=True, blank=True, on_delete=models.PROTECT)
    represented_unit_name = models.CharField(max_length=255, blank=True)
    invitation_status = models.CharField(max_length=20, choices=InvitationStatus.choices, default=InvitationStatus.INVITED)
    attendance_status = models.CharField(max_length=20, choices=AttendanceStatus.choices, default=AttendanceStatus.UNKNOWN)
    delegated_from = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="meeting_delegations")
    can_view_confidential = models.BooleanField(default=False)

    class Meta:
        ordering = ["meeting", "user_id", "external_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["meeting", "user"],
                condition=Q(user__isnull=False),
                name="mm_unique_meeting_user_participant",
            ),
            models.CheckConstraint(
                condition=Q(user__isnull=False) | ~Q(external_name=""),
                name="mm_participant_has_user_or_external_name",
            ),
        ]
        indexes = [
            models.Index(fields=["meeting", "attendance_status"]),
            models.Index(fields=["user", "invitation_status"]),
            models.Index(fields=["represented_unit", "meeting"]),
        ]

    def save(self, *args, **kwargs):
        if self.represented_unit and not self.represented_unit_name:
            self.represented_unit_name = self.represented_unit.name
        elif self.invited_unit and not self.represented_unit_name:
            self.represented_unit_name = self.invited_unit.represented_unit_name or self.invited_unit.unit.name
        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.user or self.external_name)


class MeetingAgendaItem(TimeStampedModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="agenda_items")
    sequence = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    presenter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    planned_duration_minutes = models.PositiveIntegerField(default=15)
    discussion_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["meeting", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "sequence"], name="mm_unique_agenda_sequence"),
            models.CheckConstraint(condition=Q(planned_duration_minutes__gt=0), name="mm_agenda_duration_positive"),
        ]

    def __str__(self):
        return f"{self.sequence}. {self.title}"


class MeetingMinutesVersion(TimeStampedModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="minute_versions")
    version_number = models.PositiveIntegerField()
    title = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="authored_minute_versions")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="submitted_minute_versions")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="approved_minute_versions")
    approved_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=MinuteVersionStatus.choices, default=MinuteVersionStatus.DRAFT)
    is_current = models.BooleanField(default=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    participant_snapshot = models.JSONField(default=list, blank=True)
    decision_snapshot = models.JSONField(default=list, blank=True)
    resolution_snapshot = models.JSONField(default=list, blank=True)
    final_document_checksum = models.CharField(max_length=128, blank=True)

    class Meta:
        ordering = ["meeting", "-version_number"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "version_number"], name="mm_unique_minutes_version"),
            models.UniqueConstraint(fields=["meeting"], condition=Q(is_current=True), name="mm_one_current_minutes_version"),
            models.CheckConstraint(condition=Q(version_number__gt=0), name="mm_minutes_version_positive"),
        ]
        indexes = [
            models.Index(fields=["meeting", "status"]),
            models.Index(fields=["status", "approved_at"]),
        ]

    @property
    def is_locked(self):
        return self.locked_at is not None or self.status == MinuteVersionStatus.APPROVED

    def clean(self):
        super().clean()
        if self.status == MinuteVersionStatus.APPROVED and not self.approved_at:
            raise ValidationError({"approved_at": "Approved minute versions require approved_at."})

    def save(self, *args, **kwargs):
        if self.pk:
            previous = MeetingMinutesVersion.objects.filter(pk=self.pk).first()
            if previous and previous.status == MinuteVersionStatus.APPROVED:
                immutable_fields = [
                    "body",
                    "participant_snapshot",
                    "decision_snapshot",
                    "resolution_snapshot",
                    "final_document_checksum",
                    "version_number",
                ]
                changed = [field for field in immutable_fields if getattr(previous, field) != getattr(self, field)]
                if changed:
                    raise ValidationError("Approved meeting-minute versions are immutable; create a new version.")
        if self.status == MinuteVersionStatus.APPROVED and not self.locked_at:
            self.locked_at = self.approved_at or timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.meeting} v{self.version_number}"
