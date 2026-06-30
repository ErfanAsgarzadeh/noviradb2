"""
سریالایزرها برای کاربر و واحد سازمانی.

نکتهٔ امنیتی: فیلدهای حساسِ org_role/unit در سریالایزرِ پیش‌فرض «فقط خواندنی» هستند
تا از مسیر register/profile قابلِ ارتقای امتیاز نباشند. ویرایشِ این فیلدها از طریق
AdminUserManagementSerializer انجام می‌شود که محدودیت‌های privilege-aware دارد.
"""

from rest_framework import serializers
from .models import CustomUser, OrgUnit


# ─────────────────────────────────────────────────────────────────────────────
# OrgUnit
# ─────────────────────────────────────────────────────────────────────────────

class OrgUnitSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    managerId = serializers.PrimaryKeyRelatedField(
        source='manager', queryset=CustomUser.objects.all(),
        required=False, allow_null=True
    )
    managerName = serializers.CharField(source='manager.username', read_only=True, default=None)
    membersCount = serializers.SerializerMethodField()
    # علامتِ واحدِ برنامه‌ریزی (فقط یک واحد می‌تواند true باشد)
    isPlanningUnit = serializers.BooleanField(source='is_planning_unit', required=False)

    class Meta:
        model = OrgUnit
        fields = [
            'id', 'name', 'description',
            'managerId', 'managerName', 'membersCount',
            'isPlanningUnit',
        ]

    def get_membersCount(self, obj):
        return obj.members.count()


# ─────────────────────────────────────────────────────────────────────────────
# CustomUser — نسخهٔ پیش‌فرضِ ایمن (orgRole/unitId فقط خواندنی)
# مصرف‌کنندگان: RegisterView, UserProfileView, UserListView
# ─────────────────────────────────────────────────────────────────────────────

class CustomUserSerializer(serializers.ModelSerializer):
    """
    سریالایزرِ پیش‌فرضِ کاربر — تخصیصِ نقش/واحد از این مسیر ممکن نیست.
    برای ویرایشِ ادمینیِ این فیلدها از AdminUserManagementSerializer استفاده کنید.
    """
    id = serializers.CharField(read_only=True)

    jobTitle = serializers.CharField(source='job_title', required=False, allow_blank=True, allow_null=True)
    employeeCode = serializers.CharField(source='employee_code', required=False, allow_blank=True, allow_null=True)

    # ساختار سازمانی — قفل‌شده روی این سریالایزر؛ هیچ‌گاه از مسیر register/profile قابلِ تغییر نیست.
    unitId = serializers.PrimaryKeyRelatedField(source='unit', read_only=True)
    unitName = serializers.CharField(source='unit.name', read_only=True, default=None)
    orgRole = serializers.CharField(source='org_role', read_only=True)
    # صفحاتِ مجاز — فقط خواندنی اینجا (تا navbar کاربرِ جاری بتواند منو را فیلتر کند).
    allowedPages = serializers.JSONField(source='allowed_pages', read_only=True)

    class Meta:
        model = CustomUser
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'jobTitle', 'employeeCode', 'password',
            'unitId', 'unitName', 'orgRole', 'allowedPages'
        ]

        extra_kwargs = {
            'password': {'write_only': True, 'required': False}
        }

    def create(self, validated_data):
        # برای ساخت کاربر همیشه از create_user استفاده می‌شود تا پسورد به‌درستی هش شود.
        # ثبت‌نام عمومی (RegisterView) نقش/واحد را به این مسیر نمی‌دهد؛ پس مقدارِ پیش‌فرضِ مدل
        # (org_role='member', unit=None) اعمال می‌شود.
        return CustomUser.objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            instance.set_password(password)

        instance.save()
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# CustomUser — نسخهٔ ادمین (orgRole/unitId قابلِ ویرایش با کنترلِ امتیاز)
# مصرف‌کننده: UserManagementViewSet
# ─────────────────────────────────────────────────────────────────────────────

class AdminUserManagementSerializer(serializers.ModelSerializer):
    """
    سریالایزرِ ادمینی برای مدیریت کاربران.

    محدودیت‌های privilege-aware (در validate انجام می‌شود):
    - فقط کاربرانِ سطحِ شرکت (company_admin/company_pm/superuser) می‌توانند نقش‌های
      'company_admin', 'company_pm', 'unit_manager' را تخصیص دهند.
    - مدیرانِ واحد (آن‌هایی که OrgUnit.manager == self هستند) فقط می‌توانند:
        • نقش‌های 'member' یا 'project_manager' را تخصیص دهند.
        • کاربر را به یکی از واحدهای تحتِ مدیریتِ خودشان منتسب کنند.
    """
    id = serializers.CharField(read_only=True)

    jobTitle = serializers.CharField(source='job_title', required=False, allow_blank=True, allow_null=True)
    employeeCode = serializers.CharField(source='employee_code', required=False, allow_blank=True, allow_null=True)

    unitId = serializers.PrimaryKeyRelatedField(
        source='unit', queryset=OrgUnit.objects.all(),
        required=False, allow_null=True
    )
    unitName = serializers.CharField(source='unit.name', read_only=True, default=None)
    orgRole = serializers.CharField(source='org_role', required=False)
    # صفحاتِ مجاز — قابلِ ویرایش توسطِ ادمین. null = دسترسی به همه.
    allowedPages = serializers.JSONField(source='allowed_pages', required=False, allow_null=True)

    class Meta:
        model = CustomUser
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'jobTitle', 'employeeCode', 'password',
            'unitId', 'unitName', 'orgRole', 'allowedPages'
        ]
        extra_kwargs = {
            'password': {'write_only': True, 'required': False}
        }

    # ---- privilege-aware validation ----

    # نقش‌هایی که یک «مدیرِ واحد» مجاز است تخصیص دهد
    UNIT_MANAGER_ASSIGNABLE_ROLES = {'member', 'project_manager'}

    def _actor(self):
        request = self.context.get('request')
        return getattr(request, 'user', None) if request else None

    def _is_company_level(self, user) -> bool:
        # فقط مدیرِ سیستم (company_admin/superuser) اجازهٔ تخصیصِ آزادانهٔ نقش/واحد دارد.
        # company_pm عمداً مستثناست (او کاربران را مدیریت نمی‌کند).
        if not user or not user.is_authenticated:
            return False
        return user.is_superuser or getattr(user, 'org_role', None) == 'company_admin'

    def _managed_unit_ids(self, user):
        if not user or not user.is_authenticated:
            return []
        return list(user.managed_units.values_list('id', flat=True))

    def validate_orgRole(self, value):
        actor = self._actor()
        if self._is_company_level(actor):
            return value
        if value not in self.UNIT_MANAGER_ASSIGNABLE_ROLES:
            raise serializers.ValidationError(
                "شما اجازه‌ی تخصیصِ این نقش را ندارید. فقط نقش‌های 'member' یا 'project_manager' مجاز است."
            )
        return value

    def validate_unitId(self, value):
        actor = self._actor()
        if value is None or self._is_company_level(actor):
            return value
        managed_ids = self._managed_unit_ids(actor)
        if value.id not in managed_ids:
            raise serializers.ValidationError(
                "شما اجازه‌ی انتسابِ کاربر به این واحد را ندارید."
            )
        return value

    def validate_allowedPages(self, value):
        # null = دسترسی به همه؛ در غیر این صورت باید لیستی از مسیرها (رشته) باشد.
        if value is None:
            return value
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise serializers.ValidationError("صفحاتِ مجاز باید null یا لیستی از مسیرها باشد.")
        return value

    def create(self, validated_data):
        return CustomUser.objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance
