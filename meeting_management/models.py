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


class Priority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class ResolutionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class ActionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    NOT_STARTED = "not_started", "Not started"
    IN_PROGRESS = "in_progress", "In progress"
    BLOCKED = "blocked", "Blocked"
    SUBMITTED_FOR_REVIEW = "submitted_for_review", "Submitted for review"
    REVISION_REQUESTED = "revision_requested", "Revision requested"
    ACCEPTED = "accepted", "Accepted"
    CLOSED = "closed", "Closed"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


class ActionExecutionMode(models.TextChoices):
    STANDALONE = "standalone", "Standalone"
    PROJECT_LINKED = "project_linked", "Project linked"
    PROJECT_TASK = "project_task", "Project task"


class RaciRole(models.TextChoices):
    ACCOUNTABLE = "accountable", "Accountable"
    RESPONSIBLE = "responsible", "Responsible"
    REVIEWER = "reviewer", "Reviewer"
    APPROVER = "approver", "Approver"
    CONTRIBUTOR = "contributor", "Contributor"
    INFORMED = "informed", "Informed"


class CompletionSubmissionStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    ACCEPTED = "accepted", "Accepted"
    REVISION_REQUESTED = "revision_requested", "Revision requested"
    REJECTED = "rejected", "Rejected"


class DeadlineRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class DependencyType(models.TextChoices):
    FINISH_TO_START = "finish_to_start", "Finish to start"
    APPROVAL_REQUIRED = "approval_required", "Approval required"
    INFORMATION_REQUIRED = "information_required", "Information required"


class NotificationStatus(models.TextChoices):
    UNREAD = "unread", "Unread"
    READ = "read", "Read"
    DISMISSED = "dismissed", "Dismissed"


class MeetingDecision(TimeStampedModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="decisions")
    agenda_item = models.ForeignKey(MeetingAgendaItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="decisions")
    decision_number = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    decided_at = models.DateTimeField(default=timezone.now)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_meeting_decisions")
    confidentiality = models.CharField(max_length=20, choices=ConfidentialityLevel.choices, blank=True)

    class Meta:
        ordering = ["meeting", "decision_number"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "decision_number"], name="mm_unique_decision_number"),
            models.CheckConstraint(condition=Q(decision_number__gt=0), name="mm_decision_number_positive"),
        ]
        indexes = [models.Index(fields=["meeting", "decided_at"])]

    def save(self, *args, **kwargs):
        if not self.confidentiality:
            self.confidentiality = self.meeting.confidentiality if self.meeting_id else ConfidentialityLevel.INTERNAL
        super().save(*args, **kwargs)

    def __str__(self):
        return f"D{self.decision_number}: {self.title}"


class Resolution(TimeStampedModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="resolutions")
    decision = models.ForeignKey(MeetingDecision, null=True, blank=True, on_delete=models.SET_NULL, related_name="resolutions")
    resolution_number = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    owner_unit = models.ForeignKey("CustomUser.OrgUnit", null=True, blank=True, on_delete=models.PROTECT, related_name="owned_resolutions")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="owned_resolutions")
    status = models.CharField(max_length=20, choices=ResolutionStatus.choices, default=ResolutionStatus.DRAFT)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    confidentiality = models.CharField(max_length=20, choices=ConfidentialityLevel.choices, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["meeting", "resolution_number"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "resolution_number"], name="mm_unique_resolution_number"),
            models.CheckConstraint(condition=Q(resolution_number__gt=0), name="mm_resolution_number_positive"),
        ]
        indexes = [
            models.Index(fields=["status", "priority"]),
            models.Index(fields=["owner_unit", "status"]),
            models.Index(fields=["confidentiality"]),
        ]

    def save(self, *args, **kwargs):
        if not self.confidentiality:
            self.confidentiality = self.meeting.confidentiality if self.meeting_id else ConfidentialityLevel.INTERNAL
        super().save(*args, **kwargs)

    def __str__(self):
        return f"R{self.resolution_number}: {self.title}"


class ResolutionAction(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resolution = models.ForeignKey(Resolution, on_delete=models.CASCADE, related_name="actions")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="sub_actions")
    action_number = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    accountable_unit = models.ForeignKey("CustomUser.OrgUnit", on_delete=models.PROTECT, related_name="accountable_meeting_actions")
    accountable_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="accountable_meeting_actions")
    responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="responsible_meeting_actions")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="review_meeting_actions")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="approve_meeting_actions")
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    planned_start = models.DateField(null=True, blank=True)
    original_due_date = models.DateField()
    current_due_date = models.DateField()
    completion_criteria = models.TextField(blank=True)
    expected_output = models.TextField(blank=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=30, choices=ActionStatus.choices, default=ActionStatus.DRAFT)
    confidentiality = models.CharField(max_length=20, choices=ConfidentialityLevel.choices, blank=True)
    project = models.ForeignKey("ktcPlanning.Project", null=True, blank=True, on_delete=models.PROTECT, related_name="meeting_actions")
    task = models.ForeignKey("ktcPlanning.Task", null=True, blank=True, on_delete=models.PROTECT, related_name="meeting_actions")
    execution_mode = models.CharField(max_length=20, choices=ActionExecutionMode.choices, default=ActionExecutionMode.STANDALONE)
    blocked_reason = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="closed_meeting_actions")

    class Meta:
        ordering = ["resolution", "action_number"]
        constraints = [
            models.UniqueConstraint(fields=["resolution", "action_number"], name="mm_unique_resolution_action_number"),
            models.CheckConstraint(condition=Q(action_number__gt=0), name="mm_action_number_positive"),
            models.CheckConstraint(condition=Q(progress_percent__gte=0) & Q(progress_percent__lte=100), name="mm_action_progress_range"),
            models.CheckConstraint(
                condition=Q(planned_start__isnull=True) | Q(original_due_date__gte=models.F("planned_start")),
                name="mm_action_original_due_after_start",
            ),
            models.CheckConstraint(
                condition=Q(planned_start__isnull=True) | Q(current_due_date__gte=models.F("planned_start")),
                name="mm_action_current_due_after_start",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "current_due_date"]),
            models.Index(fields=["accountable_unit", "status"]),
            models.Index(fields=["accountable_user", "status"]),
            models.Index(fields=["responsible_user", "status"]),
            models.Index(fields=["reviewer", "status"]),
            models.Index(fields=["project", "status"]),
            models.Index(fields=["confidentiality"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.progress_percent < 0 or self.progress_percent > 100:
            errors["progress_percent"] = "Progress must be between 0 and 100."
        if self.planned_start and self.original_due_date and self.original_due_date < self.planned_start:
            errors["original_due_date"] = "Original due date cannot be before planned start."
        if self.planned_start and self.current_due_date and self.current_due_date < self.planned_start:
            errors["current_due_date"] = "Current due date cannot be before planned start."
        if self.execution_mode == ActionExecutionMode.PROJECT_TASK and not self.task_id:
            errors["task"] = "Project task execution mode requires a stable task link."
        if self.task_id and self.project_id and self.task.project_id != self.project_id:
            errors["task"] = "Task must belong to the selected project."
        if errors:
            raise ValidationError(errors)

    @property
    def delay_against_original_days(self):
        return max((self.current_due_date - self.original_due_date).days, 0)

    def save(self, *args, **kwargs):
        if not self.current_due_date:
            self.current_due_date = self.original_due_date
        if not self.confidentiality:
            self.confidentiality = self.resolution.confidentiality if self.resolution_id else ConfidentialityLevel.INTERNAL
        super().save(*args, **kwargs)

    def __str__(self):
        return f"A{self.action_number}: {self.title}"


class ResolutionActionRoleAssignment(TimeStampedModel):
    action = models.ForeignKey(ResolutionAction, on_delete=models.CASCADE, related_name="role_assignments")
    role = models.CharField(max_length=20, choices=RaciRole.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="meeting_action_roles")
    unit = models.ForeignKey("CustomUser.OrgUnit", null=True, blank=True, on_delete=models.CASCADE, related_name="meeting_action_roles")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["action", "role", "user_id", "unit_id"]
        constraints = [
            models.CheckConstraint(condition=Q(user__isnull=False) | Q(unit__isnull=False), name="mm_action_role_has_user_or_unit"),
            models.UniqueConstraint(
                fields=["action", "role", "user"],
                condition=Q(is_active=True, user__isnull=False),
                name="mm_unique_active_action_user_role",
            ),
            models.UniqueConstraint(
                fields=["action", "role", "unit"],
                condition=Q(is_active=True, unit__isnull=False),
                name="mm_unique_active_action_unit_role",
            ),
        ]


class ActionAssignmentHistory(TimeStampedModel):
    action = models.ForeignKey(ResolutionAction, on_delete=models.CASCADE, related_name="assignment_history")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    previous_accountable_unit = models.ForeignKey("CustomUser.OrgUnit", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    new_accountable_unit = models.ForeignKey("CustomUser.OrgUnit", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    previous_accountable_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    new_accountable_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    previous_responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    new_responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reason = models.TextField(blank=True)


class ActionDependency(TimeStampedModel):
    predecessor = models.ForeignKey(ResolutionAction, on_delete=models.CASCADE, related_name="successor_dependencies")
    successor = models.ForeignKey(ResolutionAction, on_delete=models.CASCADE, related_name="predecessor_dependencies")
    dependency_type = models.CharField(max_length=30, choices=DependencyType.choices, default=DependencyType.FINISH_TO_START)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["successor", "predecessor"]
        constraints = [
            models.UniqueConstraint(fields=["predecessor", "successor", "dependency_type"], name="mm_unique_action_dependency"),
            models.CheckConstraint(condition=~Q(predecessor=models.F("successor")), name="mm_no_self_action_dependency"),
        ]
        indexes = [
            models.Index(fields=["predecessor", "dependency_type"]),
            models.Index(fields=["successor", "dependency_type"]),
        ]


class ActionProgressReport(TimeStampedModel):
    action = models.ForeignKey(ResolutionAction, on_delete=models.CASCADE, related_name="progress_reports")
    reporting_date = models.DateField(default=timezone.localdate)
    progress_percent = models.PositiveSmallIntegerField()
    work_completed = models.TextField(blank=True)
    next_steps = models.TextField(blank=True)
    blockers = models.TextField(blank=True)
    support_required = models.TextField(blank=True)
    forecast_completion_date = models.DateField(null=True, blank=True)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="meeting_progress_reports")
    review_comments = models.TextField(blank=True)

    class Meta:
        ordering = ["action", "-reporting_date", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(progress_percent__gte=0) & Q(progress_percent__lte=100), name="mm_report_progress_range"),
        ]
        indexes = [
            models.Index(fields=["reporter", "reporting_date"]),
            models.Index(fields=["action", "reporting_date"]),
        ]


class ActionEvidenceAttachment(TimeStampedModel):
    progress_report = models.ForeignKey(ActionProgressReport, null=True, blank=True, on_delete=models.CASCADE, related_name="evidence_attachments")
    completion_submission = models.ForeignKey("ActionCompletionSubmission", null=True, blank=True, on_delete=models.CASCADE, related_name="evidence_attachments")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    file = models.FileField(upload_to="meeting_action_evidence/%Y/%m/")
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(max_length=128, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(progress_report__isnull=False) | Q(completion_submission__isnull=False),
                name="mm_evidence_has_parent",
            ),
        ]


class ActionCompletionSubmission(TimeStampedModel):
    action = models.ForeignKey(ResolutionAction, on_delete=models.CASCADE, related_name="completion_submissions")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="meeting_completion_submissions")
    submitted_at = models.DateTimeField(default=timezone.now)
    summary = models.TextField()
    status = models.CharField(max_length=30, choices=CompletionSubmissionStatus.choices, default=CompletionSubmissionStatus.SUBMITTED)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="reviewed_completion_submissions")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)

    class Meta:
        ordering = ["action", "-submitted_at"]
        indexes = [
            models.Index(fields=["status", "submitted_at"]),
            models.Index(fields=["reviewed_by", "status"]),
        ]


class ActionDeadlineChangeRequest(TimeStampedModel):
    action = models.ForeignKey(ResolutionAction, on_delete=models.CASCADE, related_name="deadline_change_requests")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="meeting_deadline_requests")
    current_due_date_at_request = models.DateField()
    requested_due_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=DeadlineRequestStatus.choices, default=DeadlineRequestStatus.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="decided_deadline_requests")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["action", "-created_at"]
        indexes = [
            models.Index(fields=["status", "requested_due_date"]),
            models.Index(fields=["requested_by", "status"]),
        ]


class ApprovalWorkflowInstance(TimeStampedModel):
    workflow = models.ForeignKey(ApprovalWorkflow, on_delete=models.PROTECT, related_name="instances")
    meeting = models.ForeignKey(Meeting, null=True, blank=True, on_delete=models.CASCADE, related_name="workflow_instances")
    minute_version = models.ForeignKey(MeetingMinutesVersion, null=True, blank=True, on_delete=models.CASCADE, related_name="workflow_instances")
    action = models.ForeignKey(ResolutionAction, null=True, blank=True, on_delete=models.CASCADE, related_name="workflow_instances")
    status = models.CharField(max_length=30, default="active")
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"])]


class ApprovalWorkflowTask(TimeStampedModel):
    instance = models.ForeignKey(ApprovalWorkflowInstance, on_delete=models.CASCADE, related_name="tasks")
    step = models.ForeignKey(ApprovalWorkflowStep, on_delete=models.PROTECT)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="meeting_workflow_tasks")
    status = models.CharField(max_length=30, default="pending")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)

    class Meta:
        ordering = ["instance", "step__sequence"]
        indexes = [models.Index(fields=["assigned_to", "status"])]


class MeetingNotification(TimeStampedModel):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meeting_notifications")
    notification_type = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    meeting = models.ForeignKey(Meeting, null=True, blank=True, on_delete=models.CASCADE)
    action = models.ForeignKey(ResolutionAction, null=True, blank=True, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=NotificationStatus.choices, default=NotificationStatus.UNREAD)
    due_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=160)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["recipient", "idempotency_key"], name="mm_unique_notification_idempotency"),
        ]
        indexes = [
            models.Index(fields=["recipient", "status"]),
            models.Index(fields=["notification_type", "created_at"]),
        ]
