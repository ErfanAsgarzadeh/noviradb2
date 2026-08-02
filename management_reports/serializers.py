from rest_framework import serializers
from .models import ManagementReport, CuratedBottleneck, ManagementReportComment


class ManagementReportCommentSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source='author.username', read_only=True)

    class Meta:
        model = ManagementReportComment
        fields = ['id', 'message', 'author', 'author_username', 'created_at']
        read_only_fields = fields


class CuratedBottleneckSerializer(serializers.ModelSerializer):
    # تعریف فیلدهای محاسباتی و داینامیک (فقط برای نمایش)
    task_id = serializers.SerializerMethodField()
    task_name = serializers.SerializerMethodField()
    wbs_node_name = serializers.SerializerMethodField()
    manager_comments = ManagementReportCommentSerializer(many=True, read_only=True)

    class Meta:
        model = CuratedBottleneck
        fields = [
            'id', 'task', 'task_id', 'task_name', 'wbs_node_name',
            'issue_type', 'description', 'severity',
            'is_manual', 'planner_remark', 'manager_comments'
        ]

    def get_task_id(self, obj):
        return str(obj.task_id) if obj.task_id else None

    def get_task_name(self, obj):
        # اگر این گلوگاه به یک تسک وصل است، آخرین نام آن را پیدا کن
        if obj.task:
            latest_tv = obj.task.versions.filter(
                revision_id=obj.task.project.current_execution_revision_id, is_deleted=False
            ).first()
            return latest_tv.title if latest_tv else "تسک نامشخص"
        return ""

    def get_wbs_node_name(self, obj):
        # پیدا کردن نام پوشه WBS
        if obj.task:
            latest_tv = obj.task.versions.filter(
                revision_id=obj.task.project.current_execution_revision_id, is_deleted=False
            ).select_related('wbs_node').first()
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

