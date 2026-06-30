from rest_framework import serializers
from .models import ManagementReport, CuratedBottleneck


class CuratedBottleneckSerializer(serializers.ModelSerializer):
    # تعریف فیلدهای محاسباتی و داینامیک (فقط برای نمایش)
    task_name = serializers.SerializerMethodField()
    wbs_node_name = serializers.SerializerMethodField()

    class Meta:
        model = CuratedBottleneck
        fields = [
            'id', 'task', 'task_name', 'wbs_node_name',
            'issue_type', 'description', 'severity',
            'is_manual', 'planner_remark'
        ]

    def get_task_name(self, obj):
        # اگر این گلوگاه به یک تسک وصل است، آخرین نام آن را پیدا کن
        if obj.task:
            latest_tv = obj.task.versions.filter(is_deleted=False).order_by('-revision__number').first()
            return latest_tv.title if latest_tv else "تسک نامشخص"
        return ""

    def get_wbs_node_name(self, obj):
        # پیدا کردن نام پوشه WBS
        if obj.task:
            latest_tv = obj.task.versions.filter(is_deleted=False).order_by('-revision__number').select_related('wbs_node').first()
            return latest_tv.wbs_node.title if (latest_tv and latest_tv.wbs_node) else "-"
        return ""

class ManagementReportSerializer(serializers.ModelSerializer):
    # برای اینکه آیتم‌های گلوگاه به صورت Nested (توکار) داخل گزارش نمایش داده شوند
    bottlenecks = CuratedBottleneckSerializer(many=True, read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = ManagementReport
        fields = [
            'id', 'project', 'project_name', 'created_by', 'created_by_username',
            'overall_progress', 'planner_summary', 'is_published',
            'created_at', 'updated_at', 'bottlenecks'
        ]

