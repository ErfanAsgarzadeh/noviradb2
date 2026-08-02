from django.urls import path
from .views import PreparePlannerReportAPI, SaveManagementReportAPI, ExecutiveDashboardAPI, ManagementReportCommentAPI

app_name = 'management_reports'

urlpatterns = [
    # API برای برنامه‌ریز (خواندن دیتای اولیه سیستم)
    path('planner/draft/<uuid:project_id>/', PreparePlannerReportAPI.as_view(), name='prepare-draft'),

    # API برای برنامه‌ریز (ذخیره نسخه نهایی)
    path('planner/save/', SaveManagementReportAPI.as_view(), name='save-report'),

    # API برای مدیر ارشد (مشاهده داشبورد نهایی)
    path('executive/dashboard/', ExecutiveDashboardAPI.as_view(), name='executive-dashboard'),
    path('executive/bottlenecks/<int:bottleneck_id>/comments/', ManagementReportCommentAPI.as_view(), name='management-report-comment'),
]
