from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("ktcPlanning", "0027_cost_transaction_milestone_allocation"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentmilestone",
            name="manual_approved",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="paymentmilestone",
            name="manual_approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentmilestone",
            name="manual_approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
