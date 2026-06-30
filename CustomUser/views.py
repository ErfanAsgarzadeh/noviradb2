from rest_framework import generics, status, viewsets
from rest_framework.decorators import api_view
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from .models import CustomUser, OrgUnit
from .serializers import (
    CustomUserSerializer,
    AdminUserManagementSerializer,
    OrgUnitSerializer,
)
# Permission classes (BasePermission) متمرکز در اپ ktcPlanning تعریف شده‌اند تا با
# helperهای موجود (is_company_level, ...) سازگار بمانند.
from ktcPlanning.permissions import (
    is_company_level,
    is_system_admin,
    IsSystemAdminOrReadOnly,
    CanManageUsers,
)


class RegisterView(generics.CreateAPIView):
    """
    ثبت‌نامِ عمومی کاربر.

    نکتهٔ امنیتی: نقش/واحد از این مسیر هرگز قابلِ تخصیص نیست. همهٔ کاربرانِ تازه
    با org_role='member' و unit=None ثبت می‌شوند. ارتقای نقش فقط از طریق
    UserManagementViewSet توسط ادمین/مدیرِ واحد ممکن است.
    """
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    permission_classes = [AllowAny]
    throttle_scope = 'register'

    def perform_create(self, serializer):
        # حتی اگر سریالایزر فیلدهای حساس را ignore کند، اینجا هم به‌صورتِ defense-in-depth
        # مقادیرِ ایمن را hard-set می‌کنیم.
        serializer.save(org_role='member', unit=None, is_active=True)


class UserProfileView(generics.RetrieveUpdateAPIView):
    """
    مشاهده/ویرایشِ پروفایلِ کاربر جاری.
    نقش/واحد در این endpoint غیرقابلِ ویرایش‌اند (read-only در سریالایزر).
    """
    serializer_class = CustomUserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class UserListView(generics.ListAPIView):
    """
    لیستِ کاربران برای انتخاب در UI (تخصیص به تسک، نقش، …).
    """
    queryset = CustomUser.objects.all().order_by('id')
    serializer_class = CustomUserSerializer
    permission_classes = [IsAuthenticated]


class UsersInMyUnitView(generics.ListAPIView):
    """
    لیست افراد قابل انتخاب توسط کاربر جاری به‌عنوان Approver:
    - superuser یا company_admin / company_pm → همه‌ی کاربران
    - unit_manager → فقط اعضای واحد خودش (شامل خودش)
    - member → فقط هم‌واحدی‌های خودش (یا اگر واحدی ندارد، فقط خودش)
    """
    serializer_class = CustomUserSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        u = self.request.user
        role = getattr(u, 'org_role', 'member') or 'member'

        if u.is_superuser or role in ('company_admin', 'company_pm'):
            return CustomUser.objects.all().order_by('id')

        unit_id = getattr(u, 'unit_id', None)
        if not unit_id:
            return CustomUser.objects.filter(pk=u.pk)

        return CustomUser.objects.filter(unit_id=unit_id).order_by('id')


class OrgUnitViewSet(viewsets.ModelViewSet):
    """
    مدیریتِ واحدهای سازمانی.
    خواندن: هر کاربرِ احرازشده. ساخت/ویرایش/حذف: فقط سطحِ شرکت (company_admin / company_pm / superuser).
    """
    queryset = OrgUnit.objects.all().order_by('name')
    serializer_class = OrgUnitSerializer
    permission_classes = [IsAuthenticated, IsSystemAdminOrReadOnly]


class UserManagementViewSet(viewsets.ModelViewSet):
    """
    مدیریتِ کاربران: تنظیمِ واحد و نقشِ سازمانی.

    دسترسی:
      - سطحِ شرکت (company_admin / company_pm / superuser): دسترسیِ کامل به همهٔ کاربران.
      - مدیرِ واحد (OrgUnit.manager == self): فقط اعضای واحدهای تحتِ مدیریت، با محدودیت
        روی نقش‌های قابلِ تخصیص (member / project_manager).
      - بقیه: ممنوع.
    """
    queryset = CustomUser.objects.all().order_by('id')
    serializer_class = AdminUserManagementSerializer
    permission_classes = [IsAuthenticated, CanManageUsers]

    def get_queryset(self):
        actor = self.request.user
        qs = CustomUser.objects.all().order_by('id')

        if not is_system_admin(actor):
            # محدود به اعضای واحدهای تحتِ مدیریتِ این کاربر
            managed_ids = list(actor.managed_units.values_list('id', flat=True))
            qs = qs.filter(unit_id__in=managed_ids)

        unit_id = self.request.query_params.get('unit_id')
        if unit_id == 'none':
            # «بدون واحد» فقط برای مدیرِ سیستم معنا دارد؛ مدیرانِ واحد چنین کاربرانی را نمی‌بینند.
            qs = qs.filter(unit__isnull=True) if is_system_admin(actor) else qs.none()
        elif unit_id:
            qs = qs.filter(unit_id=unit_id)
        return qs

    def perform_destroy(self, instance):
        # حفاظت در سطحِ شیء برای حذف
        actor = self.request.user
        if not is_system_admin(actor):
            managed_ids = set(actor.managed_units.values_list('id', flat=True))
            if instance.unit_id not in managed_ids:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("شما اجازه‌ی حذفِ این کاربر را ندارید.")
        instance.delete()


@api_view(['POST'])
def logout_view(request):
    try:
        refresh_token = request.data.get("refresh")

        if not refresh_token:
            return Response(
                {"detail": "Refresh token required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        token = RefreshToken(refresh_token)
        token.blacklist()

        return Response({"detail": "Logged out successfully"})

    except Exception:
        return Response({"detail": "Invalid token"}, status=status.HTTP_400_BAD_REQUEST)
