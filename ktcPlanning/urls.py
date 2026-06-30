from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProjectViewSet,
    RevisionViewSet,
    WbsNodeViewSet,
    ActivityNodeViewSet,
    DependencyViewSet, TaskReportLogViewSet, TaskChatMessageViewSet, TaskRoleViewSet, ResourceHistogramView,
    ImportMSPView, ResourcePoolViewSet, AssignmentViewSet, ResourceRateViewSet, ResourceExceptionViewSet,
    ResourceSkillMappingViewSet, ResourceViewSet, ResourceSkillViewSet, ResourceRoleViewSet, PersonalTaskViewSet,
    VarianceReportViewSet, CalendarViewSet, ProjectViewerViewSet, SystemSettingsView, ExpenseTypeViewSet,
    UnitOfMeasureViewSet, CostTransactionViewSet, TaskViewSet
)

# ایجاد یک نمونه از روتور پیش‌فرض DRF
router = DefaultRouter()

# ثبت ویوها در روتور
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'project-viewers', ProjectViewerViewSet, basename='project-viewer')
router.register(r'calendars', CalendarViewSet, basename='calendar')
router.register(r'revisions', RevisionViewSet, basename='revision')
router.register(r'wbs-nodes', WbsNodeViewSet, basename='wbs-node')
router.register(r'activities', ActivityNodeViewSet, basename='activity')
router.register(r'dependencies', DependencyViewSet, basename='dependency')
router.register(r'task-reports', TaskReportLogViewSet, basename='task-report')
router.register(r'task-chats', TaskChatMessageViewSet, basename='task-chat')
router.register(r'task-roles', TaskRoleViewSet, basename='task-role')

router.register(r'resource-pools', ResourcePoolViewSet, basename='resource-pool')
router.register(r'resource-roles', ResourceRoleViewSet, basename='resource-role')
router.register(r'resource-skills', ResourceSkillViewSet, basename='resource-skill')
router.register(r'resources', ResourceViewSet, basename='resource')
router.register(r'resource-skill-mappings', ResourceSkillMappingViewSet, basename='resource-skill-mapping')
router.register(r'resource-exceptions', ResourceExceptionViewSet, basename='resource-exception')
router.register(r'resource-rates', ResourceRateViewSet, basename='resource-rate')
router.register(r'assignments', AssignmentViewSet, basename='assignment')
router.register(r'personal-tasks', PersonalTaskViewSet, basename='personal-tasks')
router.register(r'variance-reports', VarianceReportViewSet, basename='variance-report')
router.register(r'expense-types', ExpenseTypeViewSet, basename='expense-type')
router.register(r'units-of-measure', UnitOfMeasureViewSet, basename='unit-of-measure')
router.register(r'cost-transactions', CostTransactionViewSet, basename='cost-transaction')
router.register(r'tasks', TaskViewSet, basename='task')
# مسیرهای نهایی اپلیکیشن
urlpatterns = [
    path('', include(router.urls)),
    path("import-msp/", ImportMSPView.as_view(), name="import-msp"),
    path("system-settings/", SystemSettingsView.as_view(), name="system-settings"),
]