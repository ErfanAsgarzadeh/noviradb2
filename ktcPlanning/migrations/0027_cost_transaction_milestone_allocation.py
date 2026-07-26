from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("ktcPlanning", "0026_move_financial_plan_link_to_cost_transaction"),
    ]

    operations = [
        migrations.CreateModel(
            name="CostTransactionMilestoneAllocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("allocated_amount", models.DecimalField(decimal_places=2, max_digits=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cost_transaction",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="milestone_allocations",
                        to="ktcPlanning.costtransaction",
                    ),
                ),
                (
                    "milestone",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cost_allocations",
                        to="ktcPlanning.paymentmilestone",
                    ),
                ),
            ],
            options={
                "ordering": ["cost_transaction", "milestone__sequence", "milestone_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="costtransactionmilestoneallocation",
            constraint=models.CheckConstraint(condition=models.Q(("allocated_amount__gt", 0)), name="cost_milestone_alloc_amount_positive"),
        ),
        migrations.AddConstraint(
            model_name="costtransactionmilestoneallocation",
            constraint=models.UniqueConstraint(fields=("cost_transaction", "milestone"), name="unique_cost_transaction_milestone_allocation"),
        ),
        migrations.AddIndex(
            model_name="costtransactionmilestoneallocation",
            index=models.Index(fields=["cost_transaction", "milestone"], name="ktcPlanning_cost_tr_8169c8_idx"),
        ),
        migrations.AddIndex(
            model_name="costtransactionmilestoneallocation",
            index=models.Index(fields=["milestone"], name="ktcPlanning_milesto_b33ebd_idx"),
        ),
    ]
