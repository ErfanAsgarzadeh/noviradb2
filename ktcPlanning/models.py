import uuid
import uuid
from django.db import models
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from mptt.models import MPTTModel
from mptt.fields import TreeForeignKey
from ktcPlanning.validators import validate_chat_file

User = get_user_model()


BUDGET_STATUS_CHOICES = [
    ("DRAFT", "Draft"),
    ("SUBMITTED", "Submitted"),
    ("APPROVED", "Approved"),
    ("REJECTED", "Rejected"),
    ("LOCKED", "Locked"),
    ("CLOSED", "Closed"),
]


# =========================================================
# 1. PROJECT
# =========================================================

class Project(models.Model):
    LIFECYCLE_DRAFT = 'draft'
    LIFECYCLE_PLANNING = 'planning'
    LIFECYCLE_ACTIVE = 'active'
    LIFECYCLE_ON_HOLD = 'on_hold'
    LIFECYCLE_COMPLETED = 'completed'
    LIFECYCLE_CANCELLED = 'cancelled'
    LIFECYCLE_ARCHIVED = 'archived'
    LIFECYCLE_CHOICES = [
        (LIFECYCLE_DRAFT, 'Draft'),
        (LIFECYCLE_PLANNING, 'Planning'),
        (LIFECYCLE_ACTIVE, 'Active'),
        (LIFECYCLE_ON_HOLD, 'On hold'),
        (LIFECYCLE_COMPLETED, 'Completed'),
        (LIFECYCLE_CANCELLED, 'Cancelled'),
        (LIFECYCLE_ARCHIVED, 'Archived'),
    ]
    SCOPE_CHOICES = [
        ('intra_unit', 'پروژهٔ درون‌واحدی'),
        ('company', 'پروژهٔ شرکتی'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)

    is_deleted = models.BooleanField(default=False)
    lifecycle_status = models.CharField(
        max_length=16, choices=LIFECYCLE_CHOICES, default=LIFECYCLE_DRAFT,
    )
    current_data_date = models.DateTimeField(null=True, blank=True)
    active_baseline_revision = models.ForeignKey(
        'Revision', null=True, blank=True, on_delete=models.PROTECT,
        related_name='baseline_for_projects',
    )
    current_execution_revision = models.ForeignKey(
        'Revision', null=True, blank=True, on_delete=models.PROTECT,
        related_name='execution_for_projects',
    )
    current_forecast_revision = models.ForeignKey(
        'Revision', null=True, blank=True, on_delete=models.PROTECT,
        related_name='forecast_for_projects',
    )
    working_revision = models.ForeignKey(
        'Revision', null=True, blank=True, on_delete=models.PROTECT,
        related_name='working_for_projects',
    )


    # دامنهٔ پروژه: شرکتی (تاییدِ نهایی با مدیر برنامه‌ریزی) یا درون‌واحدی
    scope = models.CharField(
        max_length=16, choices=SCOPE_CHOICES, default='company',
        verbose_name="دامنهٔ پروژه",
        help_text="پروژه‌های شرکتی نیازمندِ تاییدِ نهاییِ گزارش توسطِ مدیرِ برنامه‌ریزی هستند.",
    )

    # تقویم کاری الصاق‌شده به پروژه (مستقل تعریف می‌شود و اینجا انتخاب می‌گردد)
    calendar = models.ForeignKey(
        'Calendar', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='projects'
    )
    # واحد سازمانی صاحب پروژه (برای پروژه‌هایی که مدیر واحد می‌سازد)
    owner_unit = models.ForeignKey(
        'CustomUser.OrgUnit', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='owned_projects'
    )

    parent_project = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='subprojects',
        verbose_name="پروژه مادر",
        help_text="برای ساخت ساختار پروژه/زیرپروژه شبیه Primavera استفاده می‌شود."
    )

    parent_schedule_warning = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="هشدار زمان‌بندی پروژه مادر",
        help_text="آخرین تعارض تاریخ این پروژه با شبکه CPM پروژه مادر. با اجرای CPM مادر به‌روزرسانی می‌شود.",
    )
    parent_schedule_warning_updated_at = models.DateTimeField(null=True, blank=True)
    # اولویت پروژه در رقابت سراسری بر سر منابع (عدد کمتر = اولویت بالاتر، هم‌راستا با Resource.priority)
    priority = models.IntegerField(
        default=100,
        verbose_name="اولویت پروژه",
        help_text="در تسطیح چندپروژه‌ای، پروژه‌های با عدد کمتر اولویت بالاتری در رقابت بر سر منابع دارند."
    )

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        revision_fields = (
            'active_baseline_revision',
            'current_execution_revision',
            'current_forecast_revision',
            'working_revision',
        )
        for field_name in revision_fields:
            revision = getattr(self, field_name, None)
            if revision and (revision.project_id != self.pk or revision.is_deleted):
                errors[field_name] = 'Revision must be an active revision of this project.'
        if self.active_baseline_revision and not self.active_baseline_revision.is_baseline:
            errors['active_baseline_revision'] = 'Active baseline must be marked as baseline.'
        if self.working_revision and self.working_revision.approved_at is not None:
            errors['working_revision'] = 'Working revision must be open.'
        if self.lifecycle_status == self.LIFECYCLE_ACTIVE:
            if not self.active_baseline_revision_id:
                errors['active_baseline_revision'] = 'Active projects require an active baseline.'
            if not self.current_execution_revision_id:
                errors['current_execution_revision'] = 'Active projects require an execution revision.'
            if not self.current_data_date:
                errors['current_data_date'] = 'Active projects require a data date.'
        if errors:
            raise ValidationError(errors)
        if not self.parent_project_id:
            return
        if self.pk and self.parent_project_id == self.pk:
            raise ValidationError({"parent_project": "پروژه نمی‌تواند زیرپروژه خودش باشد."})

        ancestor = self.parent_project
        while ancestor is not None:
            if self.pk and ancestor.pk == self.pk:
                raise ValidationError({"parent_project": "ساختار زیرپروژه نمی‌تواند حلقه داشته باشد."})
            ancestor = ancestor.parent_project

    def get_default_approver(self):
        if self.scope == 'company':
            from .permissions import get_planning_manager
            return get_planning_manager()
        owner_unit = self.owner_unit or getattr(self.created_by, 'unit', None)
        return getattr(owner_unit, 'manager', None) if owner_unit else None

class UnitOfMeasure(models.Model):
    code = models.CharField(max_length=20, unique=True)   # HOUR, DAY, LITER
    name = models.CharField(max_length=50)                # Hour, Day, Liter

    def __str__(self):
        return self.name


class ProjectViewer(models.Model):
    """دسترسیِ مشاهده‌گر (Viewer) در سطحِ پروژه — توسطِ سازندهٔ پروژه اضافه می‌شود."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="viewers")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="viewable_projects")
    added_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="added_project_viewers"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("project", "user")]

    def __str__(self):
        return f"Viewer {self.user} on {self.project}"


# =========================================================
# 2. CALENDAR SYSTEM
# =========================================================

class Calendar(models.Model):
    # تقویم می‌تواند مستقل از پروژه باشد (قالب) و بعداً به پروژه الصاق شود
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE, related_name="calendars")
    name = models.CharField(max_length=255)
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class WorkingInterval(models.Model):
    WEEKDAYS = [
        (0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"),
        (4, "Fri"), (5, "Sat"), (6, "Sun"),
    ]

    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="intervals")
    weekday = models.IntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()

    def clean(self):
        if self.start_time >= self.end_time:
            raise ValidationError("Start time must be before end time.")


class CalendarException(models.Model):
    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="exceptions")
    date = models.DateField()
    is_working = models.BooleanField(default=False)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("calendar", "date")]


# =========================================================
# 3. REVISION ENGINE (CORE OF REPLANNING)
# =========================================================

class Revision(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    description = models.TextField(blank=True)
    is_baseline = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="created_revisions"
    )
    approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="approved_revisions"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    # تاییدکننده‌ی تعیین‌شده هنگام ساخت Revision (فقط همین فرد می‌تواند تایید/قفل کند)
    designated_approver = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="revisions_to_approve"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    project_start=models.DateTimeField()
    project_end = models.DateTimeField(null=True, blank=True)

    is_deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = [("project", "number")]

    def __str__(self):
        return f"Rev {self.number} - {self.project.name}"

# =========================================================
# 4. WBS (TREE STRUCTURE)
# =========================================================

class WBSNode(models.Model):
    id=models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="wbs")
    created_at = models.DateTimeField(auto_now_add=True)



# immutable revisioned layer

class WBSNodeVersion(MPTTModel):

    node = models.ForeignKey(WBSNode,on_delete=models.CASCADE,related_name="versions")
    revision = models.ForeignKey(Revision,on_delete=models.CASCADE,related_name="wbs_versions")
    parent = TreeForeignKey("self",null=True,blank=True, on_delete=models.CASCADE,related_name="children")
    title = models.CharField(max_length=255)
    sequence = models.PositiveIntegerField(default=1)

    is_deleted = models.BooleanField(default=False)

    planned_start = models.DateTimeField(null=True, blank=True, verbose_name="Planned Start Date")
    planned_finish = models.DateTimeField(null=True, blank=True, verbose_name="Planned Finish Date")
    class MPTTMeta:
        order_insertion_by = ["sequence"]

    class Meta:
        unique_together = [
            ("node", "revision"),
            ("revision", "parent", "sequence")
        ]

    @property
    def wbs_code(self):
        ancestors = self.get_ancestors(include_self=True)
        return ".".join(str(a.sequence) for a in ancestors)

    def clean(self):
        super().clean()
        # اعتبارسنجی منطقی: تاریخ پایان نباید قبل از تاریخ شروع باشد
        if self.planned_start and self.planned_finish:
            if self.planned_start > self.planned_finish:
                raise ValidationError({
                    'planned_finish': "تاریخ پایان برنامه‌ریزی شده نمی‌تواند قبل از تاریخ شروع باشد."
                })

    def save(self, *args, **kwargs):
        # اجرای متد clean قبل از ذخیره کردن در دیتابیس
        self.full_clean()
        super().save(*args, **kwargs)
# =========================================================
# 5. TASK (IDENTITY ONLY)
# =========================================================
from django.apps import apps
@receiver(post_save, sender=Project)
def create_revision_zero(sender, instance, created, **kwargs):
    if created:
        # استفاده از get_model برای جلوگیری از ارجاع ناقص
        Revision = apps.get_model('ktcPlanning', 'Revision')
        WBSNode = apps.get_model('ktcPlanning', 'WBSNode')
        WBSNodeVersion = apps.get_model('ktcPlanning', 'WBSNodeVersion')

        # ۱. ایجاد Revision شماره 0
        revision = Revision.objects.create(
            project=instance,
            number=0,
            description="Initial Automatic Base Version (Rev 0)",
            is_baseline=True,
            created_by=instance.created_by,
            designated_approver=instance.get_default_approver(),
            project_start=instance.start_date if instance.start_date else instance.created_at,
            project_end=instance.end_date if instance.end_date else instance.created_at
        )

        # ۲. ایجاد گره پایه WBS
        Project.objects.filter(pk=instance.pk).update(
            active_baseline_revision=revision,
            current_execution_revision=revision,
            current_forecast_revision=revision,
            working_revision=revision,
            current_data_date=instance.start_date or instance.created_at,
            lifecycle_status=Project.LIFECYCLE_PLANNING,
        )

        base_wbs_node = WBSNode.objects.create(project=instance)

        # ۳. ایجاد نسخه WBS برای Revision 0
        WBSNodeVersion.objects.create(
            node=base_wbs_node,
            revision=revision,
            title=f"Root: {instance.name}",
            sequence=1
        )


class Task(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    def __str__(self):
        return str(self.id)


# =========================================================
# 6. TASK VERSION (IMMUTABLE SCHEDULE STATE)
# =========================================================

class TaskVersion(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="versions")
    revision = models.ForeignKey(Revision, on_delete=models.CASCADE, related_name="task_versions")
    wbs_node = models.ForeignKey(WBSNodeVersion, on_delete=models.PROTECT)
    title = models.CharField(max_length=255)
    calendar = models.ForeignKey(Calendar, null=True, blank=True, on_delete=models.SET_NULL)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=0.00,
                                 help_text="وزن تسک در پروژه (درصد یا ضریب)")
    planned_start = models.DateTimeField(null=True, blank=True)
    planned_finish = models.DateTimeField(null=True, blank=True)
    duration_hours = models.DecimalField(max_digits=10, decimal_places=2)
    description=models.TextField(null=True, blank=True)

    is_deleted = models.BooleanField(default=False)

    sequence = models.IntegerField(default=0, help_text="ترتیب نمایش در گانت‌چارت")
    class Meta:
        unique_together = [("task", "revision")]
        indexes = [
            models.Index(fields=["revision", "wbs_node"]),
            models.Index(fields=["planned_start"]),
        ]
        ordering = ['sequence']

    def clean(self):
        if  self.planned_start and self.planned_finish:
            if self.planned_start >= self.planned_finish:
                raise ValidationError("Start must be before finish.")


# =========================================================
# 7. DEPENDENCIES (VERSIONED CPM GRAPH)
# =========================================================

class Dependency(models.Model):
    LINK_TYPES = [
        ("FS", "Finish-Start"),
        ("SS", "Start-Start"),
        ("FF", "Finish-Finish"),
        ("SF", "Start-Finish"),
    ]

    revision = models.ForeignKey(Revision, on_delete=models.CASCADE, related_name="dependencies")
    predecessor = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="outgoing")
    successor = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="incoming")
    dependency_type = models.CharField(max_length=2, choices=LINK_TYPES, default="FS")
    lag_hours = models.IntegerField(default=0)

    class Meta:
        unique_together = [("revision", "predecessor", "successor")]

    def clean(self):
        if self.predecessor_id == self.successor_id:
            raise ValidationError("Self dependency not allowed.")


class SubprojectDependency(models.Model):
    DIRECTION_TASK_TO_SUBPROJECT = "TASK_TO_SUBPROJECT"
    DIRECTION_SUBPROJECT_TO_TASK = "SUBPROJECT_TO_TASK"
    DIRECTION_CHOICES = [
        (DIRECTION_TASK_TO_SUBPROJECT, "Task to Subproject"),
        (DIRECTION_SUBPROJECT_TO_TASK, "Subproject to Task"),
    ]

    revision = models.ForeignKey(Revision, on_delete=models.CASCADE, related_name="subproject_dependencies")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="subproject_dependencies")
    subproject = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="parent_schedule_dependencies")
    direction = models.CharField(max_length=24, choices=DIRECTION_CHOICES)
    dependency_type = models.CharField(max_length=2, choices=Dependency.LINK_TYPES, default="FS")
    lag_hours = models.IntegerField(default=0)

    class Meta:
        unique_together = [("revision", "task", "subproject", "direction")]

    def clean(self):
        super().clean()
        if self.revision_id and self.subproject_id:
            if self.revision.project_id == self.subproject_id:
                raise ValidationError("Project cannot depend on itself as a subproject.")
            if self.subproject.parent_project_id != self.revision.project_id:
                raise ValidationError("Subproject must belong to the dependency revision project.")
        if self.revision_id and self.task_id and self.task.project_id != self.revision.project_id:
            raise ValidationError("Task must belong to the dependency revision project.")


# =========================================================
# 8. CPM METRICS CACHE (COMPUTED LAYER)
# =========================================================

class TaskScheduleMetrics(models.Model):
    task_version = models.OneToOneField(
        TaskVersion, on_delete=models.CASCADE, related_name="metrics"
    )
    early_start = models.DateTimeField()
    early_finish = models.DateTimeField()
    late_start = models.DateTimeField()
    late_finish = models.DateTimeField()
    total_float_hours = models.IntegerField(default=0)
    free_float_hours = models.IntegerField(default=0)
    is_critical = models.BooleanField(default=False)


# =========================================================
# 9. RESOURCE MODEL
#    - Resource به User وصل شد (اختیاری — می‌تواند منبع غیرانسانی هم باشد)
# =========================================================
class ResourcePool(models.Model):
    name = models.CharField(max_length=255)

    description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

class ResourceRole(models.Model):

    name = models.CharField(
        max_length=255,
        unique=True
    )

    description = models.TextField(blank=True)

    def __str__(self):
        return self.name



class ResourceSkill(models.Model):

    name = models.CharField(
        max_length=255,
        unique=True
    )

    def __str__(self):
        return self.name


class Resource(models.Model):

    LABOR = "LABOR"
    EQUIPMENT = "EQUIPMENT"
    MATERIAL = "MATERIAL"
    COST = "COST"

    RESOURCE_TYPES = [
        (LABOR, "Labor"),
        (EQUIPMENT, "Equipment"),
        (MATERIAL, "Material"),
        (COST, "Cost"),
    ]

    pool = models.ForeignKey(
        ResourcePool,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="resources"
    )

    code = models.CharField(
        max_length=50,
        null=True,
        unique=True
    )

    name = models.CharField(
        max_length=255
    )

    resource_type = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        choices=RESOURCE_TYPES
    )

    role = models.ForeignKey(
        ResourceRole,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    calendar = models.ForeignKey(
        Calendar,
        null=True, blank=True,
        on_delete=models.PROTECT
    )

    max_units = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=100
    )

    priority = models.IntegerField(
        default=100
    )

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(

        auto_now_add=True
    )
    unit = models.ForeignKey(
        UnitOfMeasure,
        null=True,
        blank=True,
        on_delete=models.PROTECT
    )

    def __str__(self):
        return self.name


class ResourceSkillMapping(models.Model):

    resource = models.ForeignKey(
        Resource,
        on_delete=models.CASCADE,
        related_name="skills"
    )

    skill = models.ForeignKey(
        ResourceSkill,
        on_delete=models.CASCADE
    )

    level = models.IntegerField(default=1)

    class Meta:
        unique_together = [
            ("resource", "skill")
        ]


class ResourceException(models.Model):

    resource = models.ForeignKey(
        Resource,
        on_delete=models.CASCADE,
        related_name="exceptions"
    )

    start_datetime = models.DateTimeField()

    finish_datetime = models.DateTimeField()

    reason = models.CharField(
        max_length=255
    )

    is_available = models.BooleanField(
        default=False
    )

class ResourceRate(models.Model):

    resource = models.ForeignKey(
        Resource,
        on_delete=models.CASCADE,
        related_name="rates"
    )

    effective_from = models.DateField()

    regular_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    overtime_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    class Meta:
        ordering = ["effective_from"]

class RoleRequirement(models.Model):

    revision = models.ForeignKey(
        Revision,
        on_delete=models.CASCADE
    )

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE
    )

    role = models.ForeignKey(
        ResourceRole,
        on_delete=models.CASCADE
    )

    units_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2
    )

    required_count = models.IntegerField(
        default=1
    )

class SkillRequirement(models.Model):

    revision = models.ForeignKey(
        Revision,
        on_delete=models.CASCADE
    )

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE
    )

    skill = models.ForeignKey(
        ResourceSkill,
        on_delete=models.CASCADE
    )

    minimum_level = models.IntegerField(
        default=1
    )

class Assignment(models.Model):

    revision = models.ForeignKey(
        Revision,
        on_delete=models.CASCADE
    )

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE
    )

    resource = models.ForeignKey(
        Resource,
        on_delete=models.CASCADE
    )

    units_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=100
    )

    planned_hours = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    actual_hours = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    class Meta:
        unique_together = [
            (
                "revision",
                "task",
                "resource"
            )
        ]

class GlobalLevelingRun(models.Model):
    """ذخیره کانتکست اجرای یک تسطیح منابع سراسری روی چندین پروژه"""

    # ─── معیارهای قابل انتخاب برای اولویت‌بندی رقابت تسک‌ها بر سر منابع ───
    PRIORITY_TOTAL_FLOAT = "total_float"
    PRIORITY_LATE_START = "late_start"
    PRIORITY_EARLY_START = "early_start"
    PRIORITY_PLANNED_START = "planned_start"
    PRIORITY_TASK_WEIGHT = "task_weight"
    PRIORITY_SEQUENCE = "sequence"
    PRIORITY_RESOURCE_PRIORITY = "resource_priority"
    PRIORITY_PROJECT_PRIORITY = "project_priority"

    PRIORITY_CRITERIA_CHOICES = [
        (PRIORITY_TOTAL_FLOAT, "کمترین شناوری کل (Total Float) — بحرانی‌ترین تسک‌ها اول"),
        (PRIORITY_LATE_START, "زودترین تاریخ شروع دیرهنگام (Late Start)"),
        (PRIORITY_EARLY_START, "زودترین تاریخ شروع زودهنگام (Early Start)"),
        (PRIORITY_PLANNED_START, "زودترین تاریخ شروع برنامه‌ریزی‌شده (Planned Start)"),
        (PRIORITY_TASK_WEIGHT, "بیشترین وزن تسک (Weight)"),
        (PRIORITY_SEQUENCE, "کمترین شماره ترتیب نمایش (Sequence) در گانت‌چارت"),
        (PRIORITY_RESOURCE_PRIORITY, "بالاترین اولویت منبع تخصیص‌یافته (Resource.priority)"),
        (PRIORITY_PROJECT_PRIORITY, "بالاترین اولویت پروژه (Project.priority)"),
    ]

    DIRECTION_CHOICES = [
        ("asc", "صعودی (کمترین مقدار، اولویت بالاتر)"),
        ("desc", "نزولی (بیشترین مقدار، اولویت بالاتر)"),
    ]

    DEFAULT_PRIORITY_RULES = [
        {"criterion": PRIORITY_PROJECT_PRIORITY, "direction": "asc"},
        {"criterion": PRIORITY_TOTAL_FLOAT, "direction": "asc"},
    ]

    STATUS_DRAFT = "draft"
    STATUS_CALCULATED = "calculated"
    STATUS_PUBLISHED = "published"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_CALCULATED, "Calculated"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160, default="Untitled Leveling Plan")
    executed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    executed_by = models.ForeignKey(User, on_delete=models.PROTECT)
    description = models.TextField(blank=True)
    data_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    settings = models.JSONField(default=dict, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    # پروژه‌هایی که در این اجرای سراسری شرکت داده شده‌اند
    participating_projects = models.ManyToManyField(Project, related_name="leveling_runs")
    # وضعیت لولینگ: در حد پیش‌نویس/شبیه‌سازی است یا روی برنامه‌ها اعمال نهایی شده؟
    is_committed = models.BooleanField(default=False, verbose_name="اعمال نهایی شده روی برنامه اصلی")

    # فهرست مرتب معیارهای اولویت‌بندی، هر آیتم به شکل {"criterion": <کد>, "direction": "asc"|"desc"}
    # اولین آیتم مهم‌ترین معیار است؛ در صورت تساوی، معیار بعدی تعیین‌کننده می‌شود.
    # اگر خالی باشد، پیش‌فرض سیستم (DEFAULT_PRIORITY_RULES) اعمال می‌شود.
    priority_rules = models.JSONField(
        default=list,
        blank=True,
        verbose_name="معیارهای اولویت‌بندی تسک‌ها",
        help_text=(
            "ترتیب معیارهایی که مشخص می‌کند وقتی چند تسک هم‌زمان بر سر یک منبع رقابت می‌کنند، "
            "کدام تسک زودتر منبع را می‌گیرد. مثال: "
            '[{"criterion": "total_float", "direction": "asc"}, '
            '{"criterion": "resource_priority", "direction": "asc"}]'
        ),
    )

    def get_priority_rules(self) -> list:
        """فهرست نهایی معیارهای اولویت‌بندی؛ در صورت عدم تنظیم توسط کاربر، پیش‌فرض سیستم برگردانده می‌شود."""
        valid_criteria = {c[0] for c in self.PRIORITY_CRITERIA_CHOICES}
        rules = []
        for rule in (self.priority_rules or []):
            criterion = rule.get("criterion")
            direction = rule.get("direction", "asc")
            if criterion not in valid_criteria:
                raise ValidationError(f"معیار اولویت‌بندی نامعتبر است: {criterion}")
            if direction not in ("asc", "desc"):
                raise ValidationError(f"جهت مرتب‌سازی نامعتبر است: {direction}")
            rules.append({"criterion": criterion, "direction": direction})
        return rules or list(self.DEFAULT_PRIORITY_RULES)

    def __str__(self):
        return f"{self.name} - {self.executed_at.date()}"


class LevelingPlanProject(models.Model):
    leveling_run = models.ForeignKey(
        GlobalLevelingRun, on_delete=models.CASCADE, related_name="plan_projects"
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="leveling_plan_entries"
    )
    revision = models.ForeignKey(
        Revision, on_delete=models.PROTECT, related_name="leveling_plan_entries"
    )
    priority = models.PositiveIntegerField(default=100)

    class Meta:
        unique_together = [("leveling_run", "project")]
        ordering = ["priority", "project__name"]

    def clean(self):
        if self.revision_id and self.project_id and self.revision.project_id != self.project_id:
            raise ValidationError("Selected revision must belong to the selected project.")


class TaskLevelingMetrics(models.Model):
    """ذخیره تاریخ‌های پیشنهادی تسطیح، بدون دستکاری لایه اصلی TaskVersion"""
    leveling_run = models.ForeignKey(
        GlobalLevelingRun, on_delete=models.CASCADE, related_name="task_metrics"
    )
    task_version = models.ForeignKey(
        TaskVersion, on_delete=models.CASCADE, related_name="leveling_metrics"
    )

    # تاریخ‌های پیشنهادی موتور تسطیح (تداخل‌ها در این تاریخ‌ها حل شده‌اند)
    original_start = models.DateTimeField(null=True, blank=True)
    original_finish = models.DateTimeField(null=True, blank=True)
    leveled_start = models.DateTimeField()
    leveled_finish = models.DateTimeField()
    decision_reason = models.CharField(max_length=255, blank=True)

    # میزان تاخیری که لولینگ به خاطر کمبود منبع به تسک تحمیل کرده است (بر حسب ساعت)
    leveling_delay_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        unique_together = [("leveling_run", "task_version")]



class ResourceUsage(models.Model):
    leveling_run = models.ForeignKey(
        GlobalLevelingRun, on_delete=models.CASCADE, related_name="resource_usages", null=True, blank=True
    )
    revision = models.ForeignKey(
        Revision,
        null=True,
        blank=True,
        on_delete=models.CASCADE
    )

    resource = models.ForeignKey(
        Resource,
        on_delete=models.CASCADE
    )

    usage_date = models.DateField()

    planned_hours = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    actual_hours = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    capacity_hours = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    remaining_capacity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    class Meta:
        unique_together = [
            (
                "leveling_run",
                "resource",
                "usage_date"
            )
        ]
# =========================================================
# 10. TASK ROLE — نقش افراد روی تسک (جدید)
#     هم نسخه‌بندی‌شده (per revision) و هم به User وصل است
# =========================================================

class TaskRole(models.Model):
    ROLES = [
        ("owner",    "مسئول اصلی"),
        ("reviewer", "بررسی‌کننده"),
        ("executor", "مجری"),
        ("project manager", "مدیر پروژه"),
    ]

    revision = models.ForeignKey(Revision, on_delete=models.CASCADE, related_name="task_roles")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="roles")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="task_roles")
    role = models.CharField(max_length=20, choices=ROLES)

    class Meta:
        unique_together = [("revision", "task", "user", "role")]

    def __str__(self):
        return f"{self.user} — {self.role} on {self.task}"


# =========================================================
# 11. TASK ACTUAL — واقعیت اجرا (جدید)
#     progress از TaskVersion به اینجا منتقل شد
# =========================================================

class TaskActual(models.Model):
    task_version = models.OneToOneField(
        TaskVersion, on_delete=models.CASCADE, related_name="actual"
    )
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_finish = models.DateTimeField(null=True, blank=True)
    # درصد پیشرفت واقعی — جای progress در TaskVersion
    progress = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    updated_by = models.ForeignKey(User, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.actual_start and self.actual_finish:
            if self.actual_start >= self.actual_finish:
                raise ValidationError("actual_start باید قبل از actual_finish باشد.")

# =========================================================
# 12. VARIANCE REPORT — انحراف از برنامه (اصلاح شده)
# =========================================================

class VarianceReport(models.Model):
    # اتصال مستقیم به Task برای حفظ تاریخچه در طول ریویژن‌های مختلف
    task = models.ForeignKey('Task', on_delete=models.CASCADE, related_name="variance_snapshots")
    revision = models.ForeignKey('Revision', on_delete=models.CASCADE, related_name="variances")

    # تاریخ محاسبه (Data Date)
    report_date = models.DateField(default=timezone.localdate)

    # مقادیر پایه EVM (بر اساس ساعت)
    budget_at_completion = models.DecimalField(max_digits=15, decimal_places=2, default=0)  # BAC
    planned_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)         # PV
    earned_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)          # EV
    actual_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0)           # AC

    # شاخص‌های عملکرد (Performance Indices)
    spi = models.DecimalField(max_digits=5, decimal_places=2, default=1.00)
    cpi = models.DecimalField(max_digits=5, decimal_places=2, default=1.00)

    # انحراف‌ها (Variances)
    schedule_variance = models.DecimalField(max_digits=15, decimal_places=2, default=0)     # SV = EV - PV
    cost_variance = models.DecimalField(max_digits=15, decimal_places=2, default=0)         # CV = EV - AC

    # پیش‌بینی‌ها (Forecasting)
    estimate_at_completion = models.DecimalField(max_digits=15, decimal_places=2, default=0) # EAC
    estimate_to_complete = models.DecimalField(max_digits=15, decimal_places=2, default=0)   # ETC
    variance_at_completion = models.DecimalField(max_digits=15, decimal_places=2, default=0) # VAC

    # فلگ عملیاتی (برای داشبورد "مدیریت بر مبنای استثنا")
    action_required = models.BooleanField(default=False)

    class Meta:
        unique_together = [("task", "report_date", "revision")]
        ordering = ['-report_date']

    def __str__(self):
        return f"Variance for Task {self.task.id} on {self.report_date}"


# =========================================================
# 13. BASELINE (REVISION-BASED SNAPSHOT)
# =========================================================

class Baseline(models.Model):
    revision = models.OneToOneField(Revision, on_delete=models.CASCADE, related_name="baseline")
    created_at = models.DateTimeField(auto_now_add=True)


# =========================================================
# 14. TASK REPORTING (MY TASKS SYSTEM)
# =========================================================

class TaskReportLog(models.Model):
    STATUS_CHOICES = [
        ("on-track", "On Track"),
        ("at-risk", "At Risk"),
        ("blocked", "Blocked"),
        ("completed", "Completed"),
    ]

    APPROVAL_STATUS_CHOICES = [
        ('pending', 'در انتظار تایید'),
        ('reviewer_approved', 'تاییدشده توسط بررسی‌کننده'),
        ('final_approved', 'تایید نهایی'),
        ('rejected', 'رد شده'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="report_logs")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="submitted_reports")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="on-track")
    progress_percent = models.PositiveIntegerField(default=0)
    time_spent_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)

    notes = models.TextField(blank=True, verbose_name="Progress Notes")
    blockers = models.TextField(blank=True, verbose_name="Critical Blockers")

    timestamp = models.DateTimeField(auto_now_add=True)

    # ─── State machine تایید (دو‌مرحله‌ای) ───
    approval_status = models.CharField(
        max_length=24, choices=APPROVAL_STATUS_CHOICES, default='pending',
        verbose_name="وضعیت تایید"
    )

    # مرحلهٔ ۱: تایید بررسی‌کننده
    reviewer_approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="reviewer_approved_reports"
    )
    reviewer_approved_at = models.DateTimeField(null=True, blank=True)

    # مرحلهٔ ۲: تایید نهایی (فقط برای پروژه‌های شرکتی)
    final_approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="final_approved_reports"
    )
    final_approved_at = models.DateTimeField(null=True, blank=True)

    # فیلدهای قدیمی — نگه‌داشته شده برای سازگاری با کدِ موجود (حذف در آینده)
    is_approved = models.BooleanField(default=False, verbose_name="وضعیت تایید (legacy)")
    approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_reports"
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"Report {self.progress_percent}% by {self.user} - {self.approval_status}"


class TaskReportAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(TaskReportLog, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(
        upload_to='task_report_attachments/%Y/%m/',
        validators=[validate_chat_file],
    )
    file_name = models.CharField(max_length=255, blank=True, default='')
    file_type = models.CharField(max_length=100, blank=True, default='')
    file_size = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return self.file_name or str(self.file)



# =========================================================
# 15. TASK CHAT & COLLABORATION
# =========================================================

class TaskChatMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # اتصال به Task (نه Version) تا تاریخچه مکالمات در ورژن‌های مختلف برنامه ثابت بماند
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="chat_messages")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="chat_messages")

    text = models.TextField(blank=True, default='')
    file = models.FileField(
        upload_to='chat_attachments/%Y/%m/',
        null=True,
        blank=True,
        validators=[validate_chat_file],
    )
    file_name = models.CharField(max_length=255, blank=True, default='')
    file_type = models.CharField(max_length=50, blank=True, default='')  # e.g. 'image/png', 'application/pdf'
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"Message by {self.user} on {self.task}"


# =========================================================
# مدل‌های جدید برای لایه تسطیح چندپروژه‌ای (Multi-Project Leveling Layer)
# =========================================================



# =========================================================
# 16. SYSTEM SETTINGS (SINGLETON)
# =========================================================

class SystemSettings(models.Model):
    """
    تنظیماتِ کلی سیستم (تک‌ردیفی).
    شامل تنظیماتِ حاکمیتیِ workflow مانند اجازهٔ bypass تاییدِ بررسی‌کننده.
    """
    allow_planning_manager_bypass_reviewer = models.BooleanField(
        default=False,
        verbose_name="اجازهٔ bypass تایید بررسی‌کننده توسط مدیر برنامه‌ریزی",
        help_text="اگر فعال باشد، مدیرِ برنامه‌ریزی می‌تواند گزارش‌های پروژهٔ شرکتی را بدون تاییدِ بررسی‌کننده مستقیماً تایید نهایی کند."
    )

    class Meta:
        verbose_name = "تنظیمات سیستم"
        verbose_name_plural = "تنظیمات سیستم"

    def __str__(self):
        return "تنظیمات سیستم"

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ExpenseType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FundingSource(models.Model):
    SOURCE_TYPES = [
        ("CLIENT_PAYMENT", "Client Payment"),
        ("INTERNAL_CAPITAL", "Internal Capital"),
        ("LOAN", "Loan"),
        ("CONTRACT", "Contract"),
        ("GRANT", "Grant"),
        ("OTHER", "Other"),
    ]

    title = models.CharField(max_length=255)
    source_type = models.CharField(max_length=32, choices=SOURCE_TYPES)
    source_party = models.CharField(max_length=255, blank=True)
    reference_no = models.CharField(max_length=100, blank=True)
    received_date = models.DateField()
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=8, default="IRR")
    status = models.CharField(max_length=16, choices=BUDGET_STATUS_CHOICES, default="DRAFT")
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    submitted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="submitted_funding_sources")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_funding_sources")
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="rejected_funding_sources")
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_date", "-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["source_type", "status"]),
        ]

    def clean(self):
        super().clean()
        if self.total_amount is not None and self.total_amount <= 0:
            raise ValidationError("Funding amount must be greater than zero.")
        if self.pk and self.total_amount is not None and self.total_amount < self.allocated_amount:
            raise ValidationError("Funding amount cannot be less than existing root allocations.")

    @property
    def allocated_amount(self):
        result = self.allocations.filter(parent_allocation__isnull=True, is_borrow_sink=False).aggregate(total=models.Sum("allocated_amount"))
        return result["total"] or 0

    @property
    def unallocated_amount(self):
        return self.total_amount - self.allocated_amount

    def __str__(self):
        return f"{self.title} - {self.total_amount}"


class BudgetAllocation(models.Model):
    SCOPE_TYPES = [
        ("PROJECT", "Project"),
        ("WBS", "WBS"),
        ("TASK", "Task"),
        ("RESERVE", "Reserve"),
        ("ORG_UNIT", "Org Unit"),
    ]

    COST_TYPES = [
        ("LABOR", "Labor"),
        ("MATERIAL", "Material"),
        ("EQUIPMENT", "Equipment"),
        ("EXPENSE", "Expense"),
        ("SUBCONTRACT", "Subcontract"),
        ("COST", "Cost"),
        ("OVERHEAD", "Overhead"),
        ("RESERVE", "Reserve"),
    ]

    funding_source = models.ForeignKey(
        FundingSource,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    parent_allocation = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="child_allocations",
    )
    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="budget_allocations",
    )
    revision = models.ForeignKey(
        Revision,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="budget_allocations",
    )
    scope_type = models.CharField(max_length=16, choices=SCOPE_TYPES)
    wbs_node = models.ForeignKey(
        WBSNodeVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="budget_allocations",
    )
    task = models.ForeignKey(
        Task,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="budget_allocations",
    )
    org_unit = models.ForeignKey(
        'CustomUser.OrgUnit',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="budget_allocations",
    )
    cost_type = models.CharField(max_length=20, choices=COST_TYPES)
    allocated_amount = models.DecimalField(max_digits=18, decimal_places=2)
    is_borrow_sink = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=BUDGET_STATUS_CHOICES, default="DRAFT")
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    submitted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="submitted_budget_allocations")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_budget_allocations")
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="rejected_budget_allocations")
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["project__name", "scope_type", "cost_type"]
        indexes = [
            models.Index(fields=["project", "scope_type"]),
            models.Index(fields=["project", "cost_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["project", "status"]),
            models.Index(fields=["parent_allocation"]),
        ]

    def clean(self):
        super().clean()
        if self.allocated_amount is not None and self.allocated_amount <= 0 and not self.is_borrow_sink:
            raise ValidationError("Allocated amount must be greater than zero.")

        project_scopes = {"PROJECT", "WBS", "TASK"}
        if self.scope_type in project_scopes and not self.project_id:
            raise ValidationError("Project is required for project, WBS, and task allocations.")

        if self.revision_id and self.revision.project_id != self.project_id:
            raise ValidationError("Revision must belong to the selected project.")
        if self.task_id and self.task.project_id != self.project_id:
            raise ValidationError("Task must belong to the selected project.")
        if self.wbs_node_id and self.wbs_node.revision.project_id != self.project_id:
            raise ValidationError("WBS node must belong to the selected project.")

        if self.scope_type == "TASK" and not self.task_id:
            raise ValidationError("Task allocation requires a task.")
        if self.scope_type in {"WBS", "TASK"} and not self.wbs_node_id:
            raise ValidationError("WBS allocation requires a WBS node.")
        if self.scope_type != "TASK" and self.task_id:
            raise ValidationError("Task can only be set for TASK allocations.")
        if self.scope_type not in {"WBS", "TASK"} and self.wbs_node_id:
            raise ValidationError("WBS node can only be set for WBS or TASK allocations.")
        if self.scope_type != "ORG_UNIT" and self.org_unit_id:
            raise ValidationError("Org unit can only be set for ORG_UNIT allocations.")

        if not self.funding_source_id:
            raise ValidationError("Funding source is required.")
        if self.status == "APPROVED" and self.funding_source.status != "APPROVED":
            raise ValidationError("Approved allocations require an approved funding source.")
        if self.parent_allocation_id:
            if self.pk and self.parent_allocation_id == self.pk:
                raise ValidationError("Budget allocation cannot be its own parent.")
            if self.parent_allocation.funding_source_id != self.funding_source_id:
                raise ValidationError("Child allocation must use the same funding source as its parent.")
            if self.parent_allocation.cost_type != self.cost_type:
                raise ValidationError("Child allocation must use the same cost type as its parent.")
            if self.status == "APPROVED" and self.parent_allocation.status != "APPROVED":
                raise ValidationError("Approved child allocations require an approved parent allocation.")
            self._validate_parent_scope()
            existing_children = self.parent_allocation.child_allocations.all()
            if self.pk:
                existing_children = existing_children.exclude(pk=self.pk)
            child_total = existing_children.aggregate(total=models.Sum("allocated_amount"))["total"] or 0
            if child_total + self.allocated_amount > self.parent_allocation.allocated_amount:
                raise ValidationError("Child allocations cannot exceed parent allocation amount.")

        if self.pk and self.allocated_amount is not None:
            required_capacity = self.actual_amount + self.child_allocated_amount + self.borrowed_out_amount() - self.borrowed_in_amount()
            if required_capacity < 0:
                required_capacity = 0
            if self.allocated_amount < required_capacity:
                raise ValidationError("Allocation amount cannot be less than actual usage, child budgets, and borrowed-out capacity.")

        if not self.parent_allocation_id:
            existing = BudgetAllocation.objects.filter(
                funding_source=self.funding_source,
                parent_allocation__isnull=True,
                is_borrow_sink=False,
            )
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            current_total = existing.aggregate(total=models.Sum("allocated_amount"))["total"] or 0
            if self.funding_source_id and current_total + self.allocated_amount > self.funding_source.total_amount:
                raise ValidationError("Allocations cannot exceed funding source amount.")

    def _task_wbs_node_for_validation(self, task, revision):
        if not task:
            return None
        versions = task.versions.filter(is_deleted=False)
        if revision:
            version = versions.filter(revision=revision).first()
        else:
            version = versions.filter(revision_id=self.project.current_execution_revision_id).first()
        return version.wbs_node if version else None

    def _validate_parent_scope(self):
        parent = self.parent_allocation
        if not parent:
            return

        if parent.project_id and self.project_id != parent.project_id:
            raise ValidationError("Child allocation must stay inside the parent project.")

        if parent.scope_type == "PROJECT":
            if self.scope_type == "ORG_UNIT":
                raise ValidationError("Project budget children cannot target org units.")
            return

        if parent.scope_type == "WBS":
            if self.scope_type not in {"WBS", "TASK"}:
                raise ValidationError("WBS budget children must target WBS nodes or tasks inside that WBS.")
            if self.scope_type == "WBS":
                if not self.wbs_node_id or not self.wbs_node.is_descendant_of(parent.wbs_node, include_self=True):
                    raise ValidationError("Child WBS allocation must be inside the parent WBS.")
            if self.scope_type == "TASK":
                task_wbs = self._task_wbs_node_for_validation(self.task, self.revision or parent.revision)
                if not task_wbs or not task_wbs.is_descendant_of(parent.wbs_node, include_self=True):
                    raise ValidationError("Child task allocation must be inside the parent WBS.")
            return

        if parent.scope_type == "TASK":
            if self.scope_type != "TASK" or self.task_id != parent.task_id:
                raise ValidationError("Task budget children must target the same task.")
            return

        if parent.scope_type == "ORG_UNIT":
            if self.scope_type != "ORG_UNIT":
                raise ValidationError("Org unit budget children must stay inside org unit scope.")
            if parent.org_unit_id and self.org_unit_id != parent.org_unit_id:
                raise ValidationError("Org unit budget children must stay inside the same org unit.")

    @property
    def actual_amount(self):
        consumed = self.consumptions.aggregate(total=models.Sum("amount"))["total"] or 0
        legacy_direct = self.transactions.filter(budget_consumptions__isnull=True).aggregate(
            total=models.Sum("amount")
        )["total"] or 0
        return consumed + legacy_direct

    @property
    def child_allocated_amount(self):
        result = self.child_allocations.aggregate(total=models.Sum("allocated_amount"))
        return result["total"] or 0

    def borrowed_in_amount(self):
        result = self.borrowed_in_records.filter(status__in=["APPROVED", "SETTLED"]).aggregate(total=models.Sum("amount"))
        return result["total"] or 0

    def borrowed_out_amount(self, exclude_borrow_id=None):
        queryset = self.borrowed_out_records.filter(status__in=["APPROVED", "SETTLED"])
        if exclude_borrow_id:
            queryset = queryset.exclude(pk=exclude_borrow_id)
        result = queryset.aggregate(total=models.Sum("amount"))
        return result["total"] or 0

    def borrow_available_amount(self, exclude_borrow_id=None):
        return self.allocated_amount + self.borrowed_in_amount() - self.actual_amount - self.child_allocated_amount - self.borrowed_out_amount(exclude_borrow_id)

    @property
    def remaining_amount(self):
        return self.borrow_available_amount()

    def __str__(self):
        target = self.project or self.org_unit or "Company"
        return f"{target} - {self.scope_type} - {self.allocated_amount}"


class CostTransaction(models.Model):
    TRANSACTION_TYPES = [
        ("LABOR", "Labor"),
        ("MATERIAL", "Material"),
        ("EQUIPMENT", "Equipment"),
        ("EXPENSE", "Expense"),
        ("SUBCONTRACT", "Subcontract"),
        ("COST","Cost"),
    ]

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="cost_transactions"
    )

    revision = models.ForeignKey(
        Revision,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cost_transactions"
    )

    task = models.ForeignKey(
        Task,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cost_transactions"
    )

    assignment = models.ForeignKey(
        Assignment,
        null=True,
        blank=True,
        on_delete=models.PROTECT
    )

    resource_rate = models.ForeignKey(
        ResourceRate,
        null=True,
        blank=True,
        on_delete=models.PROTECT
    )

    expense_type = models.ForeignKey(
        ExpenseType,
        null=True,
        blank=True,
        on_delete=models.PROTECT
    )

    resource = models.ForeignKey(
        Resource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    budget_allocation = models.ForeignKey(
        BudgetAllocation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transactions",
    )

    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPES
    )

    transaction_date = models.DateField()

    quantity = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=1
    )

    expense_rate = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True
    )

    amount = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        editable=False
    )

    unit_rate = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True
    )


    description = models.TextField(blank=True)

    created_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date", "-created_at"]

    def clean(self):
        super().clean()

        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

        if (
            self.budget_allocation_id
            and self.budget_allocation.project_id
            and self.budget_allocation.project_id != self.project_id
        ):
            raise ValidationError("Budget allocation must belong to the selected project.")

        if self.transaction_type == "EXPENSE":

            if not self.expense_type:
                raise ValidationError("ExpenseType is required.")

            if self.expense_rate is None:
                raise ValidationError("Expense rate is required.")

            if self.resource_rate:
                raise ValidationError("Expense cannot have ResourceRate.")

            if self.assignment:
                raise ValidationError("Expense cannot have Assignment.")

            if self.expense_rate < 0:
                raise ValidationError("Expense rate cannot be negative.")

        else:

            if not self.assignment:
                raise ValidationError("Assignment is required.")

            if self.transaction_type == "COST" and (self.unit_rate is None or self.unit_rate < 0):
                raise ValidationError("Unit rate is required for cost resources.")

            if self.transaction_type != "COST" and not self.resource_rate:
                raise ValidationError("ResourceRate is required.")

            if self.resource_rate and self.resource_rate.resource != self.assignment.resource:
                raise ValidationError(
                    "ResourceRate must belong to Assignment resource."
                )

            if self.task_id and self.assignment.task_id != self.task_id:
                raise ValidationError("Assignment must belong to the selected task.")

            if self.revision_id and self.assignment.revision_id != self.revision_id:
                raise ValidationError("Assignment must belong to the selected revision.")

            if self.resource_rate and self.resource_rate.regular_rate < 0:
                raise ValidationError("Resource rate cannot be negative.")

    def save(self, *args, **kwargs):

        if self.transaction_type == "EXPENSE":
            if self.expense_rate is None:
                raise ValidationError("Expense rate is required.")
            rate = self.expense_rate
            self.assignment = None
            self.resource_rate = None
            self.resource = None
        else:
            if not self.assignment:
                raise ValidationError("Assignment is required.")
            if self.assignment:
                if not self.task_id:
                    self.task = self.assignment.task
                if not self.revision_id:
                    self.revision = self.assignment.revision
                self.resource = self.assignment.resource
            if self.transaction_type == "COST":
                if self.unit_rate is None:
                    raise ValidationError("Unit rate is required for cost resources.")
                rate = self.unit_rate
                self.resource_rate = None
            else:
                if not self.resource_rate:
                    raise ValidationError("ResourceRate is required.")
                rate = self.resource_rate.regular_rate
                if self.unit_rate is None:
                    self.unit_rate = rate

        self.amount = (self.quantity * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.full_clean()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.transaction_type} - {self.amount}"


class BudgetConsumption(models.Model):
    transaction = models.ForeignKey(
        CostTransaction,
        on_delete=models.CASCADE,
        related_name="budget_consumptions",
    )
    budget_allocation = models.ForeignKey(
        BudgetAllocation,
        on_delete=models.PROTECT,
        related_name="consumptions",
    )
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= 0:
            raise ValidationError("Budget consumption amount must be greater than zero.")

    def __str__(self):
        return f"{self.transaction_id} -> {self.budget_allocation_id}: {self.amount}"

class TaskFinancialPlan(models.Model):
    DIRECTION_PAYABLE = "payable"
    DIRECTION_RECEIVABLE = "receivable"
    DIRECTION_CHOICES = [
        (DIRECTION_PAYABLE, "Payable"),
        (DIRECTION_RECEIVABLE, "Receivable"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="financial_plans")
    direction = models.CharField(max_length=16, choices=DIRECTION_CHOICES)
    contract_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=8, default="IRR")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_task_financial_plans")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["task_id", "-created_at"]
        indexes = [models.Index(fields=["task", "status"]), models.Index(fields=["status"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(contract_amount__gt=0), name="task_financial_plan_amount_positive"),
            models.UniqueConstraint(fields=["task"], condition=models.Q(status="active"), name="one_active_financial_plan_per_task"),
        ]

    def clean(self):
        super().clean()
        if self.contract_amount is not None and self.contract_amount <= 0:
            raise ValidationError("Contract amount must be greater than zero.")

    def __str__(self):
        return f"{self.task_id} - {self.direction} - {self.contract_amount} {self.currency}"


class PaymentMilestone(models.Model):
    TRIGGER_BEFORE_START = "before_start"
    TRIGGER_APPROVED_PROGRESS = "approved_progress"
    TRIGGER_TASK_COMPLETION = "task_completion"
    TRIGGER_BEFORE_DELIVERY = "before_delivery"
    TRIGGER_FIXED_DATE = "fixed_date"
    TRIGGER_MANUAL = "manual"
    TRIGGER_CHOICES = [
        (TRIGGER_BEFORE_START, "Before start"),
        (TRIGGER_APPROVED_PROGRESS, "Approved progress"),
        (TRIGGER_TASK_COMPLETION, "Task completion"),
        (TRIGGER_BEFORE_DELIVERY, "Before delivery"),
        (TRIGGER_FIXED_DATE, "Fixed date"),
        (TRIGGER_MANUAL, "Manual"),
    ]

    AMOUNT_PERCENTAGE = "percentage"
    AMOUNT_FIXED = "fixed"
    AMOUNT_TYPE_CHOICES = [(AMOUNT_PERCENTAGE, "Percentage"), (AMOUNT_FIXED, "Fixed")]

    STATUS_LOCKED = "locked"
    STATUS_ELIGIBLE = "eligible"
    STATUS_INVOICED = "invoiced"
    STATUS_PARTIALLY_PAID = "partially_paid"
    STATUS_PAID = "paid"
    STATUS_OVERDUE = "overdue"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_LOCKED, "Locked"),
        (STATUS_ELIGIBLE, "Eligible"),
        (STATUS_INVOICED, "Invoiced"),
        (STATUS_PARTIALLY_PAID, "Partially paid"),
        (STATUS_PAID, "Paid"),
        (STATUS_OVERDUE, "Overdue"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    financial_plan = models.ForeignKey(TaskFinancialPlan, on_delete=models.PROTECT, related_name="milestones")
    title = models.CharField(max_length=255)
    sequence = models.PositiveIntegerField()
    trigger_type = models.CharField(max_length=32, choices=TRIGGER_CHOICES)
    amount_type = models.CharField(max_length=16, choices=AMOUNT_TYPE_CHOICES)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    fixed_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    progress_threshold = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    blocks_task_start = models.BooleanField(default=False)
    blocks_task_delivery = models.BooleanField(default=False)
    blocks_progress_after_threshold = models.BooleanField(default=False)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_LOCKED)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["financial_plan", "sequence", "id"]
        indexes = [
            models.Index(fields=["financial_plan", "status"]),
            models.Index(fields=["status"]),
            models.Index(fields=["due_date"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["financial_plan", "sequence"], name="unique_financial_milestone_sequence"),
            models.CheckConstraint(condition=models.Q(percentage__isnull=True) | (models.Q(percentage__gt=0) & models.Q(percentage__lte=100)), name="payment_milestone_percentage_range"),
            models.CheckConstraint(condition=models.Q(progress_threshold__isnull=True) | (models.Q(progress_threshold__gte=0) & models.Q(progress_threshold__lte=100)), name="payment_milestone_progress_range"),
            models.CheckConstraint(condition=models.Q(fixed_amount__isnull=True) | models.Q(fixed_amount__gt=0), name="payment_milestone_fixed_amount_positive"),
        ]

    def clean(self):
        super().clean()
        if self.amount_type == self.AMOUNT_PERCENTAGE:
            if self.percentage is None:
                raise ValidationError("Percentage is required for percentage milestones.")
            if self.fixed_amount is not None:
                raise ValidationError("Fixed amount must be empty for percentage milestones.")
            if self.percentage <= 0 or self.percentage > 100:
                raise ValidationError("Percentage must be greater than 0 and at most 100.")
        elif self.amount_type == self.AMOUNT_FIXED:
            if self.fixed_amount is None:
                raise ValidationError("Fixed amount is required for fixed milestones.")
            if self.percentage is not None:
                raise ValidationError("Percentage must be empty for fixed milestones.")
            if self.fixed_amount <= 0:
                raise ValidationError("Fixed amount must be greater than zero.")
        if self.trigger_type == self.TRIGGER_APPROVED_PROGRESS:
            if self.progress_threshold is None:
                raise ValidationError("Progress threshold is required for approved progress milestones.")
            if self.progress_threshold < 0 or self.progress_threshold > 100:
                raise ValidationError("Progress threshold must be between 0 and 100.")
        if self.trigger_type == self.TRIGGER_FIXED_DATE and self.due_date is None:
            raise ValidationError("Due date is required for fixed date milestones.")

    @property
    def has_transactions(self):
        return self.transactions.exists()

    def __str__(self):
        return f"{self.financial_plan_id} / {self.sequence} - {self.title}"


class PaymentTransaction(models.Model):
    TYPE_PAYMENT = "payment"
    TYPE_REFUND = "refund"
    TYPE_ADJUSTMENT = "adjustment"
    TRANSACTION_TYPE_CHOICES = [
        (TYPE_PAYMENT, "Payment"),
        (TYPE_REFUND, "Refund"),
        (TYPE_ADJUSTMENT, "Adjustment"),
    ]

    milestone = models.ForeignKey(PaymentMilestone, on_delete=models.PROTECT, related_name="transactions")
    transaction_type = models.CharField(max_length=16, choices=TRANSACTION_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    transaction_date = models.DateField()
    reference_number = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_payment_transactions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["milestone", "transaction_date"]),
            models.Index(fields=["transaction_date"]),
            models.Index(fields=["transaction_type"]),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(transaction_type="adjustment") | models.Q(amount__gt=0), name="payment_refund_amount_positive"),
        ]

    def clean(self):
        super().clean()
        if self.transaction_type in {self.TYPE_PAYMENT, self.TYPE_REFUND} and self.amount <= 0:
            raise ValidationError("Payment and refund amounts must be greater than zero.")
        if self.transaction_type == self.TYPE_ADJUSTMENT and self.amount == 0:
            raise ValidationError("Adjustment amount cannot be zero.")

    def __str__(self):
        return f"{self.milestone_id} - {self.transaction_type} - {self.amount}"


class BudgetBorrow(models.Model):
    BORROW_STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("SUBMITTED", "Submitted"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
        ("SETTLED", "Settled"),
    ]

    from_allocation = models.ForeignKey(
        BudgetAllocation,
        on_delete=models.PROTECT,
        related_name="borrowed_out_records",
    )
    to_allocation = models.ForeignKey(
        BudgetAllocation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="borrowed_in_records",
    )
    destination_project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.PROTECT, related_name="budget_borrow_destinations")
    destination_revision = models.ForeignKey(Revision, null=True, blank=True, on_delete=models.SET_NULL, related_name="budget_borrow_destinations")
    destination_scope_type = models.CharField(max_length=16, choices=BudgetAllocation.SCOPE_TYPES, blank=True)
    destination_wbs_node = models.ForeignKey(WBSNodeVersion, null=True, blank=True, on_delete=models.PROTECT, related_name="budget_borrow_destinations")
    destination_task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.PROTECT, related_name="budget_borrow_destinations")
    destination_org_unit = models.ForeignKey('CustomUser.OrgUnit', null=True, blank=True, on_delete=models.PROTECT, related_name="budget_borrow_destinations")
    destination_cost_type = models.CharField(max_length=20, choices=BudgetAllocation.COST_TYPES, blank=True)
    destination_description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=BORROW_STATUS_CHOICES, default="DRAFT")
    requested_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="requested_budget_borrows")
    submitted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="submitted_budget_borrows")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_budget_borrows")
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="rejected_budget_borrows")
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    settled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="settled_budget_borrows")
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["from_allocation", "status"]),
            models.Index(fields=["to_allocation", "status"]),
        ]

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= 0:
            raise ValidationError("Borrow amount must be greater than zero.")
        if self.from_allocation_id:
            if self.from_allocation.status != "APPROVED":
                raise ValidationError("Budget borrow requires an approved source allocation.")
            if self.to_allocation_id and self.from_allocation_id == self.to_allocation_id:
                raise ValidationError("Borrow source and destination cannot be the same allocation.")
            if self.to_allocation_id and self.to_allocation.status != "APPROVED":
                raise ValidationError("Budget borrow requires an approved destination allocation.")
            if not self.to_allocation_id:
                if not self.destination_scope_type:
                    raise ValidationError("Destination scope type is required when destination allocation is new.")
                if not self.destination_cost_type:
                    raise ValidationError("Destination cost type is required when destination allocation is new.")
                if self.destination_scope_type in {"PROJECT", "WBS", "TASK"} and not self.destination_project_id:
                    raise ValidationError("Destination project is required for project, WBS, and task borrow targets.")
                if self.destination_scope_type in {"WBS", "TASK"} and not self.destination_wbs_node_id:
                    raise ValidationError("Destination WBS is required for WBS and task borrow targets.")
                if self.destination_scope_type == "TASK" and not self.destination_task_id:
                    raise ValidationError("Destination task is required for task borrow targets.")
            if self.status == "APPROVED" and self.amount > self.from_allocation.borrow_available_amount(exclude_borrow_id=self.pk):
                raise ValidationError("Borrow amount exceeds source allocation available capacity.")

    def __str__(self):
        return f"{self.from_allocation_id} -> {self.to_allocation_id}: {self.amount}"


class UnfundedForecastCost(models.Model):
    SCOPE_TYPES = BudgetAllocation.SCOPE_TYPES
    COST_TYPES = BudgetAllocation.COST_TYPES
    CONFIDENCE_CHOICES = [
        ("ESTIMATE", "Estimate"),
        ("EXPECTED", "Expected"),
        ("COMMITTED", "Committed"),
    ]
    STATUS_CHOICES = [
        ("PLANNED", "Planned"),
        ("DUE", "Due"),
        ("OVERDUE", "Overdue"),
        ("FUNDED", "Funded"),
        ("CANCELLED", "Cancelled"),
    ]

    title = models.CharField(max_length=255)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="unfunded_forecast_costs")
    revision = models.ForeignKey(Revision, null=True, blank=True, on_delete=models.SET_NULL, related_name="unfunded_forecast_costs")
    scope_type = models.CharField(max_length=16, choices=SCOPE_TYPES, default="RESERVE")
    wbs_node = models.ForeignKey(WBSNodeVersion, null=True, blank=True, on_delete=models.SET_NULL, related_name="unfunded_forecast_costs")
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="unfunded_forecast_costs")
    org_unit = models.ForeignKey('CustomUser.OrgUnit', null=True, blank=True, on_delete=models.SET_NULL, related_name="unfunded_forecast_costs")
    cost_type = models.CharField(max_length=20, choices=COST_TYPES, default="EXPENSE")
    forecast_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    confidence = models.CharField(max_length=16, choices=CONFIDENCE_CHOICES, default="ESTIMATE")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="PLANNED")
    linked_allocation = models.ForeignKey(BudgetAllocation, null=True, blank=True, on_delete=models.SET_NULL, related_name="forecast_costs")
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_unfunded_forecast_costs")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["forecast_date", "id"]
        indexes = [
            models.Index(fields=["forecast_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["project", "forecast_date"]),
            models.Index(fields=["scope_type", "cost_type"]),
        ]

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= 0:
            raise ValidationError("Forecast amount must be greater than zero.")
        if self.scope_type in {"PROJECT", "WBS", "TASK"} and not self.project_id:
            raise ValidationError("Project is required for project, WBS, and task forecast costs.")
        if self.revision_id and self.project_id and self.revision.project_id != self.project_id:
            raise ValidationError("Revision must belong to the selected project.")
        if self.wbs_node_id and self.project_id and self.wbs_node.revision.project_id != self.project_id:
            raise ValidationError("WBS node must belong to the selected project.")
        if self.task_id and self.project_id and self.task.project_id != self.project_id:
            raise ValidationError("Task must belong to the selected project.")
        if self.scope_type == "TASK" and not self.task_id:
            raise ValidationError("Task forecast requires a task.")
        if self.scope_type in {"WBS", "TASK"} and not self.wbs_node_id:
            raise ValidationError("WBS forecast requires a WBS node.")
        if self.scope_type != "TASK" and self.task_id:
            raise ValidationError("Task can only be set for TASK forecasts.")
        if self.scope_type not in {"WBS", "TASK"} and self.wbs_node_id:
            raise ValidationError("WBS node can only be set for WBS or TASK forecasts.")
        if self.scope_type != "ORG_UNIT" and self.org_unit_id:
            raise ValidationError("Org unit can only be set for ORG_UNIT forecasts.")

    def __str__(self):
        return f"{self.title} - {self.amount} - {self.forecast_date}"

