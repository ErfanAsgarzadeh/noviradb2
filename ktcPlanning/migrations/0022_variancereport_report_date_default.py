from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('ktcPlanning', '0021_repair_legacy_revision_spine'),
    ]

    operations = [
        migrations.AlterField(
            model_name='variancereport',
            name='report_date',
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
    ]