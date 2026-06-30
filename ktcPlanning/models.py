import uuid
import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from mptt.models import MPTTModel
from mptt.fields import TreeForeignKey
from ktcPlanning.validators import validate_chat_file

User = get_user_model()


# =========================================================
# 1. PROJECT
# =========================================================

class Project(models.Model):
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

    def __str__(self):
        return self.name

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
            project_start=instance.start_date if instance.start_date else instance.created_at,
            project_end=instance.end_date if instance.end_date else instance.created_at
        )

        # ۲. ایجاد گره پایه WBS
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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    executed_at = models.DateTimeField(auto_now_add=True)
    executed_by = models.ForeignKey(User, on_delete=models.PROTECT)
    description = models.TextField(blank=True)
    # پروژه‌هایی که در این اجرای سراسری شرکت داده شده‌اند
    participating_projects = models.ManyToManyField(Project, related_name="leveling_runs")
    # وضعیت لولینگ: در حد پیش‌نویس/شبیه‌سازی است یا روی برنامه‌ها اعمال نهایی شده؟
    is_committed = models.BooleanField(default=False, verbose_name="اعمال نهایی شده روی برنامه اصلی")

    def __str__(self):
        return f"Run {self.id} - {self.executed_at.date()}"


class TaskLevelingMetrics(models.Model):
    """ذخیره تاریخ‌های پیشنهادی تسطیح، بدون دستکاری لایه اصلی TaskVersion"""
    leveling_run = models.ForeignKey(
        GlobalLevelingRun, on_delete=models.CASCADE, related_name="task_metrics"
    )
    task_version = models.ForeignKey(
        TaskVersion, on_delete=models.CASCADE, related_name="leveling_metrics"
    )

    # تاریخ‌های پیشنهادی موتور تسطیح (تداخل‌ها در این تاریخ‌ها حل شده‌اند)
    leveled_start = models.DateTimeField()
    leveled_finish = models.DateTimeField()

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

    remaining_capacity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    class Meta:
        unique_together = [
            (
                "revision",
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
    report_date = models.DateField(auto_now_add=True)

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

        if self.transaction_type == "EXPENSE":

            if not self.expense_type:
                raise ValidationError("ExpenseType is required.")

            if self.resource_rate:
                raise ValidationError("Expense cannot have ResourceRate.")

        else:

            if not self.assignment:
                raise ValidationError("Assignment is required.")

            if not self.resource_rate:
                raise ValidationError("ResourceRate is required.")

            if self.resource_rate.resource != self.assignment.resource:
                raise ValidationError(
                    "ResourceRate must belong to Assignment resource."
                )

    def save(self, *args, **kwargs):

        if self.transaction_type == "EXPENSE":
            rate = self.expense_rate
        else:
            rate = self.resource_rate.regular_rate

        self.amount = self.quantity * rate

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.transaction_type} - {self.amount}"