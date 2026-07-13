from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ktcPlanning', '0016_project_parent_project'),
    ]

    operations = [
        migrations.CreateModel(
            name='SubprojectDependency',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('direction', models.CharField(choices=[('TASK_TO_SUBPROJECT', 'Task to Subproject'), ('SUBPROJECT_TO_TASK', 'Subproject to Task')], max_length=24)),
                ('dependency_type', models.CharField(choices=[('FS', 'Finish-Start'), ('SS', 'Start-Start'), ('FF', 'Finish-Finish'), ('SF', 'Start-Finish')], default='FS', max_length=2)),
                ('lag_hours', models.IntegerField(default=0)),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subproject_dependencies', to='ktcPlanning.revision')),
                ('subproject', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parent_schedule_dependencies', to='ktcPlanning.project')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subproject_dependencies', to='ktcPlanning.task')),
            ],
            options={
                'unique_together': {('revision', 'task', 'subproject', 'direction')},
            },
        ),
    ]
