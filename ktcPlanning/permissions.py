"""
منطق مرکزی مجوزدهی (Authorization) بر اساس نقشِ سازمانی و واحد.

طبقاتِ نقش (org_role):
  - company_admin / company_pm  → سطحِ شرکت
  - unit_manager                → مدیرِ واحد (به‌شرطِ اینکه واقعاً OrgUnit.manager باشد)
  - project_manager             → مدیرِ پروژه (روی پروژه‌های ساختهٔ خودش)
  - member                      → عضو معمولی

قواعد سطحِ بالا:
  - مشاهده (read): فعلاً برای همهٔ کاربرانِ احرازشده باز است (در PR2 محدود می‌شود).
  - ساختِ پروژه: company-level + unit_manager + project_manager.
  - ویرایشِ پروژه: company-level، یا OrgUnit.manager واحدِ پروژه، یا created_by پروژه.
  - تاییدِ نهاییِ گزارشِ پروژهٔ شرکتی: مدیرِ واحدِ برنامه‌ریزی (PR3).

نکتهٔ امنیتی: superuser همیشه دسترسی کامل دارد (safety net).
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS


# ─────────────────────────────────────────────────────────────────────────────
# Helperهای بنیادی
# ─────────────────────────────────────────────────────────────────────────────

def _role(user):
    return getattr(user, 'org_role', 'member') or 'member'


def is_company_level(user) -> bool:
    """مدیرِ سیستم یا مدیرِ پروژه‌های شرکت (یا superuser). — برای دسترسیِ پروژه‌ها."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or _role(user) in ('company_admin', 'company_pm')


def is_system_admin(user) -> bool:
    """
    فقط مدیرِ سیستم (company_admin) یا superuser.
    برای کارهای ادمینیِ سیستم مثل مدیریتِ کاربران و واحدها استفاده می‌شود.
    توجه: company_pm (مدیرِ پروژهٔ شرکت) عمداً اینجا مجاز نیست — او فقط پروژه‌ها را مدیریت می‌کند.
    """
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or _role(user) == 'company_admin'


def can_create_project(user) -> bool:
    """چه کسانی می‌توانند پروژهٔ جدید بسازند."""
    if not user or not user.is_authenticated:
        return False
    return (
        user.is_superuser
        or _role(user) in ('company_admin', 'company_pm', 'unit_manager', 'project_manager')
    )


def manages_unit(user, unit) -> bool:
    """
    مدیرِ واقعیِ یک واحد فقط کسی است که در `OrgUnit.manager` آن واحد ثبت شده باشد.

    قبلاً این تابع به‌اشتباه هر کاربری با org_role='unit_manager' که عضوِ واحد باشد را
    مدیرِ آن واحد در نظر می‌گرفت؛ این منجر به وجودِ چندین «مدیرِ واحدِ همتراز» می‌شد و
    اصلِ «یک نفر مدیرِ هر واحد» را نقض می‌کرد. این رفتار اصلاح شده است: فقط FK رسمی
    معتبر است.
    """
    if not user or not user.is_authenticated or not unit:
        return False
    return getattr(unit, 'manager_id', None) == user.id


# ─────────────────────────────────────────────────────────────────────────────
# واحدِ برنامه‌ریزی و delegation (آماده‌سازیِ پایه)
# ─────────────────────────────────────────────────────────────────────────────

def get_planning_unit():
    """
    واحدِ علامت‌خوردهٔ `is_planning_unit=True` را برمی‌گرداند (اگر وجود داشته باشد).
    Lazy import تا حلقهٔ ایمپورت با اپ CustomUser ایجاد نشود.
    """
    from CustomUser.models import OrgUnit
    return OrgUnit.objects.filter(is_planning_unit=True).first()


def get_planning_manager():
    """مدیرِ واحدِ برنامه‌ریزی (یا None اگر تنظیم نشده باشد)."""
    unit = get_planning_unit()
    return unit.manager if unit else None


def effective_actor_ids(user) -> set:
    """
    مجموعهٔ ID کاربرانی که این کاربر امروز «به‌نمایندگیِ آن‌ها» می‌تواند عمل کند.

    امروز فقط شامل خودِ کاربر است. در آینده، با افزودنِ مدلِ Delegation
    (انتقالِ کارتابل برای مدت محدود)، این مجموعه به ID کاربرانی که اختیارشان را
    به این کاربر داده‌اند گسترش می‌یابد. هر تابعِ بررسیِ نقشِ خاص (مثلِ
    is_planning_manager) از این تابع استفاده می‌کند تا delegation به‌صورتِ
    شفاف و بدون refactor در همه‌جا فعال شود.
    """
    if not user or not user.is_authenticated:
        return set()
    return {user.id}


def is_planning_manager(user) -> bool:
    """آیا این کاربر، مدیرِ واحدِ برنامه‌ریزی است (یا اختیارش را دارد)؟"""
    if not user or not user.is_authenticated:
        return False
    pm = get_planning_manager()
    if not pm:
        return False
    return pm.id in effective_actor_ids(user)


# ─────────────────────────────────────────────────────────────────────────────
# مجوزِ ویرایش پروژه و سایر helperهای رایج
# ─────────────────────────────────────────────────────────────────────────────

def can_edit_project(user, project) -> bool:
    """آیا کاربر اجازهٔ ویرایش این پروژه را دارد؟"""
    if not user or not user.is_authenticated:
        return False
    if is_company_level(user):
        return True
    # سازندهٔ پروژه (شاملِ project_manager که پروژه‌ی خودش را ساخته)
    if getattr(project, 'created_by_id', None) == user.id:
        return True
    # مدیرِ واحدِ صاحبِ پروژه
    if manages_unit(user, getattr(project, 'owner_unit', None)):
        return True
    return False


def require_can_create_project(user):
    if not can_create_project(user):
        raise PermissionDenied("شما اجازه‌ی ساخت پروژه را ندارید.")


def require_can_edit_project(user, project):
    if not can_edit_project(user, project):
        raise PermissionDenied("شما اجازه‌ی ویرایش این پروژه را ندارید.")


# ─────────────────────────────────────────────────────────────────────────────
# Read scoping — کدام پروژه‌ها برای یک کاربر قابلِ مشاهده‌اند
# ─────────────────────────────────────────────────────────────────────────────

def accessible_project_ids(user):
    """
    QuerySet از idِ پروژه‌هایی که کاربر اجازهٔ «مشاهده» دارد (برای فیلترِ `__in`).
    سطحِ شرکت → همه. وگرنه اجتماعی از: created_by، مدیرِ owner_unit،
    پروژه‌هایی که در آن‌ها TaskRole دارد، cross-unit، و ProjectViewer.
    """
    from django.db.models import Q
    from .models import Project

    base = Project.objects.filter(is_deleted=False)
    if is_company_level(user):
        return base.values_list('id', flat=True)
    if not (user and user.is_authenticated):
        return base.none().values_list('id', flat=True)

    q = (
        Q(created_by=user)
        | Q(owner_unit__manager=user)
        | Q(revisions__task_roles__user=user)
        | Q(revisions__task_roles__user__unit__manager=user)
    )
    try:
        from .models import ProjectViewer  # noqa: F401
        q |= Q(viewers__user=user)
    except Exception:
        pass

    return base.filter(q).values_list('id', flat=True).distinct()


def accessible_projects(user):
    """QuerySet پروژه‌هایی که کاربر اجازهٔ مشاهده دارد."""
    from .models import Project
    return Project.objects.filter(is_deleted=False, id__in=accessible_project_ids(user))


def can_view_project(user, project) -> bool:
    """آیا کاربر اجازهٔ مشاهدهٔ این پروژهٔ مشخص را دارد؟"""
    if not project:
        return False
    if is_company_level(user):
        return True
    return project.id in set(accessible_project_ids(user))


def can_manage_viewers(user, project) -> bool:
    """افزودن/حذفِ مشاهده‌گر (Viewer) فقط توسطِ سازندهٔ پروژه (و سطحِ شرکت)."""
    if not user or not user.is_authenticated:
        return False
    if is_company_level(user):
        return True
    return getattr(project, 'created_by_id', None) == user.id


def require_can_manage_viewers(user, project):
    if not can_manage_viewers(user, project):
        raise PermissionDenied("تنها سازندهٔ پروژه می‌تواند مشاهده‌گر اضافه یا حذف کند.")


# ─────────────────────────────────────────────────────────────────────────────
# Task-level role helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_task_reviewer(user, task) -> bool:
    """
    آیا این کاربر روی این تسک نقشِ reviewer یا project manager دارد؟
    فقط نقش‌های موجود در نسخهٔ بازِ پروژه را در نظر می‌گیرد.
    """
    if not user or not user.is_authenticated or not task:
        return False
    from .models import TaskRole
    return TaskRole.objects.filter(
        task=task,
        user=user,
        role__in=['reviewer', 'project manager'],
        revision__approved_at__isnull=True,
    ).exists()


def can_assign_task_role(actor, task, target_user, role) -> bool:
    """
    آیا «actor» اجازه دارد «target_user» را با نقش «role» روی این «task» تخصیص دهد؟

    قواعد:
      - superuser / company_admin / company_pm → همیشه مجاز
      - role == 'reviewer'  → actor باید can_edit_project داشته باشد، و target_user عضوِ یک واحد باشد
      - role == 'executor'  → actor باید خودش reviewer این تسک باشد، و target_user هم‌واحدِ actor باشد
      - role == 'project manager' → فقط سطحِ شرکت
      - role == 'owner'    → فقط can_edit_project
    """
    if not actor or not actor.is_authenticated:
        return False
    if not task or not target_user:
        return False

    if is_company_level(actor):
        return True

    if role == 'reviewer':
        if not can_edit_project(actor, task.project):
            return False
        # target_user باید به یک واحد وصل باشد
        return getattr(target_user, 'unit_id', None) is not None

    if role == 'executor':
        # actor باید reviewer این تسک باشد
        if not is_task_reviewer(actor, task):
            return False
        # actor باید واحد داشته باشد
        actor_unit = getattr(actor, 'unit_id', None)
        if not actor_unit:
            return False
        # target_user باید هم‌واحدِ actor باشد
        return getattr(target_user, 'unit_id', None) == actor_unit

    if role == 'project manager':
        # نقشِ مدیر پروژه فقط توسطِ سطحِ شرکت قابلِ تخصیص است
        return False  # is_company_level قبلاً بالا چک شد

    if role == 'owner':
        return can_edit_project(actor, task.project)

    return False


def require_can_assign_task_role(actor, task, target_user, role):
    if not can_assign_task_role(actor, task, target_user, role):
        raise PermissionDenied(
            f"شما اجازه‌ی تخصیصِ نقشِ «{role}» را روی این تسک ندارید."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Revision approval
# ─────────────────────────────────────────────────────────────────────────────

def can_approve_revision(user, revision) -> bool:
    """
    آیا کاربر می‌تواند این revision را تایید (قفل) کند؟
      - superuser / company_admin همیشه مجاز
      - designated_approver که هنگام ساختِ نسخه تعیین شده است
    """
    if not user or not user.is_authenticated or not revision:
        return False
    if user.is_superuser or _role(user) == 'company_admin':
        return True
    designated_id = getattr(revision, 'designated_approver_id', None)
    return designated_id == user.id


def require_can_approve_revision(user, revision):
    if not can_approve_revision(user, revision):
        raise PermissionDenied(
            "تاییدِ این نسخه فقط توسطِ تاییدکنندهٔ تعیین‌شده مجاز است."
        )


# ─────────────────────────────────────────────────────────────────────────────
# DRF BasePermission classes — ایمن‌ترین مسیر برای endpointهای مدیریتی
# ─────────────────────────────────────────────────────────────────────────────

class IsCompanyLevel(BasePermission):
    """فقط کاربرانِ سطحِ شرکت (company_admin / company_pm / superuser)."""
    message = "این عملیات فقط برای کاربرانِ سطحِ شرکت مجاز است."

    def has_permission(self, request, view):
        return is_company_level(request.user)


class IsCompanyLevelOrReadOnly(BasePermission):
    """خواندن: هر کاربرِ احرازشده. نوشتن: فقط سطحِ شرکت."""
    message = "ویرایش این منبع فقط برای کاربرانِ سطحِ شرکت مجاز است."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return is_company_level(user)


class CanManageUsers(BasePermission):
    """
    مدیریتِ کاربران: سطحِ شرکت یا مدیرِ یک واحد (که حداقل یک واحد را مدیریت می‌کند).
    Scoping در سطحِ ردیف توسط ViewSet.get_queryset انجام می‌شود.
    """
    message = "شما اجازه‌ی مدیریتِ کاربران را ندارید."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if is_system_admin(user):
            return True
        # «مدیرِ واحد» واقعی = کسی که حداقل یک OrgUnit.manager او باشد
        return user.managed_units.exists()

    def has_object_permission(self, request, view, obj):
        user = request.user
        if is_system_admin(user):
            return True
        managed_ids = set(user.managed_units.values_list('id', flat=True))
        return obj.unit_id in managed_ids


class IsSystemAdminOrReadOnly(BasePermission):
    """خواندن: هر کاربرِ احرازشده. نوشتن: فقط مدیرِ سیستم (company_admin / superuser)."""
    message = "ویرایش این منبع فقط برای مدیرِ سیستم مجاز است."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return is_system_admin(user)
