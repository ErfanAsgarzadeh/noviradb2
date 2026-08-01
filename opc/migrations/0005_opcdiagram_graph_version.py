from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('opc', '0004_alter_opcdiagram_unique_together'),
    ]

    operations = [
        migrations.AddField(
            model_name='opcdiagram',
            name='graph_version',
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
