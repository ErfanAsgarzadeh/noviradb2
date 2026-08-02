from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('management_reports', '0003_managementreport_updated_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='ManagementReportComment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='management_report_comments', to=settings.AUTH_USER_MODEL)),
                ('bottleneck', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='manager_comments', to='management_reports.curatedbottleneck')),
            ],
            options={
                'ordering': ['created_at', 'id'],
            },
        ),
    ]
