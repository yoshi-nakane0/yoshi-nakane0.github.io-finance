from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('earning', '0006_remove_earningsevent_eps_4q_ago_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='earningsevent',
            name='expectation_score',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
