from django.db import migrations, models
import django.db.models.deletion


def move_plan_links_to_cost_transactions(apps, schema_editor):
    TaskFinancialPlan = apps.get_model("ktcPlanning", "TaskFinancialPlan")
    CostTransaction = apps.get_model("ktcPlanning", "CostTransaction")
    for plan in TaskFinancialPlan.objects.exclude(cost_transaction_id__isnull=True).iterator():
        CostTransaction.objects.filter(pk=plan.cost_transaction_id, financial_plan__isnull=True).update(financial_plan_id=plan.pk)


def move_cost_transaction_links_to_plans(apps, schema_editor):
    TaskFinancialPlan = apps.get_model("ktcPlanning", "TaskFinancialPlan")
    CostTransaction = apps.get_model("ktcPlanning", "CostTransaction")
    for transaction in CostTransaction.objects.exclude(financial_plan_id__isnull=True).iterator():
        TaskFinancialPlan.objects.filter(pk=transaction.financial_plan_id, cost_transaction__isnull=True).update(cost_transaction_id=transaction.pk)


class Migration(migrations.Migration):

    dependencies = [
        ("ktcPlanning", "0025_costtransaction_direct_cost_amount_semantics"),
    ]

    operations = [
        migrations.AddField(
            model_name="costtransaction",
            name="financial_plan",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cost_transactions",
                to="ktcPlanning.taskfinancialplan",
            ),
        ),
        migrations.RunPython(move_plan_links_to_cost_transactions, move_cost_transaction_links_to_plans),
        migrations.RemoveField(
            model_name="taskfinancialplan",
            name="cost_transaction",
        ),
    ]
