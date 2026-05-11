from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0009_indicator_value_max_indicator_value_min'),
    ]

    operations = [
        migrations.AlterField(
            model_name='indicator',
            name='source',
            field=models.CharField(
                choices=[
                    ('fred', 'FRED'),
                    ('cboe', 'Cboe'),
                    ('finra', 'FINRA'),
                    ('aaii', 'AAII'),
                    ('naaim', 'NAAIM'),
                    ('yfinance', 'Yahoo Finance'),
                    ('yfinance_daily', 'Yahoo Finance (日次)'),
                ],
                default='fred',
                max_length=16,
            ),
        ),
    ]
