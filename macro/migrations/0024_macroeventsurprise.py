from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0023_indicatorseries_macroforecastrun_macroscenario_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='MacroEventSurprise',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_date', models.DateField()),
                ('event_name', models.CharField(max_length=128)),
                ('category', models.CharField(default='macro', max_length=32)),
                ('actual', models.FloatField(blank=True, null=True)),
                ('consensus', models.FloatField(blank=True, null=True)),
                ('previous', models.FloatField(blank=True, null=True)),
                ('surprise', models.FloatField(blank=True, null=True)),
                ('revision', models.FloatField(blank=True, null=True)),
                ('unit', models.CharField(blank=True, max_length=16)),
                ('direction', models.CharField(default='unknown', max_length=32)),
                ('market_impact', models.TextField(blank=True)),
                ('next_forecast_impact', models.TextField(blank=True)),
                ('source', models.CharField(default='manual_consensus', max_length=64)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-event_date', 'event_name'],
            },
        ),
        migrations.AddIndex(
            model_name='macroeventsurprise',
            index=models.Index(fields=['category', '-event_date'], name='macro_macro_categor_b8f5f2_idx'),
        ),
        migrations.AddIndex(
            model_name='macroeventsurprise',
            index=models.Index(fields=['direction', '-event_date'], name='macro_macro_directi_f878de_idx'),
        ),
        migrations.AddConstraint(
            model_name='macroeventsurprise',
            constraint=models.UniqueConstraint(fields=('event_date', 'event_name', 'source'), name='uq_macro_event_surprise_identity'),
        ),
    ]
