from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0012_phase6_volatility_extensions'),
    ]

    operations = [
        migrations.AlterField(
            model_name='priceobservation',
            name='ticker',
            field=models.CharField(
                choices=[
                    ('N225', '日経225'),
                    ('GSPC', 'S&P 500'),
                    ('DJI', 'NYダウ'),
                    ('IXIC', 'NASDAQ'),
                ],
                max_length=16,
            ),
        ),
    ]
