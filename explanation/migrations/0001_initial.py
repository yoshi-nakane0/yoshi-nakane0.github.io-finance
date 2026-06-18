# Generated manually for the Explanation decision layer.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='ExplanationSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('as_of', models.DateTimeField(db_index=True)),
                ('final_label', models.CharField(max_length=64)),
                ('final_stance', models.CharField(db_index=True, max_length=64)),
                ('action_posture', models.CharField(max_length=64)),
                ('confidence_score', models.IntegerField()),
                ('confidence_grade', models.CharField(max_length=16)),
                ('macro_bias', models.CharField(max_length=64)),
                ('basecalc_bias', models.CharField(max_length=64)),
                ('alignment_status', models.CharField(max_length=32)),
                ('data_quality_score', models.IntegerField()),
                ('audit_level', models.CharField(max_length=32)),
                ('audit_items', models.JSONField(default=list)),
                ('scenario', models.JSONField(default=dict)),
                ('evidence', models.JSONField(default=list)),
                ('source_snapshots', models.JSONField(default=dict)),
                ('score_breakdown', models.JSONField(default=dict)),
                ('version', models.CharField(default='explanation_v1', max_length=32)),
            ],
            options={
                'ordering': ['-as_of', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ExplanationOutcome',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('horizon', models.CharField(max_length=16)),
                ('evaluated_at', models.DateTimeField()),
                ('price_at_evaluation', models.FloatField()),
                ('realized_return_pct', models.FloatField()),
                ('direction_hit', models.BooleanField()),
                ('invalidation_hit', models.BooleanField(default=False)),
                ('explanation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='explanation.explanationsnapshot')),
            ],
            options={
                'ordering': ['-evaluated_at'],
            },
        ),
        migrations.AddIndex(
            model_name='explanationsnapshot',
            index=models.Index(fields=['-as_of'], name='explanation_as_of_idx'),
        ),
        migrations.AddIndex(
            model_name='explanationsnapshot',
            index=models.Index(fields=['final_stance', '-as_of'], name='explanation_stance_idx'),
        ),
        migrations.AddIndex(
            model_name='explanationsnapshot',
            index=models.Index(fields=['audit_level', '-as_of'], name='explanation_audit_idx'),
        ),
        migrations.AddIndex(
            model_name='explanationoutcome',
            index=models.Index(fields=['horizon', '-evaluated_at'], name='explanation_horizon_idx'),
        ),
        migrations.AddIndex(
            model_name='explanationoutcome',
            index=models.Index(fields=['direction_hit', '-evaluated_at'], name='explanation_hit_idx'),
        ),
    ]
