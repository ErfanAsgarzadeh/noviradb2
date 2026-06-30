from django.db import models
from django.contrib.auth.models import AbstractUser


class OrgUnit(models.Model):
    """واحد سازمانی (دپارتمان)"""
    name = models.CharField(max_length=255, verbose_name="نام واحد")
    description = models.CharField(max_length=255, blank=True, default='')
    manager = models.ForeignKey(
        'CustomUser', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='managed_units',
        verbose_name="مدیر واحد"
    )
    # علامت‌گذاری «واحدِ برنامه‌ریزی». فقط یک واحد در سیستم می‌تواند این فلگ را داشته باشد.
    # مدیرِ همین واحد، نقشِ «مدیرِ برنامه‌ریزی» را در workflow تاییدها بازی می‌کند.
    is_planning_unit = models.BooleanField(
        default=False,
        verbose_name="واحدِ برنامه‌ریزی؟",
        help_text="فقط یک واحد می‌تواند به‌عنوان واحدِ برنامه‌ریزی علامت‌گذاری شود."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # تضمین می‌کند که فقط یک ردیف با is_planning_unit=True وجود داشته باشد.
            # ردیف‌های با مقدار False محدودیت ندارند.
            models.UniqueConstraint(
                fields=['is_planning_unit'],
                condition=models.Q(is_planning_unit=True),
                name='only_one_planning_unit',
            ),
        ]

    def __str__(self):
        return self.name


class CustomUser(AbstractUser):
    ORG_ROLES = [
        ("company_admin",   "مدیر سیستم"),
        ("company_pm",      "مدیر پروژه شرکت"),
        ("unit_manager",    "مدیر واحد"),
        ("project_manager", "مدیر پروژه"),
        ("member",          "عضو"),
    ]

    employee_code = models.CharField(max_length=10, unique=True, null=True, blank=True, verbose_name="Employee Code")
    job_title = models.CharField(max_length=100, null=True, blank=True, verbose_name="Job Title")

    # ساختار سازمانی
    unit = models.ForeignKey(
        OrgUnit, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='members',
        verbose_name="واحد سازمانی"
    )
    org_role = models.CharField(max_length=20, choices=ORG_ROLES, default="member", verbose_name="نقش سازمانی")

    # کنترلِ دسترسی در سطحِ صفحه (منو). null = دسترسی به همهٔ صفحات (پیش‌فرض).
    # در غیر این صورت لیستی از مسیرهای مجاز، مثلاً ["/DashBoard/Home", "/DashBoard/MyTask"].
    allowed_pages = models.JSONField(
        null=True, blank=True, default=None,
        verbose_name="صفحاتِ مجاز",
        help_text="null یعنی دسترسی به همهٔ صفحات. در غیر این صورت لیستِ مسیرهای مجاز."
    )

    def __str__(self):
        return f"{self.username}({self.job_title if self.job_title else self.username})"
