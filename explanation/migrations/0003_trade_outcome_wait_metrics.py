from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('explanation', '0002_trade_decision_v2'),
    ]

    operations = [
        migrations.AlterField(
            model_name='explanationtradeoutcome',
            name='direction_hit',
            field=models.BooleanField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='explanationtradeoutcome',
            name='is_actionable',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='explanationtradeoutcome',
            name='outcome_kind',
            field=models.CharField(default='wait_observed', max_length=32),
        ),
        migrations.AddField(
            model_name='explanationtradeoutcome',
            name='missed_opportunity',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='explanationtradeoutcome',
            name='horizon_return_pct',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
