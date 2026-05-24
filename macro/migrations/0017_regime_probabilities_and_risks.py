from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0016_observation_time_aware_z_scores_forecastsnapshot'),
    ]

    operations = [
        migrations.AlterField(
            model_name='regimesnapshot',
            name='model_version',
            field=models.CharField(default='regime_v1', max_length=64),
        ),
        migrations.AddField(
            model_name='regimesnapshot',
            name='regime_probabilities',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='regimesnapshot',
            name='risk_probabilities',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
