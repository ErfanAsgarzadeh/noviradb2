from rest_framework.permissions import SAFE_METHODS, BasePermission


def org_role(user):
    return getattr(user, 'org_role', 'member') or 'member'


def is_engineering_releaser(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or org_role(user) in {'company_admin', 'company_pm'}


def can_manage_engineering(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or org_role(user) in {'company_admin', 'company_pm', 'unit_manager', 'project_manager'}


class CanManageEngineering(BasePermission):
    message = 'You do not have permission to manage engineering items.'

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return can_manage_engineering(request.user)



def can_view_part_master_config(user) -> bool:
    return bool(user and user.is_authenticated)


def can_manage_part_master_config(user) -> bool:
    return can_manage_engineering(user)


def can_activate_coding_template(user) -> bool:
    return is_engineering_releaser(user)


def can_enter_manual_controlled_code(user) -> bool:
    return is_engineering_releaser(user)


def can_override_duplicate_warning(user) -> bool:
    return is_engineering_releaser(user)


class CanActivateCodingTemplate(BasePermission):
    message = 'You do not have permission to activate coding templates.'

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return can_activate_coding_template(request.user)
