from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProjectViewSet,
    RevisionViewSet,
    WbsNodeViewSet,
    ActivityNodeViewSet,
    DependencyViewSet, SubprojectDependencyViewSet, TaskReportLogViewSet, TaskChatMessageViewSet, TaskRoleViewSet, ResourceHistogramView,
    ImportMSPView, ExportMSPView, ResourcePoolViewSet, AssignmentViewSet, ResourceRateViewSet, ResourceExceptionViewSet,
    ResourceSkillMappingViewSet, ResourceViewSet, ResourceSkillViewSet, ResourceRoleViewSet, PersonalTaskViewSet,
    VarianceReportViewSet, CalendarViewSet, ProjectViewerViewSet, SystemSettingsView, ExpenseTypeViewSet,
    UnitOfMeasureViewSet, FundingSourceViewSet, BudgetAllocationViewSet, BudgetBorrowViewSet, UnfundedForecastCostViewSet,
    CostTransactionViewSet, TaskDeliveryAttachmentViewSet, TaskDeliveryViewSet, TaskFinancialPlanViewSet, PaymentMilestoneViewSet, PaymentTransactionViewSet, TaskViewSet, ResourceLevelingPlanViewSet,
    FinancialControlConvertView, FinancialControlExchangeRateView, FinancialControlGenerateCostSnapshotsView, FinancialControlView
)

# ایجاد یک نمونه از روتور پیش‌فرض DRF
class OptionalSlashRouter(DefaultRouter):
    """Accept DRF endpoints with or without a trailing slash.

    Some cached frontend bundles still call endpoints like
    /api/planning/calendars?templates=true. Keeping both forms valid prevents
    harmless trailing-slash differences from breaking active testers.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trailing_slash = '/?'


router = OptionalSlashRouter()

# ثبت ویوها در روتور
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'project-viewers', ProjectViewerViewSet, basename='project-viewer')
router.register(r'calendars', CalendarViewSet, basename='calendar')
router.register(r'revisions', RevisionViewSet, basename='revision')
router.register(r'wbs-nodes', WbsNodeViewSet, basename='wbs-node')
router.register(r'activities', ActivityNodeViewSet, basename='activity')
router.register(r'dependencies', DependencyViewSet, basename='dependency')
router.register(r'subproject-dependencies', SubprojectDependencyViewSet, basename='subproject-dependency')
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
router.register(r'funding-sources', FundingSourceViewSet, basename='funding-source')
router.register(r'budget-allocations', BudgetAllocationViewSet, basename='budget-allocation')
router.register(r'budget-borrows', BudgetBorrowViewSet, basename='budget-borrow')
router.register(r'unfunded-forecast-costs', UnfundedForecastCostViewSet, basename='unfunded-forecast-cost')
router.register(r'cost-transactions', CostTransactionViewSet, basename='cost-transaction')
router.register(r'task-deliveries', TaskDeliveryViewSet, basename='task-delivery')
router.register(r'task-delivery-attachments', TaskDeliveryAttachmentViewSet, basename='task-delivery-attachment')
router.register(r'task-financial-plans', TaskFinancialPlanViewSet, basename='task-financial-plan')
router.register(r'payment-milestones', PaymentMilestoneViewSet, basename='payment-milestone')
router.register(r'payment-transactions', PaymentTransactionViewSet, basename='payment-transaction')
router.register(r'tasks', TaskViewSet, basename='task')
router.register(r'resource-leveling-plans', ResourceLevelingPlanViewSet, basename='resource-leveling-plan')
# مسیرهای نهایی اپلیکیشن
urlpatterns = [
    path('financial-control/', FinancialControlView.as_view(), name='financial-control'),
    path('financial-control/convert/', FinancialControlConvertView.as_view(), name='financial-control-convert'),
    path('financial-control/exchange-rates/', FinancialControlExchangeRateView.as_view(), name='financial-control-exchange-rate'),
    path('financial-control/generate-cost-snapshots/', FinancialControlGenerateCostSnapshotsView.as_view(), name='financial-control-generate-cost-snapshots'),
    path('revisions/<int:revision_id>/resource-histogram/', ResourceHistogramView.as_view(), name='resource-histogram'),
    path('', include(router.urls)),
    path("import-msp/", ImportMSPView.as_view(), name="import-msp"),
    path("export-msp/<int:revision_id>/", ExportMSPView.as_view(), name="export-msp"),
    path("system-settings/", SystemSettingsView.as_view(), name="system-settings"),
]
