from django.contrib import admin
from .models import *


admin.site.register(Project)
admin.site.register(WBSNode)
admin.site.register(FundingSource)
admin.site.register(BudgetAllocation)


admin.site.register(TaskFinancialPlan)
admin.site.register(PaymentMilestone)
admin.site.register(PaymentTransaction)
