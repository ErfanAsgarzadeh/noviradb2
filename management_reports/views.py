from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Sum, F, FloatField, ExpressionWrapper
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404

# ایمپورت مدل‌های هسته برنامه‌ریزی
from ktcPlanning.models import Project, TaskReportLog, VarianceReport, TaskVersion

# ایمپورت مدل‌ها و سریالایزرهای همین اپلیکیشن
from .models import ManagementReport, CuratedBottleneck
from .serializers import ManagementReportSerializer


class PreparePlannerReportAPI(APIView):
    """
    متد GET: خواندن دیتای خام از هسته سیستم و تولید پیش‌نویس (Draft) برای برنامه‌ریز
    """

    def get(self, request, project_id):
        project = get_object_or_404(Project, id=project_id, is_deleted=False)
        latest_revision = project.revisions.filter(is_deleted=False).order_by('-number').first()

        auto_progress = 0
        active_task_versions_dict = {}

        if latest_revision:
            # ۱. استخراج تسک‌های فعال در این ریویژن
            active_task_versions = TaskVersion.objects.filter(
                task__project=project,
                revision=latest_revision,
                is_deleted=False
            ).select_related('wbs_node')

            # ۲. محاسبه پیشرفت خودکار سیستم با استفاده از میانگین وزنی
            progress_stats = active_task_versions.aggregate(
                total_weight=Sum('weight'),
                weighted_progress_sum=Sum(
                    ExpressionWrapper(
                        F('weight') * Coalesce('actual__progress', 0.0),
                        output_field=FloatField()
                    )
                )
            )

            total_weight = progress_stats['total_weight'] or 0
            weighted_progress_sum = progress_stats['weighted_progress_sum'] or 0

            if total_weight > 0:
                auto_progress = round(weighted_progress_sum / total_weight, 2)

            # ۳. ذخیره موقت نام‌ها برای چسباندن به گزارش‌های خطا (جلوگیری از کوئری‌های تکراری)
            for tv in active_task_versions:
                active_task_versions_dict[tv.task_id] = {
                    "task_name": tv.title,
                    "wbs_name": tv.wbs_node.title if tv.wbs_node else "بدون گروه"
                }

        suggested_bottlenecks = []
        available_highlights = []

        # ۴. دریافت بلاکرهای انسانی از کارگاه
        problematic_logs = TaskReportLog.objects.filter(
            task__project=project,
            status__in=['blocked', 'at-risk']
        ).order_by('task', '-timestamp').distinct('task')

        for log in problematic_logs:
            tv_info = active_task_versions_dict.get(log.task_id, {})
            suggested_bottlenecks.append({
                "task_id": str(log.task_id),
                "task_name": tv_info.get("task_name", "تسک نامشخص"),
                "wbs_node_name": tv_info.get("wbs_name", "-"),
                "issue_type": "گزارش خرابی/مسدودی از کارگاه",
                "description": log.blockers or log.notes,
                "severity": "high" if log.status == "blocked" else "medium",
                "is_manual": False,
                "planner_remark": ""
            })

        # ۵. دریافت گزارش‌های عادی و موفق (برای انتخاب اختیاری توسط برنامه‌ریز)
        normal_logs = TaskReportLog.objects.filter(
            task__project=project,
            status__in=['on-track', 'completed']
        ).order_by('task', '-timestamp').distinct('task')

        for log in normal_logs:
            tv_info = active_task_versions_dict.get(log.task_id, {})
            available_highlights.append({
                "task_id": str(log.task_id),
                "task_name": tv_info.get("task_name", "تسک نامشخص"),
                "wbs_node_name": tv_info.get("wbs_name", "-"),
                "issue_type": "گزارش پیشرفت/تکمیل",
                "description": log.notes,
                "severity": "low",
                "is_manual": True,
                "planner_remark": ""
            })

        # ۶. دریافت انحرافات EVM سیستمی
        if latest_revision:
            critical_variances = VarianceReport.objects.filter(
                task__project=project,
                revision=latest_revision,
                action_required=True
            )
            for var in critical_variances:
                tv_info = active_task_versions_dict.get(var.task_id, {})
                suggested_bottlenecks.append({
                    "task_id": str(var.task_id),
                    "task_name": tv_info.get("task_name", "تسک نامشخص"),
                    "wbs_node_name": tv_info.get("wbs_name", "-"),
                    "issue_type": "انحراف شاخص‌های زمانی/هزینه‌ای (EVM)",
                    "description": f"شاخص عملکرد زمانی (SPI): {var.spi} | شاخص عملکرد هزینه‌ای (CPI): {var.cpi}",
                    "severity": "high",
                    "is_manual": False,
                    "planner_remark": ""
                })

        return Response({
            "project_id": project.id,
            "project_name": project.name,
            "suggested_overall_progress": auto_progress,
            "suggested_bottlenecks": suggested_bottlenecks,
            "available_highlights": available_highlights
        }, status=status.HTTP_200_OK)


class SaveManagementReportAPI(APIView):
    """
    متد POST: دریافت اطلاعات ویرایش‌شده توسط برنامه‌ریز و ذخیره/انتشار گزارش
    """

    def post(self, request):
        data = request.data
        project_id = data.get("project_id")

        # ساخت رکورد اصلی گزارش
        report = ManagementReport.objects.create(
            project_id=project_id,
            created_by=request.user,
            overall_progress=data.get("overall_progress", 0),
            planner_summary=data.get("planner_summary", ""),
            is_published=data.get("is_published", False)
        )

        # استخراج و ذخیره آیتم‌های گلوگاه (نام تسک و WBS در دیتابیس ذخیره نمی‌شوند)
        bottlenecks_data = data.get("bottlenecks", [])
        bottlenecks_to_create = [
            CuratedBottleneck(
                report=report,
                task_id=item.get("task_id") if item.get("task_id") else None,
                issue_type=item.get("issue_type", "نامشخص"),
                description=item.get("description", ""),
                severity=item.get("severity", "high"),
                planner_remark=item.get("planner_remark", ""),
                is_manual=item.get("is_manual", False)
            ) for item in bottlenecks_data
        ]

        CuratedBottleneck.objects.bulk_create(bottlenecks_to_create)

        return Response({
            "message": "گزارش با موفقیت ذخیره شد.",
            "report_id": report.id
        }, status=status.HTTP_201_CREATED)


class ExecutiveDashboardAPI(APIView):
    """
    متد GET: ارائه داشبورد نهایی و تایید شده به مدیر ارشد
    """

    def get(self, request):
        active_projects = Project.objects.filter(is_deleted=False)
        summary_data = []

        for project in active_projects:
            # فقط دریافت گزارش‌هایی که توسط برنامه‌ریز منتشر شده‌اند
            latest_published_report = project.management_reports.filter(
                is_published=True
            ).order_by('-created_at').first()

            if latest_published_report:
                serializer = ManagementReportSerializer(latest_published_report)
                summary_data.append(serializer.data)

        return Response({
            "total_active_projects": len(summary_data),
            "executive_dashboard": summary_data
        }, status=status.HTTP_200_OK)