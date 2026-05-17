from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0013_priceobservation_more_tickers'),
    ]

    operations = [
        migrations.AddField(
            model_name='regimesnapshot',
            name='rule_strength',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='regimesnapshot',
            name='data_quality',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='regimesnapshot',
            name='evidence',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='regimesnapshot',
            name='warnings',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='regimesnapshot',
            name='model_version',
            field=models.CharField(default='regime_v1', max_length=32),
        ),
    ]
