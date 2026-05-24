import math
from bisect import bisect_left

from django.db import migrations, models


MIN_SAMPLES_FOR_STATS = 24


def _stats(values):
    if len(values) < MIN_SAMPLES_FOR_STATS:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return mean, math.sqrt(variance)


def _z_score(value, values):
    mean, std = _stats(values)
    if std <= 0:
        return None
    return (value - mean) / std


def _safe_years_ago(value, years):
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _apply_scores(Observation, rows):
    dates = [row.observation_date for row in rows]
    values = [row.value for row in rows]
    updates = []
    for idx, row in enumerate(rows):
        available_values = values[:idx + 1]
        expanding = _z_score(row.value, available_values)

        rolling_10y_start = bisect_left(
            dates,
            _safe_years_ago(row.observation_date, 10),
            0,
            idx + 1,
        )
        rolling_5y_start = bisect_left(
            dates,
            _safe_years_ago(row.observation_date, 5),
            0,
            idx + 1,
        )
        row.expanding_z_score = expanding
        row.rolling_10y_z_score = _z_score(
            row.value,
            values[rolling_10y_start:idx + 1],
        )
        row.rolling_5y_z_score = _z_score(
            row.value,
            values[rolling_5y_start:idx + 1],
        )
        row.deviation_from_long_term = expanding
        updates.append(row)
    if updates:
        Observation.objects.bulk_update(
            updates,
            fields=[
                'deviation_from_long_term',
                'expanding_z_score',
                'rolling_10y_z_score',
                'rolling_5y_z_score',
            ],
            batch_size=1000,
        )


def backfill_time_aware_z_scores(apps, schema_editor):
    Observation = apps.get_model('macro', 'Observation')
    current_indicator_id = None
    current_rows = []
    queryset = (
        Observation.objects
        .order_by('indicator_id', 'observation_date')
        .only('id', 'indicator_id', 'observation_date', 'value')
        .iterator(chunk_size=1000)
    )
    for row in queryset:
        if current_indicator_id is None:
            current_indicator_id = row.indicator_id
        if row.indicator_id != current_indicator_id:
            _apply_scores(Observation, current_rows)
            current_indicator_id = row.indicator_id
            current_rows = []
        current_rows.append(row)
    if current_rows:
        _apply_scores(Observation, current_rows)


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0015_add_usrec_indicator'),
    ]

    operations = [
        migrations.AddField(
            model_name='observation',
            name='expanding_z_score',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='observation',
            name='rolling_10y_z_score',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='observation',
            name='rolling_5y_z_score',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='ForecastSnapshot',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('as_of_date', models.DateField()),
                ('model_version', models.CharField(max_length=64)),
                ('target', models.CharField(max_length=32)),
                ('horizon', models.CharField(max_length=32)),
                ('prediction_value', models.FloatField()),
                (
                    'prediction_interval',
                    models.JSONField(blank=True, null=True),
                ),
                ('features_hash', models.CharField(blank=True, max_length=64)),
                ('realized_value', models.FloatField(blank=True, null=True)),
                ('error', models.FloatField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-as_of_date', '-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='forecastsnapshot',
            constraint=models.UniqueConstraint(
                fields=('as_of_date', 'model_version', 'target', 'horizon'),
                name='uq_forecast_snapshot_identity',
            ),
        ),
        migrations.AddIndex(
            model_name='forecastsnapshot',
            index=models.Index(
                fields=['target', 'as_of_date'],
                name='macro_forec_target_30ee36_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='forecastsnapshot',
            index=models.Index(
                fields=['model_version', 'as_of_date'],
                name='macro_forec_model_v_f6e4c4_idx',
            ),
        ),
        migrations.RunPython(
            backfill_time_aware_z_scores,
            migrations.RunPython.noop,
        ),
    ]
