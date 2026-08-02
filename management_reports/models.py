# management_reports/models.py

from django.db import models
from django.contrib.auth import get_user_model
# ایمپورت کردن مدل‌های اصلی از اپلیکیشن برنامه‌ریزی
from ktcPlanning.models import Project, Task 

User = get_user_model()

class ManagementReport(models.Model):
    # ارتباط با مدل پروژه در یک اپلیکیشن دیگر
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="management_reports")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    overall_progress = models.DecimalField(max_digits=5, decimal_places=2)
    planner_summary = models.TextField(blank=True)
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class CuratedBottleneck(models.Model):
    report = models.ForeignKey(ManagementReport, on_delete=models.CASCADE, related_name="bottlenecks")
    task = models.ForeignKey('ktcPlanning.Task', null=True, blank=True, on_delete=models.SET_NULL)

    issue_type = models.CharField(max_length=100)
    description = models.TextField(verbose_name="شرح مشکل (تولید شده توسط سیستم یا کاربر)")
    severity = models.CharField(max_length=20, default='high')

    # --- فیلدهای جدید ---
    planner_remark = models.TextField(blank=True, verbose_name="تحلیل و راهکار برنامه‌ریز")
    is_manual = models.BooleanField(default=False, verbose_name="آیا دستی اضافه شده؟")

    def __str__(self):
        return f"{self.issue_type} - {self.report.project.name}"


class ManagementReportComment(models.Model):
    bottleneck = models.ForeignKey(CuratedBottleneck, on_delete=models.CASCADE, related_name="manager_comments")
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="management_report_comments")
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Comment on {self.bottleneck_id} by {self.author}"
