# ktcPlanning/serializers.py
from rest_framework import serializers
from django.db.models import Sum
from decimal import Decimal

from .models import *
from .financial_services import get_task_financial_status, milestone_amount, milestone_paid_amount, milestone_outstanding


# =========================================================
# CALENDAR SERIALIZERS (طھط¹ط±غŒظپ طھظ‚ظˆغŒظ… ظ…ط³طھظ‚ظ„ + ط³ط§ط¹ط§طھ ع©ط§ط±غŒ + طھط¹ط·غŒظ„ط§طھ)
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

        # ط¬ط§غŒع¯ط²غŒظ†غŒ ع©ط§ظ…ظ„ ط³ط§ط¹ط§طھ ع©ط§ط±غŒ ط¯ط± طµظˆط±طھ ط§ط±ط³ط§ظ„
        if intervals_data is not None:
            instance.intervals.all().delete()
            for iv in intervals_data:
                WorkingInterval.objects.create(calendar=instance, **iv)

        # ط¬ط§غŒع¯ط²غŒظ†غŒ ع©ط§ظ…ظ„ طھط¹ط·غŒظ„ط§طھ ط¯ط± طµظˆط±طھ ط§ط±ط³ط§ظ„
        if exceptions_data is not None:
            instance.exceptions.all().delete()
            for ex in exceptions_data:
                CalendarException.objects.create(calendar=instance, **ex)

        return instance


class ProjectSerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source='created_at', format="%Y-%m-%dT%H:%M:%S", read_only=True)
    description = serializers.SerializerMethodField()
    start_date = serializers.DateTimeField(format="%Y-%m-%d", required=False, allow_null=True)
    end_date = serializers.DateTimeField(format="%Y-%m-%d", required=False, allow_null=True)
    calendarId = serializers.PrimaryKeyRelatedField(
        source='calendar', queryset=Calendar.objects.all(), required=False, allow_null=True
    )
    calendarName = serializers.CharField(source='calendar.name', read_only=True, default=None)
    parentProjectId = serializers.PrimaryKeyRelatedField(
        source='parent_project', queryset=Project.objects.filter(is_deleted=False),
        required=False, allow_null=True,
    )
    parentProjectName = serializers.CharField(source='parent_project.name', read_only=True, default=None)
    childProjectCount = serializers.SerializerMethodField()
    parentScheduleWarning = serializers.JSONField(source='parent_schedule_warning', read_only=True)
    parentScheduleWarningUpdatedAt = serializers.DateTimeField(source='parent_schedule_warning_updated_at', read_only=True)
    lifecycleStatus = serializers.ChoiceField(source='lifecycle_status', choices=Project.LIFECYCLE_CHOICES, required=False)
    currentDataDate = serializers.DateTimeField(source='current_data_date', required=False, allow_null=True)
    activeBaselineRevisionId = serializers.PrimaryKeyRelatedField(
        source='active_baseline_revision', queryset=Revision.objects.filter(is_deleted=False),
        required=False, allow_null=True,
    )
    currentExecutionRevisionId = serializers.PrimaryKeyRelatedField(
        source='current_execution_revision', queryset=Revision.objects.filter(is_deleted=False),
        required=False, allow_null=True,
    )
    currentForecastRevisionId = serializers.PrimaryKeyRelatedField(
        source='current_forecast_revision', queryset=Revision.objects.filter(is_deleted=False),
        required=False, allow_null=True,
    )
    workingRevisionId = serializers.PrimaryKeyRelatedField(
        source='working_revision', queryset=Revision.objects.filter(is_deleted=False),
        required=False, allow_null=True,
    )
    scheduleGovernance = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'description', 'createdAt', 'start_date', 'end_date',
            'calendarId', 'calendarName', 'scope', 'parentProjectId',
            'parentProjectName', 'childProjectCount', 'parentScheduleWarning',
            'parentScheduleWarningUpdatedAt', 'lifecycleStatus', 'currentDataDate',
            'activeBaselineRevisionId', 'currentExecutionRevisionId',
            'currentForecastRevisionId', 'workingRevisionId', 'scheduleGovernance',
        ]

    def get_description(self, obj):
        return ""

    def get_childProjectCount(self, obj):
        return obj.subprojects.filter(is_deleted=False).count()

    def get_scheduleGovernance(self, obj):
        issues = []
        if not obj.active_baseline_revision_id:
            issues.append('missing_active_baseline')
        if not obj.current_execution_revision_id:
            issues.append('missing_execution_revision')
        if not obj.current_forecast_revision_id:
            issues.append('missing_forecast_revision')
        if not obj.current_data_date:
            issues.append('missing_data_date')
        return {
            'ready': not issues,
            'issues': issues,
            'hasWorkingRevision': bool(obj.working_revision_id),
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance
        project_id = instance.pk if instance else None
        parent_project = attrs.get('parent_project', instance.parent_project if instance else None)

        if parent_project is not None:
            if instance and parent_project.pk == instance.pk:
                raise serializers.ValidationError({'parentProjectId': 'Project cannot be its own parent.'})
            ancestor = parent_project
            while ancestor is not None:
                if instance and ancestor.pk == instance.pk:
                    raise serializers.ValidationError({'parentProjectId': 'Subproject hierarchy cannot contain a cycle.'})
                ancestor = ancestor.parent_project

        revision_fields = {
            'active_baseline_revision': 'activeBaselineRevisionId',
            'current_execution_revision': 'currentExecutionRevisionId',
            'current_forecast_revision': 'currentForecastRevisionId',
            'working_revision': 'workingRevisionId',
        }
        for source, api_name in revision_fields.items():
            revision = attrs.get(source, getattr(instance, source, None) if instance else None)
            if revision and (project_id is None or revision.project_id != project_id or revision.is_deleted):
                raise serializers.ValidationError({api_name: 'Revision must belong to this project and be active.'})

        if 'active_baseline_revision' in attrs and attrs['active_baseline_revision']:
            baseline = attrs['active_baseline_revision']
            if not baseline.is_baseline or baseline.approved_at is None:
                raise serializers.ValidationError({'activeBaselineRevisionId': 'Official baseline must be approved and marked as baseline.'})
        for source, api_name in (
            ('current_execution_revision', 'currentExecutionRevisionId'),
            ('current_forecast_revision', 'currentForecastRevisionId'),
        ):
            if source in attrs and attrs[source] and attrs[source].approved_at is None:
                raise serializers.ValidationError({api_name: 'Official revision must be approved.'})
        if 'working_revision' in attrs and attrs['working_revision'] and attrs['working_revision'].approved_at is not None:
            raise serializers.ValidationError({'workingRevisionId': 'Working revision must be open.'})
        if instance and 'working_revision' in attrs:
            old = instance.working_revision
            new = attrs['working_revision']
            if old and new and old.pk != new.pk and not old.is_deleted and old.approved_at is None:
                raise serializers.ValidationError({'workingRevisionId': 'Close or clear the current working revision first.'})

        lifecycle = attrs.get('lifecycle_status', instance.lifecycle_status if instance else Project.LIFECYCLE_DRAFT)
        if lifecycle == Project.LIFECYCLE_ACTIVE:
            required = {
                'activeBaselineRevisionId': attrs.get('active_baseline_revision', getattr(instance, 'active_baseline_revision', None)),
                'currentExecutionRevisionId': attrs.get('current_execution_revision', getattr(instance, 'current_execution_revision', None)),
                'currentDataDate': attrs.get('current_data_date', getattr(instance, 'current_data_date', None)),
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise serializers.ValidationError({name: 'Required for an active project.' for name in missing})
        return attrs

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
    # طھط§غŒغŒط¯ع©ظ†ظ†ط¯ظ‡â€ŒغŒ طھط¹غŒغŒظ†â€Œط´ط¯ظ‡ â€” User ط§ط² models.py ط¯ط± namespace ظ‡ط³طھ (User = get_user_model())
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

    def validate(self, attrs):
        has_approver_update = 'designated_approver' in attrs
        approver = attrs.get('designated_approver')
        revision = self.instance
        project = revision.project if revision else attrs.get('project')
        if has_approver_update and project is not None:
            default_approver = project.get_default_approver()
            if default_approver and (approver is None or approver.id != default_approver.id):
                raise serializers.ValidationError({
                    'designatedApproverId': 'Project approver must be the manager of the project creator unit.'
                })
            if default_approver is None and approver is not None:
                raise serializers.ValidationError({
                    'designatedApproverId': 'The project creator unit has no manager.'
                })
        return attrs


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

    # طھط؛غŒغŒط± ظ…ظ‡ظ…: ظپغŒظ„ط¯ duration ط­ط§ظ„ط§ ظ…غŒâ€Œطھظˆط§ظ†ط¯ ط§ط² ظپط±ط§ظ†طھâ€Œط§ظ†ط¯ ط¯ط±غŒط§ظپطھ ط´ظˆط¯
    duration = serializers.FloatField(required=False, write_only=True)

    progress = serializers.FloatField(required=False)
    sequence = serializers.IntegerField(required=False)
    
    # ظپغŒظ„ط¯ظ‡ط§غŒ Actual (ط´ط±ظˆط¹/ظ¾ط§غŒط§ظ† ظˆط§ظ‚ط¹غŒ) - write_only ع†ظˆظ† ط¯ط± to_representation ط¬ط¯ط§ع¯ط§ظ†ظ‡ ظ‡ظ†ط¯ظ„ ظ…غŒâ€Œط´ظˆظ†ط¯
    actual_start = serializers.DateTimeField(required=False, write_only=True, allow_null=True)
    actual_finish = serializers.DateTimeField(required=False, write_only=True, allow_null=True)
    
    resources = serializers.SerializerMethodField()
    constraintType = serializers.SerializerMethodField()
    constraintDate = serializers.SerializerMethodField()
    notes = serializers.SerializerMethodField()
    metrics = TaskScheduleMetricsSerializer(read_only=True, allow_null=True)
    scheduleQuality = serializers.SerializerMethodField()
    description=serializers.CharField(required=False)
    weight = serializers.FloatField(required=False)
    class Meta:
        model = TaskVersion
        fields = [
            'id', 'code', 'name', 'parentId', 'type', 'startDate', 'endDate',
            'duration', 'progress', 'sequence', 'actual_start', 'actual_finish',
            'resources', 'constraintType', 'constraintDate', 'notes','metrics','scheduleQuality','description','weight'
        ]

    def get_parentId(self, obj):
        # ط¨ط±ط±ط³غŒ ظ…غŒâ€Œع©ظ†غŒظ… ع©ظ‡ طھط³ع© ط¨ظ‡ ع©ط¯ط§ظ… ظˆط±عکظ† WBS ظˆطµظ„ ط§ط³طھطŒ
        # ط³ظ¾ط³ UUID ع¯ط±ظ‡ ط§طµظ„غŒ ط¢ظ† WBS ط±ط§ ط¨ظ‡ ظپط±ط§ظ†طھâ€Œط§ظ†ط¯ ظ…غŒâ€Œظپط±ط³طھغŒظ…
        return obj.wbs_node.node.id if obj.wbs_node else None

    def get_scheduleQuality(self, obj):
        actual = getattr(obj, 'actual', None)
        progress = float(actual.progress or 0) if actual else 0
        is_completed = bool(actual and (actual.actual_finish is not None or progress >= 100))
        quality = getattr(obj, '_schedule_quality', None)
        if quality:
            return quality
        return {
            'isCompleted': is_completed,
            'isOpenEnd': False,
            'definesProjectFinish': False,
            'severity': 'done' if is_completed else 'ok',
            'message': 'Actualized task; not counted as remaining critical work.' if is_completed else '',
        }
    # --- طھط¨ط¯غŒظ„ ط¯غŒطھط§ ظ‡ظ†ع¯ط§ظ… ط§ط±ط³ط§ظ„ ط¨ظ‡ ظپط±ط§ظ†طھâ€Œط§ظ†ط¯ (ط³ط§ط¹طھ ط¨ظ‡ ط±ظˆط²) ---


    def to_representation(self, instance):
        data = super().to_representation(instance)

        actual = getattr(instance, 'actual', None)
        data['progress'] = float(actual.progress) if actual else 0

        # ط§ط·ظ„ط§ط¹ط§طھ ظˆط§ظ‚ط¹غŒ ط¨ط±ط§غŒ ظ†ظ…ط§غŒط´ ط¯ط± ظپط±ط§ظ†طھâ€Œط§ظ†ط¯
        data['actual'] = {
            'actualStart': actual.actual_start.strftime("%Y-%m-%dT%H:%M") if (actual and actual.actual_start) else '',
            'actualFinish': actual.actual_finish.strftime("%Y-%m-%dT%H:%M") if (actual and actual.actual_finish) else '',
            'progress': float(actual.progress) if actual else 0,
        }

        data['duration'] = float(instance.duration_hours) if instance.duration_hours else 0
        return data
    # --- طھط¨ط¯غŒظ„ ط¯غŒطھط§ ظ‡ظ†ع¯ط§ظ… ط¯ط±غŒط§ظپطھ ط§ط² ظپط±ط§ظ†طھâ€Œط§ظ†ط¯ (ط±ظˆط² ط¨ظ‡ ط³ط§ط¹طھ) ---
    def validate(self, attrs):
        if 'duration' in attrs:
            attrs['duration_hours'] = attrs.pop('duration')
        elif not self.instance:  # ط§ع¯ط± ط³ط§ط®طھ طھط³ع© ط¬ط¯غŒط¯ ط¨ظˆط¯ ظˆ ظ…ظ‚ط¯ط§ط±غŒ ظ†غŒط§ظ…ط¯
            attrs['duration_hours'] = 40.0  # ط¯غŒظپط§ظ„طھ 5 ط±ظˆط²
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
        prefetched_assignments = getattr(obj.task, 'revision_assignments', None)
        if prefetched_assignments is not None:
            return [assign.resource.name for assign in prefetched_assignments]

        assignments = Assignment.objects.filter(task=obj.task, revision=obj.revision)
        # ط¨ظ‡ ط¬ط§غŒ ط¨ط±ع¯ط±ط¯ط§ظ†ط¯ظ† غŒع© ط¢ط¨ط¬ع©طھ ط¯ط§ط±ط§غŒ ظ†ط§ظ… ظˆ ط¢غŒط¯غŒطŒ ظپظ‚ط· ظ†ط§ظ… ظ…ظ†ط§ط¨ط¹ ط±ط§ ط¨ظ‡ طµظˆط±طھ ظ…طھظ† ط³ط§ط¯ظ‡ ط¨ط±ظ…غŒâ€Œع¯ط±ط¯ط§ظ†غŒظ…
        # ط®ط±ظˆط¬غŒ ط¨ظ‡ ط§غŒظ† ط´ع©ظ„ ظ…غŒâ€Œط´ظˆط¯: ['Ali', 'Crane', 'Excavator']
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

        from .financial_services import validate_progress_transition, validate_task_delivery, validate_task_start

        if actual_start is not None:
            validate_task_start(instance.task)
        if progress is not None:
            validate_progress_transition(instance.task, progress)
        if actual_finish is not None or (progress is not None and progress >= 100):
            validate_task_delivery(instance.task)

        instance = super().update(instance, validated_data)

        # ط§ع¯ط± ظ‡ط± غŒع© ط§ط² ظپغŒظ„ط¯ظ‡ط§غŒ actual ط§ط±ط³ط§ظ„ ط´ط¯ظ‡ ط¨ط§ط´ط¯طŒ TaskActual ط±ط§ ط¢ظ¾ط¯غŒطھ ع©ظ†
        if progress is not None or actual_start is not None or actual_finish is not None:
            actual, _ = TaskActual.objects.get_or_create(
                task_version=instance,
                defaults={
                    'updated_by': self.context['request'].user
                }
            )
            if progress is not None:
                actual.progress = progress
                if float(progress) <= 0:
                    # Progress 0 means the task has no actual execution yet.
                    # Clear actual dates to keep CPM/EVM variance calculations clean.
                    actual.actual_start = None
                    actual.actual_finish = None
            if progress is None or float(progress) > 0:
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

    def _lag_hours_from_request(self, revision):
        lag_days = float(self.initial_data.get('lag') or 0)
        return int(round(lag_days * project_work_hours_per_day(revision.project)))

    def create(self, validated_data):
        predecessor_id = validated_data.pop('predecessor_id')
        successor_id = validated_data.pop('successor_id')

        validated_data['predecessor_id'] = predecessor_id
        validated_data['successor_id'] = successor_id
        revision = validated_data.get('revision')
        if revision is not None:
            validated_data['lag_hours'] = self._lag_hours_from_request(revision)

        return super().create(validated_data)

    def update(self, instance, validated_data):
        if 'lag' in self.initial_data:
            validated_data['lag_hours'] = self._lag_hours_from_request(instance.revision)
        return super().update(instance, validated_data)

    def get_lag(self, obj):
        work_hours = project_work_hours_per_day(obj.revision.project)
        return obj.lag_hours / work_hours if obj.lag_hours else 0


def subproject_virtual_id(project_id):
    return f"subproject-{project_id}"


def project_work_hours_per_day(project):
    calendar = getattr(project, 'calendar', None)
    if calendar is None:
        calendar = Calendar.objects.filter(project=project, is_default=True).first()
    if calendar is None:
        return 8

    hours_by_weekday = {}
    for interval in calendar.intervals.all():
        start_minutes = interval.start_time.hour * 60 + interval.start_time.minute
        end_minutes = interval.end_time.hour * 60 + interval.end_time.minute
        if end_minutes > start_minutes:
            hours_by_weekday[interval.weekday] = hours_by_weekday.get(interval.weekday, 0) + (end_minutes - start_minutes) / 60

    working_day_hours = [hours for hours in hours_by_weekday.values() if hours > 0]
    if not working_day_hours:
        return 8
    return sum(working_day_hours) / len(working_day_hours)


class SubprojectDependencySerializer(serializers.ModelSerializer):
    revisionId = serializers.PrimaryKeyRelatedField(source='revision', queryset=Revision.objects.all(), write_only=True)
    taskId = serializers.PrimaryKeyRelatedField(source='task', queryset=Task.objects.all(), write_only=True)
    subprojectId = serializers.PrimaryKeyRelatedField(source='subproject', queryset=Project.objects.all(), write_only=True)
    fromId = serializers.SerializerMethodField()
    toId = serializers.SerializerMethodField()
    type = serializers.CharField(source='dependency_type')
    lag = serializers.SerializerMethodField()

    class Meta:
        model = SubprojectDependency
        fields = ['id', 'revisionId', 'taskId', 'subprojectId', 'direction', 'fromId', 'toId', 'type', 'lag']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['id'] = f"subdep-{instance.id}"
        return data

    def validate(self, attrs):
        dependency_type = attrs.get('dependency_type')
        if dependency_type and dependency_type not in dict(Dependency.LINK_TYPES):
            raise serializers.ValidationError({'type': 'Invalid dependency type.'})

        revision = attrs.get('revision') or getattr(self.instance, 'revision', None)
        task = attrs.get('task') or getattr(self.instance, 'task', None)
        subproject = attrs.get('subproject') or getattr(self.instance, 'subproject', None)
        if revision and task and task.project_id != revision.project_id:
            raise serializers.ValidationError({'taskId': 'Task must belong to the parent project revision.'})
        if revision and subproject and subproject.parent_project_id != revision.project_id:
            raise serializers.ValidationError({'subprojectId': 'Subproject must be assigned under this parent project.'})
        return attrs

    def create(self, validated_data):
        validated_data['lag_hours'] = int(round(float(self.initial_data.get('lag') or 0) * project_work_hours_per_day(validated_data['revision'].project)))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if 'lag' in self.initial_data:
            validated_data['lag_hours'] = int(round(float(self.initial_data.get('lag') or 0) * project_work_hours_per_day(instance.revision.project)))
        return super().update(instance, validated_data)

    def get_fromId(self, obj):
        if obj.direction == SubprojectDependency.DIRECTION_TASK_TO_SUBPROJECT:
            return str(obj.task_id)
        return subproject_virtual_id(obj.subproject_id)

    def get_toId(self, obj):
        if obj.direction == SubprojectDependency.DIRECTION_TASK_TO_SUBPROJECT:
            return subproject_virtual_id(obj.subproject_id)
        return str(obj.task_id)

    def get_lag(self, obj):
        work_hours = project_work_hours_per_day(obj.revision.project)
        return obj.lag_hours / work_hours if obj.lag_hours else 0


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
            return f"/api/planning/task-reports/{obj.report_id}/attachments/{obj.id}/download/"
        return None


class TaskReportLogSerializer(serializers.ModelSerializer):
    attachments = TaskReportAttachmentSerializer(many=True, read_only=True)
    task_name = serializers.SerializerMethodField()
    task_code = serializers.SerializerMethodField()
    project_id = serializers.SerializerMethodField()
    project_name = serializers.SerializerMethodField()
    revision_id = serializers.SerializerMethodField()

    class Meta:
        model = TaskReportLog
        fields = [
            'id',
            'task',
            'task_name',
            'task_code',
            'project_id',
            'project_name',
            'revision_id',
            'user',
            'status',
            'progress_percent',
            'time_spent_hours',
            'notes',
            'blockers',
            'timestamp',
            # ظپغŒظ„ط¯ظ‡ط§غŒ state machine (ط¯ظˆâ€Œظ…ط±ط­ظ„ظ‡â€Œط§غŒ)
            'approval_status',
            'reviewer_approved_by',
            'reviewer_approved_at',
            'final_approved_by',
            'final_approved_at',
            # legacy (ط³ط§ط²ع¯ط§ط±غŒ طھط§ ظپط±ط§ظ†طھâ€Œط§ظ†ط¯ ط¨ظ‡â€Œط±ظˆط²ط±ط³ط§ظ†غŒ ط´ظˆط¯)
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

    def _display_task_version(self, obj):
        project = obj.task.project
        candidate_revision_ids = [
            getattr(obj, 'revision_id', None),
            project.current_execution_revision_id,
            project.working_revision_id,
            project.current_forecast_revision_id,
            project.active_baseline_revision_id,
        ]
        for revision_id in candidate_revision_ids:
            if not revision_id:
                continue
            version = obj.task.versions.filter(revision_id=revision_id, is_deleted=False).select_related('wbs_node', 'revision').first()
            if version:
                return version
        return None

    def get_task_name(self, obj):
        version = self._display_task_version(obj)
        return version.title if version else ''

    def get_task_code(self, obj):
        version = self._display_task_version(obj)
        return version.wbs_node.wbs_code if version and version.wbs_node else f"ACT-{str(obj.task_id)[:4].upper()}"

    def get_project_id(self, obj):
        return str(obj.task.project_id)

    def get_project_name(self, obj):
        return obj.task.project.name

    def get_revision_id(self, obj):
        version = self._display_task_version(obj)
        return str(version.revision_id) if version else None

class TaskChatMessageSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = TaskChatMessage
        fields = [
            'id',
            'task',
            'task_name',
            'task_code',
            'project_id',
            'project_name',
            'revision_id',
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
    # ط¨ط±ط§غŒ ط§غŒظ†ع©ظ‡ ط§ط³ظ… ظپغŒظ„ط¯ظ‡ط§ ط¯ط± ظپط±ط§ظ†طھâ€Œط§ظ†ط¯ ط±ط§ط­طھâ€Œطھط± ظ…ظ¾ ط´ظˆط¯
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
            # ظپغŒظ„ط¯ظ‡ط§غŒ ظ‡ظ…â€Œظ†ط§ظ… ط¨ط±ط§غŒ ظپط±ط§ظ†طھâ€Œط§ظ†ط¯:
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

    # â”€â”€ ظپغŒظ„ط¯ظ‡ط§غŒ ظ†ظ…ط§غŒط´غŒ ط¬ط¯غŒط¯ â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
            # ظ†ظ…ط§غŒط´غŒ
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
        # ظ¾غŒط¯ط§ ع©ط±ط¯ظ† ط¹ظ†ظˆط§ظ† طھط³ع© ط¯ط± ظ‡ظ…ط§ظ† ط±غŒظˆغŒعکظ†غŒ ع©ظ‡ ع¯ط²ط§ط±ط´ ط¨ط±ط§غŒ ط¢ظ† ط«ط¨طھ ط´ط¯ظ‡
        tv = obj.task.versions.filter(revision=obj.revision).first()
        return tv.title if tv else "طھط³ع© ظ†ط§ظ…ط´ط®طµ"

    def get_task_code(self, obj):
        # ط§ط³طھط®ط±ط§ط¬ ع©ط¯ WBS ط¨ط±ط§غŒ ط§غŒظ† طھط³ع©
        tv = obj.task.versions.filter(revision=obj.revision).first()
        return tv.wbs_node.wbs_code if (tv and hasattr(tv, 'wbs_node')) else "N/A"



# â”€â”€â”€ SystemSettings Serializer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

# â”€â”€â”€ ط¬ط§غŒع¯ط²غŒظ† ع©ظ† ط§غŒظ† ط¨ظ„ط§ع© ط±ط§ ط¯ط± serializers.py â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class FundingSourceSerializer(serializers.ModelSerializer):
    allocated_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    unallocated_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    submitted_by = serializers.PrimaryKeyRelatedField(read_only=True)
    approved_by = serializers.PrimaryKeyRelatedField(read_only=True)
    rejected_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = FundingSource
        fields = [
            'id', 'title', 'source_type', 'source_party', 'reference_no',
            'received_date', 'total_amount', 'currency', 'status', 'description',
            'allocated_amount', 'unallocated_amount',
            'created_by', 'submitted_by', 'submitted_at',
            'approved_by', 'approved_at', 'rejected_by', 'rejected_at',
            'rejection_reason', 'created_at',
        ]
        read_only_fields = [
            'status', 'allocated_amount', 'unallocated_amount',
            'created_by', 'submitted_by', 'submitted_at',
            'approved_by', 'approved_at', 'rejected_by', 'rejected_at',
            'rejection_reason', 'created_at',
        ]

    def validate_total_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('Funding amount must be greater than zero.')
        if self.instance and value < self.instance.allocated_amount:
            raise serializers.ValidationError('Funding amount cannot be less than existing root allocations.')
        return value


class BudgetAllocationSerializer(serializers.ModelSerializer):
    actual_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    child_allocated_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    remaining_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    borrowed_in_amount = serializers.SerializerMethodField()
    borrowed_out_amount = serializers.SerializerMethodField()
    effective_amount = serializers.SerializerMethodField()
    funding_source_title = serializers.CharField(source='funding_source.title', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    task_title = serializers.SerializerMethodField()
    wbs_title = serializers.CharField(source='wbs_node.title', read_only=True, default=None)
    org_unit_name = serializers.CharField(source='org_unit.name', read_only=True, default=None)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    submitted_by = serializers.PrimaryKeyRelatedField(read_only=True)
    approved_by = serializers.PrimaryKeyRelatedField(read_only=True)
    rejected_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = BudgetAllocation
        fields = [
            'id', 'funding_source', 'funding_source_title',
            'parent_allocation',
            'project', 'project_name', 'revision',
            'scope_type', 'wbs_node', 'wbs_title', 'task', 'task_title',
            'org_unit', 'org_unit_name', 'cost_type', 'allocated_amount',
            'is_borrow_sink', 'status', 'actual_amount', 'child_allocated_amount',
            'borrowed_in_amount', 'borrowed_out_amount', 'effective_amount',
            'remaining_amount', 'description',
            'created_by', 'submitted_by', 'submitted_at',
            'approved_by', 'approved_at', 'rejected_by', 'rejected_at',
            'rejection_reason', 'created_at',
        ]
        read_only_fields = [
            'status', 'is_borrow_sink', 'actual_amount', 'remaining_amount',
            'child_allocated_amount', 'borrowed_in_amount', 'borrowed_out_amount',
            'effective_amount',
            'created_by', 'submitted_by', 'submitted_at',
            'approved_by', 'approved_at', 'rejected_by', 'rejected_at',
            'rejection_reason', 'created_at',
        ]
        extra_kwargs = {'project': {'required': False, 'allow_null': True}}

    def get_task_title(self, obj):
        if not obj.task:
            return None
        tv = obj.task.versions.filter(revision=obj.revision, is_deleted=False).first()
        if not tv:
            tv = obj.task.versions.filter(is_deleted=False).last()
        return tv.title if tv else str(obj.task.id)

    def get_borrowed_in_amount(self, obj):
        return obj.borrowed_in_amount()

    def get_borrowed_out_amount(self, obj):
        return obj.borrowed_out_amount()

    def get_effective_amount(self, obj):
        return obj.allocated_amount + obj.borrowed_in_amount() - obj.borrowed_out_amount()

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
        cost_type = value('cost_type')
        allocated_amount = value('allocated_amount')
        parent_allocation = value('parent_allocation')
        is_borrow_sink = value('is_borrow_sink')

        if allocated_amount is not None and allocated_amount <= 0 and not is_borrow_sink:
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
        if scope_type != 'TASK' and task:
            raise serializers.ValidationError({'task': 'Task can only be set for TASK allocations.'})
        if scope_type not in {'WBS', 'TASK'} and wbs_node:
            raise serializers.ValidationError({'wbs_node': 'WBS node can only be set for WBS or TASK allocations.'})
        if scope_type != 'ORG_UNIT' and org_unit:
            raise serializers.ValidationError({'org_unit': 'Org unit can only be set for ORG_UNIT allocations.'})
        if value('status') == 'APPROVED' and funding_source and funding_source.status != 'APPROVED':
            raise serializers.ValidationError({'status': 'Approved allocations require an approved funding source.'})
        if parent_allocation:
            if instance and parent_allocation.id == instance.id:
                raise serializers.ValidationError({'parent_allocation': 'Budget allocation cannot be its own parent.'})
            if funding_source and parent_allocation.funding_source_id != funding_source.id:
                raise serializers.ValidationError({'parent_allocation': 'Child allocation must use the same funding source as its parent.'})
            if cost_type and parent_allocation.cost_type != cost_type:
                raise serializers.ValidationError({'cost_type': 'Child allocation must use the same cost type as its parent.'})
            if value('status') == 'APPROVED' and parent_allocation.status != 'APPROVED':
                raise serializers.ValidationError({'status': 'Approved child allocations require an approved parent allocation.'})
            if parent_allocation.project_id and (not project or parent_allocation.project_id != project.id):
                raise serializers.ValidationError({'project': 'Child allocation must stay inside the parent project.'})
            if parent_allocation.scope_type == 'PROJECT' and scope_type == 'ORG_UNIT':
                raise serializers.ValidationError({'scope_type': 'Project budget children cannot target org units.'})
            if parent_allocation.scope_type == 'WBS':
                if scope_type not in {'WBS', 'TASK'}:
                    raise serializers.ValidationError({'scope_type': 'WBS budget children must target WBS nodes or tasks inside that WBS.'})
                if scope_type == 'WBS' and (not wbs_node or not wbs_node.is_descendant_of(parent_allocation.wbs_node, include_self=True)):
                    raise serializers.ValidationError({'wbs_node': 'Child WBS allocation must be inside the parent WBS.'})
                if scope_type == 'TASK':
                    versions = task.versions.filter(is_deleted=False) if task else TaskVersion.objects.none()
                    target_revision = revision or parent_allocation.revision or (project.current_execution_revision if project else None)
                    task_version = versions.filter(revision=target_revision).first() if target_revision else None
                    if not task_version or not task_version.wbs_node.is_descendant_of(parent_allocation.wbs_node, include_self=True):
                        raise serializers.ValidationError({'task': 'Child task allocation must be inside the parent WBS.'})
            if parent_allocation.scope_type == 'TASK' and (scope_type != 'TASK' or not task or parent_allocation.task_id != task.id):
                raise serializers.ValidationError({'task': 'Task budget children must target the same task.'})
            if parent_allocation.scope_type == 'ORG_UNIT':
                if scope_type != 'ORG_UNIT':
                    raise serializers.ValidationError({'scope_type': 'Org unit budget children must stay inside org unit scope.'})
                if parent_allocation.org_unit_id and (not org_unit or parent_allocation.org_unit_id != org_unit.id):
                    raise serializers.ValidationError({'org_unit': 'Org unit budget children must stay inside the same org unit.'})
            existing_children = parent_allocation.child_allocations.all()
            if instance:
                existing_children = existing_children.exclude(pk=instance.pk)
            current_child_total = existing_children.aggregate(total=Sum('allocated_amount'))['total'] or 0
            if allocated_amount is not None and current_child_total + allocated_amount > parent_allocation.allocated_amount:
                raise serializers.ValidationError({'allocated_amount': 'Child allocations cannot exceed parent allocation amount.'})

        if instance and allocated_amount is not None:
            children_total = instance.child_allocations.aggregate(total=Sum('allocated_amount'))['total'] or 0
            required_capacity = instance.actual_amount + children_total + instance.borrowed_out_amount() - instance.borrowed_in_amount()
            if required_capacity < 0:
                required_capacity = 0
            if allocated_amount < required_capacity:
                raise serializers.ValidationError({
                    'allocated_amount': 'Allocation amount cannot be less than actual usage, child budgets, and borrowed-out capacity.'
                })

        if funding_source and allocated_amount is not None:
            if not parent_allocation:
                existing = BudgetAllocation.objects.filter(funding_source=funding_source, parent_allocation__isnull=True)
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


class BudgetBorrowSerializer(serializers.ModelSerializer):
    from_allocation_label = serializers.SerializerMethodField()
    to_allocation_label = serializers.SerializerMethodField()
    requested_by = serializers.PrimaryKeyRelatedField(read_only=True)
    submitted_by = serializers.PrimaryKeyRelatedField(read_only=True)
    approved_by = serializers.PrimaryKeyRelatedField(read_only=True)
    rejected_by = serializers.PrimaryKeyRelatedField(read_only=True)
    settled_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = BudgetBorrow
        fields = [
            'id',
            'from_allocation',
            'from_allocation_label',
            'to_allocation',
            'to_allocation_label',
            'destination_project',
            'destination_revision',
            'destination_scope_type',
            'destination_wbs_node',
            'destination_task',
            'destination_org_unit',
            'destination_cost_type',
            'destination_description',
            'amount',
            'reason',
            'status',
            'requested_by',
            'submitted_by',
            'submitted_at',
            'approved_by',
            'approved_at',
            'rejected_by',
            'rejected_at',
            'rejection_reason',
            'settled_by',
            'settled_at',
            'created_at',
        ]
        read_only_fields = [
            'status',
            'requested_by',
            'submitted_by',
            'submitted_at',
            'approved_by',
            'approved_at',
            'rejected_by',
            'rejected_at',
            'rejection_reason',
            'settled_by',
            'settled_at',
            'created_at',
        ]
        extra_kwargs = {'to_allocation': {'required': False, 'allow_null': True}}

    def get_allocation_label(self, allocation):
        target = allocation.project.name if allocation.project_id else None
        if allocation.task_id:
            target = f"Task {allocation.task_id}"
        elif allocation.wbs_node_id:
            target = allocation.wbs_node.title
        elif allocation.org_unit_id:
            target = allocation.org_unit.name
        return f"{target or 'Company'} / {allocation.scope_type} / {allocation.cost_type}"

    def get_from_allocation_label(self, obj):
        return self.get_allocation_label(obj.from_allocation)

    def get_to_allocation_label(self, obj):
        if obj.to_allocation_id:
            return self.get_allocation_label(obj.to_allocation)
        if obj.destination_task_id:
            target = f"Task {obj.destination_task_id}"
        elif obj.destination_wbs_node_id:
            target = obj.destination_wbs_node.title
        elif obj.destination_project_id:
            target = obj.destination_project.name
        elif obj.destination_org_unit_id:
            target = obj.destination_org_unit.name
        else:
            target = "New destination"
        return f"{target} / {obj.destination_scope_type or '-'} / {obj.destination_cost_type or '-'}"

    def validate(self, attrs):
        instance = self.instance

        def value(name):
            if name in attrs:
                return attrs[name]
            return getattr(instance, name, None) if instance else None

        from_allocation = value('from_allocation')
        to_allocation = value('to_allocation')
        destination_scope_type = value('destination_scope_type')
        destination_project = value('destination_project')
        destination_wbs_node = value('destination_wbs_node')
        destination_task = value('destination_task')
        destination_org_unit = value('destination_org_unit')
        destination_cost_type = value('destination_cost_type')
        amount = value('amount')
        status = value('status') or 'DRAFT'

        if amount is not None and amount <= 0:
            raise serializers.ValidationError({'amount': 'Borrow amount must be greater than zero.'})
        if from_allocation:
            if from_allocation.status != 'APPROVED':
                raise serializers.ValidationError({'from_allocation': 'Budget borrow requires an approved source allocation.'})
            if to_allocation:
                if from_allocation.id == to_allocation.id:
                    raise serializers.ValidationError({'to_allocation': 'Borrow source and destination cannot be the same allocation.'})
                if to_allocation.status != 'APPROVED':
                    raise serializers.ValidationError({'to_allocation': 'Budget borrow requires an approved destination allocation.'})
            else:
                if not destination_scope_type:
                    raise serializers.ValidationError({'destination_scope_type': 'Destination scope type is required when destination allocation is new.'})
                if not destination_cost_type:
                    raise serializers.ValidationError({'destination_cost_type': 'Destination cost type is required when destination allocation is new.'})
                if destination_scope_type in {'PROJECT', 'WBS', 'TASK'} and not destination_project:
                    raise serializers.ValidationError({'destination_project': 'Destination project is required for project, WBS, and task borrow targets.'})
                if destination_scope_type in {'WBS', 'TASK'} and not destination_wbs_node:
                    raise serializers.ValidationError({'destination_wbs_node': 'Destination WBS is required for WBS and task borrow targets.'})
                if destination_scope_type == 'TASK' and not destination_task:
                    raise serializers.ValidationError({'destination_task': 'Destination task is required for task borrow targets.'})
            if status == 'APPROVED' and amount > from_allocation.borrow_available_amount(exclude_borrow_id=instance.pk if instance else None):
                raise serializers.ValidationError({'amount': 'Borrow amount exceeds source allocation available capacity.'})

        return attrs


class UnfundedForecastCostSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source='project.name', read_only=True)
    task_title = serializers.SerializerMethodField()
    wbs_title = serializers.CharField(source='wbs_node.title', read_only=True, default=None)
    org_unit_name = serializers.CharField(source='org_unit.name', read_only=True, default=None)
    linked_allocation_label = serializers.SerializerMethodField()
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = UnfundedForecastCost
        fields = [
            'id',
            'title',
            'project',
            'project_name',
            'revision',
            'scope_type',
            'wbs_node',
            'wbs_title',
            'task',
            'task_title',
            'org_unit',
            'org_unit_name',
            'cost_type',
            'forecast_date',
            'amount',
            'confidence',
            'status',
            'linked_allocation',
            'linked_allocation_label',
            'description',
            'created_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at', 'linked_allocation_label']
        extra_kwargs = {'project': {'required': False, 'allow_null': True}}

    def get_task_title(self, obj):
        if not obj.task:
            return None
        tv = obj.task.versions.filter(revision=obj.revision, is_deleted=False).first()
        if not tv:
            tv = obj.task.versions.filter(is_deleted=False).last()
        return tv.title if tv else str(obj.task.id)

    def get_linked_allocation_label(self, obj):
        if not obj.linked_allocation_id:
            return None
        allocation = obj.linked_allocation
        target = allocation.project.name if allocation.project_id else None
        if allocation.task_id:
            target = f"Task {allocation.task_id}"
        elif allocation.wbs_node_id:
            target = allocation.wbs_node.title
        elif allocation.org_unit_id:
            target = allocation.org_unit.name
        return f"{allocation.funding_source.title} / {target or 'Company'} / {allocation.scope_type} / {allocation.cost_type}"

    def validate(self, attrs):
        instance = self.instance

        def value(name):
            if name in attrs:
                return attrs[name]
            return getattr(instance, name, None) if instance else None

        project = value('project')
        revision = value('revision')
        scope_type = value('scope_type')
        wbs_node = value('wbs_node')
        task = value('task')
        org_unit = value('org_unit')
        amount = value('amount')

        if amount is not None and amount <= 0:
            raise serializers.ValidationError({'amount': 'Forecast amount must be greater than zero.'})
        if scope_type in {'PROJECT', 'WBS', 'TASK'} and not project:
            raise serializers.ValidationError({'project': 'Project is required for project, WBS, and task forecast costs.'})
        if revision and project and revision.project_id != project.id:
            raise serializers.ValidationError({'revision': 'Revision must belong to the selected project.'})
        if wbs_node and project and wbs_node.revision.project_id != project.id:
            raise serializers.ValidationError({'wbs_node': 'WBS node must belong to the selected project.'})
        if task and project and task.project_id != project.id:
            raise serializers.ValidationError({'task': 'Task must belong to the selected project.'})
        if scope_type == 'TASK' and not task:
            raise serializers.ValidationError({'task': 'Task forecast requires a task.'})
        if scope_type in {'WBS', 'TASK'} and not wbs_node:
            raise serializers.ValidationError({'wbs_node': 'WBS forecast requires a WBS node.'})
        if scope_type != 'TASK' and task:
            raise serializers.ValidationError({'task': 'Task can only be set for TASK forecasts.'})
        if scope_type not in {'WBS', 'TASK'} and wbs_node:
            raise serializers.ValidationError({'wbs_node': 'WBS node can only be set for WBS or TASK forecasts.'})
        if scope_type != 'ORG_UNIT' and org_unit:
            raise serializers.ValidationError({'org_unit': 'Org unit can only be set for ORG_UNIT forecasts.'})

        return attrs


class CostTransactionSerializer(serializers.ModelSerializer):
    # ظپغŒظ„ط¯ظ‡ط§غŒ read-only ع©ظ‡ ط¨ع©ظ†ط¯ ظ…ط­ط§ط³ط¨ظ‡ ظ…غŒâ€Œع©ظ†ط¯
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    # ظ†ظ…ط§غŒط´ ظ†ط§ظ… ظ…ظ†ط¨ط¹ ط§ظ†طھط®ط§ط¨غŒ ط¨ط±ط§غŒ ط®ظˆط§ظ†ط¯ظ† ط¢ط³ط§ظ† ط¯ط± ظپط±ط§ظ†طھ
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
            'resource_rate',   # FK â€” ط¨ط±ط§غŒ non-EXPENSE ط§ط¬ط¨ط§ط±غŒ
            'resource',        # FK â€” ط§ط®طھغŒط§ط±غŒ (ط®ظˆط§ظ†ط¯ظ‡ ظ…غŒâ€Œط´ظˆط¯ ط§ط² resource_rate.resource ط¯ط± clean)
            'expense_type',    # FK â€” ط¨ط±ط§غŒ EXPENSE ط§ط¬ط¨ط§ط±غŒ
            'budget_allocation',
            'transaction_type',
            'transaction_date',
            'quantity',
            'unit_rate',       # ط¨ط±ط§غŒ non-EXPENSE
            'expense_rate',    # ط¨ط±ط§غŒ EXPENSE
            'amount',          # read-onlyطŒ ظ…ط­ط§ط³ط¨ظ‡â€Œط´ط¯ظ‡ ط¯ط± save()
            'description',
            'created_by',
            'created_at',
            # ظپغŒظ„ط¯ظ‡ط§غŒ ع©ظ…ع©غŒ ظ†ظ…ط§غŒط´غŒ (read-only)
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


class PaymentTransactionSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = PaymentTransaction
        fields = ['id', 'milestone', 'transaction_type', 'amount', 'transaction_date', 'reference_number', 'description', 'created_by', 'created_at']
        read_only_fields = ['id', 'created_by', 'created_at']

    def validate(self, attrs):
        instance = self.instance
        transaction_type = attrs.get('transaction_type', getattr(instance, 'transaction_type', None))
        amount = attrs.get('amount', getattr(instance, 'amount', None))
        if transaction_type in {PaymentTransaction.TYPE_PAYMENT, PaymentTransaction.TYPE_REFUND} and amount is not None and amount <= 0:
            raise serializers.ValidationError({'amount': 'Payment and refund amounts must be greater than zero.'})
        if transaction_type == PaymentTransaction.TYPE_ADJUSTMENT and amount == 0:
            raise serializers.ValidationError({'amount': 'Adjustment amount cannot be zero.'})
        return attrs


class PaymentMilestoneSerializer(serializers.ModelSerializer):
    calculated_amount = serializers.SerializerMethodField()
    paid_amount = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()
    transactions = PaymentTransactionSerializer(many=True, read_only=True)

    class Meta:
        model = PaymentMilestone
        fields = [
            'id', 'financial_plan', 'title', 'sequence', 'trigger_type', 'amount_type',
            'percentage', 'fixed_amount', 'progress_threshold', 'due_date',
            'blocks_task_start', 'blocks_task_delivery', 'blocks_progress_after_threshold',
            'status', 'description', 'calculated_amount', 'paid_amount', 'outstanding',
            'transactions', 'created_at', 'updated_at',
        ]
        read_only_fields = ['status', 'calculated_amount', 'paid_amount', 'outstanding', 'transactions', 'created_at', 'updated_at']

    def get_calculated_amount(self, obj):
        return str(milestone_amount(obj))

    def get_paid_amount(self, obj):
        return str(milestone_paid_amount(obj))

    def get_outstanding(self, obj):
        return str(milestone_outstanding(obj))

    def validate(self, attrs):
        instance = self.instance
        financial_plan = attrs.get('financial_plan', getattr(instance, 'financial_plan', None))
        sequence = attrs.get('sequence', getattr(instance, 'sequence', None))
        amount_type = attrs.get('amount_type', getattr(instance, 'amount_type', None))
        percentage = attrs.get('percentage', getattr(instance, 'percentage', None))
        fixed_amount = attrs.get('fixed_amount', getattr(instance, 'fixed_amount', None))
        trigger_type = attrs.get('trigger_type', getattr(instance, 'trigger_type', None))
        progress_threshold = attrs.get('progress_threshold', getattr(instance, 'progress_threshold', None))
        due_date = attrs.get('due_date', getattr(instance, 'due_date', None))
        if financial_plan and sequence is not None:
            duplicate_sequence = financial_plan.milestones.filter(sequence=sequence)
            if instance:
                duplicate_sequence = duplicate_sequence.exclude(pk=instance.pk)
            if duplicate_sequence.exists():
                raise serializers.ValidationError({'sequence': 'This sequence is already used in this financial plan. Use the next available number.'})
        if instance and instance.transactions.exists() and {'amount_type', 'percentage', 'fixed_amount', 'financial_plan'}.intersection(attrs.keys()):
            raise serializers.ValidationError({'milestone': 'Milestone with transactions cannot change financial amount fields.'})
        if amount_type == PaymentMilestone.AMOUNT_PERCENTAGE:
            if percentage is None:
                raise serializers.ValidationError({'percentage': 'Percentage is required.'})
            if fixed_amount is not None:
                raise serializers.ValidationError({'fixed_amount': 'Fixed amount must be empty for percentage milestones.'})
            if percentage <= 0 or percentage > 100:
                raise serializers.ValidationError({'percentage': 'Percentage must be > 0 and <= 100.'})
        elif amount_type == PaymentMilestone.AMOUNT_FIXED:
            if fixed_amount is None:
                raise serializers.ValidationError({'fixed_amount': 'Fixed amount is required.'})
            if percentage is not None:
                raise serializers.ValidationError({'percentage': 'Percentage must be empty for fixed milestones.'})
            if fixed_amount <= 0:
                raise serializers.ValidationError({'fixed_amount': 'Fixed amount must be greater than zero.'})
        if financial_plan and amount_type == PaymentMilestone.AMOUNT_PERCENTAGE:
            existing_total = financial_plan.milestones.filter(amount_type=PaymentMilestone.AMOUNT_PERCENTAGE)
            if instance:
                existing_total = existing_total.exclude(pk=instance.pk)
            existing_total = existing_total.aggregate(total=Sum('percentage'))['total'] or Decimal('0')
            if existing_total + percentage > Decimal('100'):
                raise serializers.ValidationError({'percentage': 'Percentage milestones cannot exceed 100% of the plan.'})
        if trigger_type == PaymentMilestone.TRIGGER_APPROVED_PROGRESS and progress_threshold is None:
            raise serializers.ValidationError({'progress_threshold': 'Progress threshold is required.'})
        if progress_threshold is not None and (progress_threshold < 0 or progress_threshold > 100):
            raise serializers.ValidationError({'progress_threshold': 'Progress threshold must be between 0 and 100.'})
        if trigger_type == PaymentMilestone.TRIGGER_FIXED_DATE and due_date is None:
            raise serializers.ValidationError({'due_date': 'Due date is required.'})
        return attrs


class TaskFinancialPlanSerializer(serializers.ModelSerializer):
    milestones = PaymentMilestoneSerializer(many=True, read_only=True)
    financial_status = serializers.SerializerMethodField()
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = TaskFinancialPlan
        fields = [
            'id', 'task', 'direction', 'contract_amount', 'currency', 'status',
            'description', 'created_by', 'created_at', 'updated_at', 'milestones', 'financial_status',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at', 'financial_status']

    def get_financial_status(self, obj):
        if obj.status == TaskFinancialPlan.STATUS_ACTIVE:
            return get_task_financial_status(obj.task)
        return None

    def validate_contract_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('Contract amount must be greater than zero.')
        return value

    def validate(self, attrs):
        status = attrs.get('status', getattr(self.instance, 'status', TaskFinancialPlan.STATUS_DRAFT))
        if status == TaskFinancialPlan.STATUS_ACTIVE:
            raise serializers.ValidationError({'status': 'Create or update the plan as draft, add milestones, then use activate.'})
        return attrs
class TaskDropdownSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    code = serializers.SerializerMethodField()
    wbsNodeId = serializers.SerializerMethodField()
    wbsVersionId = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = ['id', 'name', 'code', 'wbsNodeId', 'wbsVersionId']

    @staticmethod
    def _execution_version(obj):
        revision_id = obj.project.current_execution_revision_id
        if not revision_id:
            return None
        return obj.versions.filter(
            revision_id=revision_id, is_deleted=False
        ).select_related('wbs_node').first()

    def get_name(self, obj):
        version = self._execution_version(obj)
        return version.title if version else "Task without an official execution version"

    def get_code(self, obj):
        version = self._execution_version(obj)
        if version and version.wbs_node:
            return version.wbs_node.wbs_code, version.wbs_node.title
        return ""

    def get_wbsNodeId(self, obj):
        version = self._execution_version(obj)
        return str(version.wbs_node.node_id) if version and version.wbs_node else None

    def get_wbsVersionId(self, obj):
        version = self._execution_version(obj)
        return version.wbs_node_id if version and version.wbs_node else None

class LevelingPlanProjectSerializer(serializers.ModelSerializer):
    projectId = serializers.CharField(source="project_id", read_only=True)
    projectName = serializers.CharField(source="project.name", read_only=True)
    revisionId = serializers.CharField(source="revision_id", read_only=True)
    revisionNumber = serializers.IntegerField(source="revision.number", read_only=True)

    class Meta:
        model = LevelingPlanProject
        fields = [
            "id", "projectId", "projectName", "revisionId",
            "revisionNumber", "priority",
        ]


class ResourceLevelingPlanSerializer(serializers.ModelSerializer):
    projects = LevelingPlanProjectSerializer(source="plan_projects", many=True, read_only=True)
    createdByName = serializers.CharField(source="executed_by.username", read_only=True)
    createdAt = serializers.DateTimeField(source="executed_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    dataDate = serializers.DateTimeField(source="data_date", read_only=True)
    lastRunAt = serializers.DateTimeField(source="last_run_at", read_only=True)
    publishedAt = serializers.DateTimeField(source="published_at", read_only=True)
    priorityRules = serializers.JSONField(source="priority_rules", read_only=True)
    taskCount = serializers.SerializerMethodField()
    resourceCount = serializers.SerializerMethodField()

    class Meta:
        model = GlobalLevelingRun
        fields = [
            "id", "name", "description", "status", "settings",
            "createdByName", "createdAt", "updatedAt", "dataDate",
            "lastRunAt", "publishedAt", "priorityRules", "projects",
            "taskCount", "resourceCount",
        ]

    def get_taskCount(self, obj):
        return obj.task_metrics.count()

    def get_resourceCount(self, obj):
        return obj.resource_usages.values("resource_id").distinct().count()



