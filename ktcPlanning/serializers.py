# ktcPlanning/serializers.py
from rest_framework import serializers
from django.db.models import Sum

from .models import *


# =========================================================
# CALENDAR SERIALIZERS (تعریف تقویم مستقل + ساعات کاری + تعطیلات)
# =========================================================

class WorkingIntervalSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkingInterval
        fields = ['id', 'weekday', 'start_time', 'end_time']


class CalendarExceptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CalendarException
        fields = ['id', 'date', 'is_working', 'description']


class CalendarSerializer(serializers.ModelSerializer):
    intervals = WorkingIntervalSerializer(many=True, required=False)
    exceptions = CalendarExceptionSerializer(many=True, required=False)

    class Meta:
        model = Calendar
        fields = ['id', 'name', 'is_default', 'project', 'intervals', 'exceptions']
        extra_kwargs = {'project': {'required': False, 'allow_null': True}}

    def create(self, validated_data):
        intervals_data = validated_data.pop('intervals', [])
        exceptions_data = validated_data.pop('exceptions', [])
        calendar = Calendar.objects.create(**validated_data)
        for iv in intervals_data:
            WorkingInterval.objects.create(calendar=calendar, **iv)
        for ex in exceptions_data:
            CalendarException.objects.create(calendar=calendar, **ex)
        return calendar

    def update(self, instance, validated_data):
        intervals_data = validated_data.pop('intervals', None)
        exceptions_data = validated_data.pop('exceptions', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # جایگزینی کامل ساعات کاری در صورت ارسال
        if intervals_data is not None:
            instance.intervals.all().delete()
            for iv in intervals_data:
                WorkingInterval.objects.create(calendar=instance, **iv)

        # جایگزینی کامل تعطیلات در صورت ارسال
        if exceptions_data is not None:
            instance.exceptions.all().delete()
            for ex in exceptions_data:
                CalendarException.objects.create(calendar=instance, **ex)

        return instance


class ProjectSerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source='created_at', format="%Y-%m-%dT%H:%M:%S", read_only=True)
    description = serializers.SerializerMethodField()
    start_date = serializers.DateTimeField( format="%Y-%m-%d", required=False, allow_null=True)
    end_date = serializers.DateTimeField( format="%Y-%m-%d", required=False, allow_null=True)
    # الصاق/خواندن تقویم پروژه
    calendarId = serializers.PrimaryKeyRelatedField(
        source='calendar', queryset=Calendar.objects.all(),
        required=False, allow_null=True
    )
    calendarName = serializers.CharField(source='calendar.name', read_only=True, default=None)

    class Meta:
        model = Project
        fields = ['id', 'name', 'description', 'createdAt', 'start_date', 'end_date', 'calendarId', 'calendarName', 'scope']

    def get_description(self, obj):
        return ""


class ProjectViewerSerializer(serializers.ModelSerializer):
    projectId = serializers.PrimaryKeyRelatedField(source='project', queryset=Project.objects.all())
    userId = serializers.PrimaryKeyRelatedField(source='user', queryset=User.objects.all())
    userName = serializers.CharField(source='user.username', read_only=True)
    addedById = serializers.PrimaryKeyRelatedField(source='added_by', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True, format="%Y-%m-%dT%H:%M:%S")

    class Meta:
        model = ProjectViewer
        fields = ['id', 'projectId', 'userId', 'userName', 'addedById', 'createdAt']


class RevisionSerializer(serializers.ModelSerializer):
    projectId = serializers.PrimaryKeyRelatedField(source='project', read_only=True)
    projectStart = serializers.DateTimeField(source='project_start', format="%Y-%m-%d")
    projectEnd = serializers.DateTimeField(source='project_end', format="%Y-%m-%d")
    createdAt = serializers.DateTimeField(source='created_at', format="%Y-%m-%dT%H:%M:%S")
    approvedAt = serializers.DateTimeField(source='approved_at', format="%Y-%m-%dT%H:%M:%S")
    isBaseline = serializers.BooleanField(source='is_baseline')
    # تاییدکننده‌ی تعیین‌شده — User از models.py در namespace هست (User = get_user_model())
    designatedApproverId = serializers.PrimaryKeyRelatedField(
        source='designated_approver', queryset=User.objects.all(),
        required=False, allow_null=True
    )
    designatedApproverName = serializers.CharField(source='designated_approver.username', read_only=True, default=None)

    class Meta:
        model = Revision
        fields = ['id', 'projectId', 'number', 'description', 'projectStart','projectEnd',
                  'createdAt','approvedAt', 'isBaseline',
                  'designatedApproverId', 'designatedApproverName']


class WbsNodeSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source='node.id', read_only=True)
    versionId = serializers.IntegerField(source='id', read_only=True)
    code = serializers.CharField(source='wbs_code', read_only=True)
    name = serializers.CharField(source='title')
    parentId = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    isExpanded = serializers.SerializerMethodField()

    startDate = serializers.DateTimeField(source='planned_start', format="%Y-%m-%d", allow_null=True)
    endDate = serializers.DateTimeField(source='planned_finish', format="%Y-%m-%d", allow_null=True)
    duration = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    sequence = serializers.IntegerField(required=False)

    class Meta:
        model = WBSNodeVersion
        fields = ['id', 'versionId', 'code', 'name', 'parentId', 'type', 'isExpanded',
                  'startDate', 'endDate', 'duration', 'progress', 'sequence']

    def get_parentId(self, obj):
        return obj.parent.node.id if obj.parent else None
    def get_type(self, obj):
        return 'wbs'

    def get_isExpanded(self, obj):
        return True

    def get_startDate(self, obj):
        return None

    def get_endDate(self, obj):
        return None

    def get_duration(self, obj):
        return 0

    def get_progress(self, obj):
        return 0

class TaskScheduleMetricsSerializer(serializers.ModelSerializer):
    earlyStart = serializers.DateTimeField(source='early_start', format="%Y-%m-%d %H:%M:%S")
    earlyFinish = serializers.DateTimeField(source='early_finish', format="%Y-%m-%d %H:%M:%S")
    lateStart = serializers.DateTimeField(source='late_start', format="%Y-%m-%d %H:%M:%S")
    lateFinish = serializers.DateTimeField(source='late_finish', format="%Y-%m-%d %H:%M:%S")
    totalFloatHours = serializers.IntegerField(source='total_float_hours')
    freeFloatHours = serializers.IntegerField(source='free_float_hours')
    isCritical = serializers.BooleanField(source='is_critical')

    class Meta:
        model = TaskScheduleMetrics
        fields = [
            'earlyStart', 'earlyFinish', 'lateStart', 'lateFinish',
            'totalFloatHours', 'freeFloatHours', 'isCritical'
        ]

class ActivityNodeSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source='task.id', read_only=True)

    code = serializers.SerializerMethodField()
    name = serializers.CharField(source='title')
    parentId = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()

    startDate = serializers.DateTimeField(source='planned_start', format="%Y-%m-%d %H:%M:%S", allow_null=True)
    endDate = serializers.DateTimeField(source='planned_finish', format="%Y-%m-%d %H:%M:%S", allow_null=True)

    # تغییر مهم: فیلد duration حالا می‌تواند از فرانت‌اند دریافت شود
    duration = serializers.FloatField(required=False, write_only=True)

    progress = serializers.FloatField(required=False)
    sequence = serializers.IntegerField(required=False)
    
    # فیلدهای Actual (شروع/پایان واقعی) - write_only چون در to_representation جداگانه هندل می‌شوند
    actual_start = serializers.DateTimeField(required=False, write_only=True, allow_null=True)
    actual_finish = serializers.DateTimeField(required=False, write_only=True, allow_null=True)
    
    resources = serializers.SerializerMethodField()
    constraintType = serializers.SerializerMethodField()
    constraintDate = serializers.SerializerMethodField()
    notes = serializers.SerializerMethodField()
    metrics = TaskScheduleMetricsSerializer(read_only=True, allow_null=True)
    description=serializers.CharField(required=False)
    weight = serializers.FloatField(required=False)
    class Meta:
        model = TaskVersion
        fields = [
            'id', 'code', 'name', 'parentId', 'type', 'startDate', 'endDate',
            'duration', 'progress', 'sequence', 'actual_start', 'actual_finish',
            'resources', 'constraintType', 'constraintDate', 'notes','metrics','description','weight'
        ]

    def get_parentId(self, obj):
        # بررسی می‌کنیم که تسک به کدام ورژن WBS وصل است،
        # سپس UUID گره اصلی آن WBS را به فرانت‌اند می‌فرستیم
        return obj.wbs_node.node.id if obj.wbs_node else None
    # --- تبدیل دیتا هنگام ارسال به فرانت‌اند (ساعت به روز) ---


    def to_representation(self, instance):
        data = super().to_representation(instance)

        actual = getattr(instance, 'actual', None)
        data['progress'] = float(actual.progress) if actual else 0

        # اطلاعات واقعی برای نمایش در فرانت‌اند
        data['actual'] = {
            'actualStart': actual.actual_start.strftime("%Y-%m-%dT%H:%M") if (actual and actual.actual_start) else '',
            'actualFinish': actual.actual_finish.strftime("%Y-%m-%dT%H:%M") if (actual and actual.actual_finish) else '',
            'progress': float(actual.progress) if actual else 0,
        }

        data['duration'] = float(instance.duration_hours) if instance.duration_hours else 0
        return data
    # --- تبدیل دیتا هنگام دریافت از فرانت‌اند (روز به ساعت) ---
    def validate(self, attrs):
        if 'duration' in attrs:
            attrs['duration_hours'] = attrs.pop('duration')
        elif not self.instance:  # اگر ساخت تسک جدید بود و مقداری نیامد
            attrs['duration_hours'] = 40.0  # دیفالت 5 روز
        return attrs

    def get_type(self, obj):
        return 'activity'

    def get_code(self, obj):
        return f"ACT-{str(obj.task.id)[:4].upper()}"

    def get_progress(self, obj):
        if hasattr(obj, 'actual') and obj.actual:
            return float(obj.actual.progress)
        return 0

    def get_resources(self, obj):
        assignments = Assignment.objects.filter(task=obj.task, revision=obj.revision)
        # به جای برگرداندن یک آبجکت دارای نام و آیدی، فقط نام منابع را به صورت متن ساده برمی‌گردانیم
        # خروجی به این شکل می‌شود: ['Ali', 'Crane', 'Excavator']
        return [assign.resource.name for assign in assignments]

    def get_constraintType(self, obj):
        return "ASAP"

    def get_constraintDate(self, obj):
        return None

    def get_notes(self, obj):
        return ""

    def update(self, instance, validated_data):
        progress = validated_data.pop('progress', None)
        actual_start = validated_data.pop('actual_start', None)
        actual_finish = validated_data.pop('actual_finish', None)

        instance = super().update(instance, validated_data)

        # اگر هر یک از فیلدهای actual ارسال شده باشد، TaskActual را آپدیت کن
        if progress is not None or actual_start is not None or actual_finish is not None:
            actual, _ = TaskActual.objects.get_or_create(
                task_version=instance,
                defaults={
                    'updated_by': self.context['request'].user
                }
            )
            if progress is not None:
                actual.progress = progress
            if actual_start is not None:
                actual.actual_start = actual_start
            if actual_finish is not None:
                actual.actual_finish = actual_finish
            actual.updated_by = self.context['request'].user
            actual.save()

        return instance


class DependencySerializer(serializers.ModelSerializer):
    fromId = serializers.UUIDField(source='predecessor_id')
    toId = serializers.UUIDField(source='successor_id')
    type = serializers.CharField(source='dependency_type')
    lag = serializers.SerializerMethodField()

    class Meta:
        model = Dependency
        fields = ['id', 'fromId', 'toId', 'type', 'lag']

    def create(self, validated_data):
        predecessor_id = validated_data.pop('predecessor_id')
        successor_id = validated_data.pop('successor_id')

        validated_data['predecessor_id'] = predecessor_id
        validated_data['successor_id'] = successor_id

        return super().create(validated_data)

    def get_lag(self, obj):
        return obj.lag_hours / 8 if obj.lag_hours else 0


class TaskRoleSerializer(serializers.ModelSerializer):
    revisionId = serializers.PrimaryKeyRelatedField(source='revision', queryset=Revision.objects.all())
    taskId = serializers.PrimaryKeyRelatedField(source='task', queryset=Task.objects.all())
    userId = serializers.PrimaryKeyRelatedField(source='user', queryset=User.objects.all())

    class Meta:
        model = TaskRole
        fields = ['id', 'revisionId', 'taskId', 'userId', 'role']


# ==========================================
# My Tasks: Reporting & Chat Serializers
# ==========================================

class TaskReportAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = TaskReportAttachment
        fields = ['id', 'file', 'file_url', 'file_name', 'file_type', 'file_size', 'uploaded_at']
        read_only_fields = fields

    def get_file_url(self, obj):
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        return None


class TaskReportLogSerializer(serializers.ModelSerializer):
    attachments = TaskReportAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = TaskReportLog
        fields = [
            'id',
            'task',
            'user',
            'status',
            'progress_percent',
            'time_spent_hours',
            'notes',
            'blockers',
            'timestamp',
            # فیلدهای state machine (دو‌مرحله‌ای)
            'approval_status',
            'reviewer_approved_by',
            'reviewer_approved_at',
            'final_approved_by',
            'final_approved_at',
            # legacy (سازگاری تا فرانت‌اند به‌روزرسانی شود)
            'is_approved',
            'approved_by',
            'approved_at',
            'attachments',
        ]
        read_only_fields = [
            'id',
            'timestamp',
            'user',
            'approval_status',
            'reviewer_approved_by',
            'reviewer_approved_at',
            'final_approved_by',
            'final_approved_at',
            'is_approved',
            'approved_by',
            'approved_at',
            'attachments',
        ]

class TaskChatMessageSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = TaskChatMessage
        fields = [
            'id',
            'task',
            'user',
            'text',
            'file',
            'file_name',
            'file_type',
            'file_url',
            'timestamp'
        ]
        read_only_fields = [
            'id',
            'timestamp',
            'user',
            'file_name',
            'file_type',
            'file_url',
        ]

    def get_file_url(self, obj):
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        return None

    def validate_file(self, value):
        if value:
            from ktcPlanning.validators import ChatFileValidator
            ChatFileValidator()(value)
        return value

    def create(self, validated_data):
        file = validated_data.get('file')
        if file:
            validated_data['file_name'] = file.name
            validated_data['file_type'] = file.content_type or ''
        return super().create(validated_data)


class ResourcePoolSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourcePool
        fields = '__all__'

class ResourceRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceRole
        fields = '__all__'

class ResourceSkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceSkill
        fields = '__all__'

class ResourceSerializer(serializers.ModelSerializer):
    # برای اینکه اسم فیلدها در فرانت‌اند راحت‌تر مپ شود
    resourceType = serializers.CharField(source='resource_type', required=False)
    poolId = serializers.PrimaryKeyRelatedField(source='pool', queryset=ResourcePool.objects.all(), required=False, allow_null=True)
    roleId = serializers.PrimaryKeyRelatedField(source='role', queryset=ResourceRole.objects.all(), required=False, allow_null=True)
    maxUnits = serializers.DecimalField(source='max_units', max_digits=10, decimal_places=2, required=False)
    isActive = serializers.BooleanField(source='is_active', required=False)

    class Meta:
        model = Resource
        fields = [
            'id', 'code', 'name', 'resource_type', 'pool', 'role',
            'max_units', 'priority', 'is_active',
            # فیلدهای هم‌نام برای فرانت‌اند:
            'resourceType', 'poolId', 'roleId', 'maxUnits', 'isActive'
        ]

class ResourceSkillMappingSerializer(serializers.ModelSerializer):
    resourceId = serializers.PrimaryKeyRelatedField(source='resource', queryset=Resource.objects.all())
    skillId = serializers.PrimaryKeyRelatedField(source='skill', queryset=ResourceSkill.objects.all())

    class Meta:
        model = ResourceSkillMapping
        fields = ['id', 'resource', 'skill', 'level', 'resourceId', 'skillId']

class ResourceExceptionSerializer(serializers.ModelSerializer):
    resourceId = serializers.PrimaryKeyRelatedField(source='resource', queryset=Resource.objects.all())
    startDatetime = serializers.DateTimeField(source='start_datetime')
    finishDatetime = serializers.DateTimeField(source='finish_datetime')
    isAvailable = serializers.BooleanField(source='is_available')

    class Meta:
        model = ResourceException
        fields = [
            'id', 'resource', 'start_datetime', 'finish_datetime', 'reason', 'is_available',
            'resourceId', 'startDatetime', 'finishDatetime', 'isAvailable'
        ]

class ResourceRateSerializer(serializers.ModelSerializer):
    resourceId = serializers.PrimaryKeyRelatedField(
        source='resource',
        queryset=Resource.objects.all()
    )
    effectiveFrom = serializers.DateField(source='effective_from')
    regularRate = serializers.DecimalField(
        source='regular_rate',
        max_digits=10,
        decimal_places=2
    )
    overtimeRate = serializers.DecimalField(
        source='overtime_rate',
        max_digits=10,
        decimal_places=2
    )

    class Meta:
        model = ResourceRate
        fields = [
            'id',
            'resource',
            'effective_from',
            'regular_rate',
            'overtime_rate',
            'resourceId',
            'effectiveFrom',
            'regularRate',
            'overtimeRate',
        ]

        read_only_fields = [
            'resource',
            'effective_from',
            'regular_rate',
            'overtime_rate',
        ]


class AssignmentSerializer(serializers.ModelSerializer):
    taskId = serializers.PrimaryKeyRelatedField(source='task', queryset=Task.objects.all())
    resourceId = serializers.PrimaryKeyRelatedField(source='resource', queryset=Resource.objects.all())
    revisionId = serializers.PrimaryKeyRelatedField(source='revision', queryset=Revision.objects.all())

    unitsPercent = serializers.DecimalField(source='units_percent', max_digits=5, decimal_places=2)
    plannedHours = serializers.DecimalField(source='planned_hours', max_digits=10, decimal_places=2, required=False,
                                            default=0)
    actualHours = serializers.DecimalField(source='actual_hours', max_digits=10, decimal_places=2, required=False,
                                           default=0)

    # ── فیلدهای نمایشی جدید ──────────────────────────────────────────────────
    resource_name = serializers.CharField(source='resource.name', read_only=True)
    resource_type = serializers.CharField(source='resource.resource_type', read_only=True)

    class Meta:
        model = Assignment
        fields = [
            'id',
            'taskId',
            'resourceId',
            'revisionId',
            'unitsPercent',
            'plannedHours',
            'actualHours',
            # نمایشی
            'resource_name',
            'resource_type',
        ]


class VarianceReportSerializer(serializers.ModelSerializer):
    task_name = serializers.SerializerMethodField()
    task_code = serializers.SerializerMethodField()

    class Meta:
        model = VarianceReport
        fields = '__all__'

    def get_task_name(self, obj):
        # پیدا کردن عنوان تسک در همان ریویژنی که گزارش برای آن ثبت شده
        tv = obj.task.versions.filter(revision=obj.revision).first()
        return tv.title if tv else "تسک نامشخص"

    def get_task_code(self, obj):
        # استخراج کد WBS برای این تسک
        tv = obj.task.versions.filter(revision=obj.revision).first()
        return tv.wbs_node.wbs_code if (tv and hasattr(tv, 'wbs_node')) else "N/A"



# ─── SystemSettings Serializer ────────────────────────────────────────────────

class SystemSettingsSerializer(serializers.ModelSerializer):
    allowPlanningManagerBypassReviewer = serializers.BooleanField(
        source='allow_planning_manager_bypass_reviewer'
    )

    class Meta:
        model = SystemSettings
        fields = ['allowPlanningManagerBypassReviewer']


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnitOfMeasure
        fields = ['id', 'code', 'name']

class ExpenseTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseType
        fields = ['id', 'name', 'description', 'is_active', 'unit']

# ─── جایگزین کن این بلاک را در serializers.py ───────────────────────────────

class FundingSourceSerializer(serializers.ModelSerializer):
    allocated_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    unallocated_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = FundingSource
        fields = [
            'id', 'title', 'source_type', 'source_party', 'reference_no',
            'received_date', 'total_amount', 'currency', 'description',
            'allocated_amount', 'unallocated_amount', 'created_by', 'created_at',
        ]
        read_only_fields = ['allocated_amount', 'unallocated_amount', 'created_by', 'created_at']

    def validate_total_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('Funding amount must be greater than zero.')
        return value


class BudgetAllocationSerializer(serializers.ModelSerializer):
    actual_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    remaining_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    funding_source_title = serializers.CharField(source='funding_source.title', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    task_title = serializers.SerializerMethodField()
    wbs_title = serializers.CharField(source='wbs_node.title', read_only=True, default=None)
    org_unit_name = serializers.CharField(source='org_unit.name', read_only=True, default=None)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = BudgetAllocation
        fields = [
            'id', 'funding_source', 'funding_source_title',
            'project', 'project_name', 'revision',
            'scope_type', 'wbs_node', 'wbs_title', 'task', 'task_title',
            'org_unit', 'org_unit_name', 'cost_type', 'allocated_amount',
            'actual_amount', 'remaining_amount', 'description',
            'created_by', 'created_at',
        ]
        read_only_fields = ['actual_amount', 'remaining_amount', 'created_by', 'created_at']
        extra_kwargs = {'project': {'required': False, 'allow_null': True}}

    def get_task_title(self, obj):
        if not obj.task:
            return None
        tv = obj.task.versions.filter(revision=obj.revision, is_deleted=False).first()
        if not tv:
            tv = obj.task.versions.filter(is_deleted=False).last()
        return tv.title if tv else str(obj.task.id)

    def validate(self, attrs):
        instance = self.instance

        def value(name):
            if name in attrs:
                return attrs[name]
            return getattr(instance, name, None) if instance else None

        funding_source = value('funding_source')
        project = value('project')
        revision = value('revision')
        scope_type = value('scope_type')
        wbs_node = value('wbs_node')
        task = value('task')
        org_unit = value('org_unit')
        allocated_amount = value('allocated_amount')

        if allocated_amount is not None and allocated_amount <= 0:
            raise serializers.ValidationError({'allocated_amount': 'Allocated amount must be greater than zero.'})
        if scope_type in {'PROJECT', 'WBS', 'TASK'} and not project:
            raise serializers.ValidationError({'project': 'Project is required for project, WBS, and task allocations.'})
        if revision and project and revision.project_id != project.id:
            raise serializers.ValidationError({'revision': 'Revision must belong to the selected project.'})
        if task and project and task.project_id != project.id:
            raise serializers.ValidationError({'task': 'Task must belong to the selected project.'})
        if wbs_node and project and wbs_node.revision.project_id != project.id:
            raise serializers.ValidationError({'wbs_node': 'WBS node must belong to the selected project.'})

        if scope_type == 'TASK' and not task:
            raise serializers.ValidationError({'task': 'Task allocation requires a task.'})
        if scope_type in {'WBS', 'TASK'} and not wbs_node:
            raise serializers.ValidationError({'wbs_node': 'WBS allocation requires a WBS node.'})
        if scope_type == 'ORG_UNIT' and not org_unit:
            raise serializers.ValidationError({'org_unit': 'Org unit allocation requires an org unit.'})
        if scope_type != 'TASK' and task:
            raise serializers.ValidationError({'task': 'Task can only be set for TASK allocations.'})
        if scope_type not in {'WBS', 'TASK'} and wbs_node:
            raise serializers.ValidationError({'wbs_node': 'WBS node can only be set for WBS or TASK allocations.'})
        if scope_type != 'ORG_UNIT' and org_unit:
            raise serializers.ValidationError({'org_unit': 'Org unit can only be set for ORG_UNIT allocations.'})

        if funding_source and allocated_amount is not None:
            existing = BudgetAllocation.objects.filter(funding_source=funding_source)
            if instance:
                existing = existing.exclude(pk=instance.pk)
            current_total = existing.aggregate(total=Sum('allocated_amount'))['total'] or 0
            if current_total + allocated_amount > funding_source.total_amount:
                raise serializers.ValidationError({'allocated_amount': 'Allocations cannot exceed funding source amount.'})

        return attrs


class BudgetConsumptionSerializer(serializers.ModelSerializer):
    budget_allocation_label = serializers.SerializerMethodField()
    funding_source_title = serializers.CharField(source='budget_allocation.funding_source.title', read_only=True)

    class Meta:
        model = BudgetConsumption
        fields = [
            'id',
            'budget_allocation',
            'budget_allocation_label',
            'funding_source_title',
            'amount',
            'created_at',
        ]
        read_only_fields = fields

    def get_budget_allocation_label(self, obj):
        allocation = obj.budget_allocation
        target = allocation.project_name if hasattr(allocation, 'project_name') else None
        if allocation.task_id:
            target = f"Task {allocation.task_id}"
        elif allocation.wbs_node_id:
            target = allocation.wbs_node.title
        elif allocation.project_id:
            target = allocation.project.name
        elif allocation.org_unit_id:
            target = allocation.org_unit.name
        else:
            target = "Company"
        return f"{allocation.funding_source.title} / {target} / {allocation.cost_type}"


class CostTransactionSerializer(serializers.ModelSerializer):
    # فیلدهای read-only که بکند محاسبه می‌کند
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    # نمایش نام منبع انتخابی برای خواندن آسان در فرانت
    resource_name = serializers.CharField(source='resource.name', read_only=True, default=None)
    expense_type_name = serializers.CharField(source='expense_type.name', read_only=True, default=None)
    task_title = serializers.SerializerMethodField()
    budget_consumptions = BudgetConsumptionSerializer(many=True, read_only=True)

    class Meta:
        model = CostTransaction
        fields = [
            'id',
            'project',
            'revision',
            'task',
            'assignment',
            'resource_rate',   # FK — برای non-EXPENSE اجباری
            'resource',        # FK — اختیاری (خوانده می‌شود از resource_rate.resource در clean)
            'expense_type',    # FK — برای EXPENSE اجباری
            'budget_allocation',
            'transaction_type',
            'transaction_date',
            'quantity',
            'unit_rate',       # برای non-EXPENSE
            'expense_rate',    # برای EXPENSE
            'amount',          # read-only، محاسبه‌شده در save()
            'description',
            'created_by',
            'created_at',
            # فیلدهای کمکی نمایشی (read-only)
            'resource_name',
            'expense_type_name',
            'task_title',
            'budget_consumptions',
        ]
        read_only_fields = ['amount', 'created_by', 'created_at', 'budget_consumptions']

    def get_task_title(self, obj):
        if not obj.task:
            return None
        tv = obj.task.versions.filter(is_deleted=False).last()
        return tv.title if tv else str(obj.task.id)

    def validate(self, attrs):
        instance = self.instance

        def value(name):
            if name in attrs:
                return attrs[name]
            return getattr(instance, name, None) if instance else None

        transaction_type = value('transaction_type')
        quantity = value('quantity')
        task = value('task')
        revision = value('revision')
        assignment = value('assignment')
        resource_rate = value('resource_rate')
        budget_allocation = value('budget_allocation')
        expense_type = value('expense_type')
        expense_rate = value('expense_rate')
        unit_rate = value('unit_rate')

        if quantity is not None and quantity <= 0:
            raise serializers.ValidationError({'quantity': 'Quantity must be greater than zero.'})
        if (
            budget_allocation
            and budget_allocation.project_id
            and value('project')
            and budget_allocation.project_id != value('project').id
        ):
            raise serializers.ValidationError({'budget_allocation': 'Budget allocation must belong to the selected project.'})

        if transaction_type == 'EXPENSE':
            if not expense_type:
                raise serializers.ValidationError({'expense_type': 'ExpenseType is required.'})
            if expense_rate is None:
                raise serializers.ValidationError({'expense_rate': 'Expense rate is required.'})
            if expense_rate < 0:
                raise serializers.ValidationError({'expense_rate': 'Expense rate cannot be negative.'})
            if resource_rate:
                raise serializers.ValidationError({'resource_rate': 'Expense cannot have ResourceRate.'})
            if assignment:
                raise serializers.ValidationError({'assignment': 'Expense cannot have Assignment.'})
            return attrs

        if not assignment:
            raise serializers.ValidationError({'assignment': 'Assignment is required.'})
        if transaction_type == 'COST':
            if unit_rate is None:
                raise serializers.ValidationError({'unit_rate': 'Unit rate is required for cost resources.'})
            if unit_rate < 0:
                raise serializers.ValidationError({'unit_rate': 'Unit rate cannot be negative.'})
        elif not resource_rate:
            raise serializers.ValidationError({'resource_rate': 'ResourceRate is required.'})
        if resource_rate and resource_rate.resource_id != assignment.resource_id:
            raise serializers.ValidationError({'resource_rate': 'ResourceRate must belong to Assignment resource.'})
        if task and assignment.task_id != task.id:
            raise serializers.ValidationError({'assignment': 'Assignment must belong to the selected task.'})
        if revision and assignment.revision_id != revision.id:
            raise serializers.ValidationError({'assignment': 'Assignment must belong to the selected revision.'})

        return attrs

class TaskDropdownSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    code = serializers.SerializerMethodField()
    wbsNodeId = serializers.SerializerMethodField()
    wbsVersionId = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = ['id', 'name', 'code', 'wbsNodeId', 'wbsVersionId']

    def get_name(self, obj):
        # گرفتن عنوان از نسخه فعال (ریویژنِ باز و قفل‌نشده)
        active_version = obj.versions.filter(revision__approved_at__isnull=True, is_deleted=False).first()
        if not active_version:
            # در غیر این صورت، آخرین نسخه موجود
            active_version = obj.versions.filter(is_deleted=False).last()
        return active_version.title if active_version else "تسک بدون عنوان"

    def get_code(self, obj):
        active_version = obj.versions.filter(revision__approved_at__isnull=True, is_deleted=False).first()
        if not active_version:
            active_version = obj.versions.filter(is_deleted=False).last()

        if active_version and active_version.wbs_node:
            return active_version.wbs_node.wbs_code,active_version.wbs_node.title
        return ""

    def get_wbsNodeId(self, obj):
        active_version = obj.versions.filter(revision__approved_at__isnull=True, is_deleted=False).first()
        if not active_version:
            active_version = obj.versions.filter(is_deleted=False).last()
        return str(active_version.wbs_node.node_id) if active_version and active_version.wbs_node else None

    def get_wbsVersionId(self, obj):
        active_version = obj.versions.filter(revision__approved_at__isnull=True, is_deleted=False).first()
        if not active_version:
            active_version = obj.versions.filter(is_deleted=False).last()
        return active_version.wbs_node_id if active_version and active_version.wbs_node else None
