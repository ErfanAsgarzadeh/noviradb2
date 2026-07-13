from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ktcPlanning', '0015_globallevelingrun_priority_rules_project_priority'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='parent_project',
            field=models.ForeignKey(
                blank=True,
                help_text='برای ساخت ساختار پروژه/زیرپروژه شبیه Primavera استفاده می‌شود.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='subprojects',
                to='ktcPlanning.project',
                verbose_name='پروژه مادر',
            ),
        ),
    ]
