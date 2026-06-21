from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('explanation', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='explanationsnapshot',
            name='trade_decision',
            field=models.JSONField(default=dict),
        ),
        migrations.AlterField(
            model_name='explanationsnapshot',
            name='version',
            field=models.CharField(default='explanation_v2', max_length=32),
        ),
        migrations.CreateModel(
            name='ExplanationTradeOutcome',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('horizon', models.CharField(max_length=16)),
                ('evaluated_at', models.DateTimeField()),
                ('selected_side', models.CharField(max_length=16)),
                ('decision_type', models.CharField(max_length=32)),
                ('trend_or_reversal', models.CharField(blank=True, default='', max_length=32)),
                ('entry_price', models.FloatField(blank=True, null=True)),
                ('target_1_price', models.FloatField(blank=True, null=True)),
                ('target_1_hit', models.BooleanField(default=False)),
                ('target_2_price', models.FloatField(blank=True, null=True)),
                ('target_2_hit', models.BooleanField(default=False)),
                ('stop_price', models.FloatField(blank=True, null=True)),
                ('stop_hit', models.BooleanField(default=False)),
                ('max_favorable_excursion', models.FloatField(blank=True, null=True)),
                ('max_adverse_excursion', models.FloatField(blank=True, null=True)),
                ('exit_price', models.FloatField(blank=True, null=True)),
                ('exit_reason', models.CharField(blank=True, default='', max_length=32)),
                ('realized_rr', models.FloatField(blank=True, null=True)),
                ('expected_rr', models.FloatField(blank=True, null=True)),
                ('direction_hit', models.BooleanField(default=False)),
                ('macro_regime', models.CharField(blank=True, default='', max_length=64)),
                ('technical_regime', models.CharField(blank=True, default='', max_length=64)),
                ('confidence_bucket', models.CharField(blank=True, default='', max_length=16)),
                ('sample_count_at_decision', models.IntegerField(blank=True, null=True)),
                ('explanation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='explanation.explanationsnapshot')),
            ],
            options={
                'ordering': ['-evaluated_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='explanationtradeoutcome',
            constraint=models.UniqueConstraint(fields=('explanation', 'horizon'), name='unique_explanation_trade_horizon'),
        ),
        migrations.AddIndex(
            model_name='explanationtradeoutcome',
            index=models.Index(fields=['selected_side', '-evaluated_at'], name='explanation_trade_side_idx'),
        ),
        migrations.AddIndex(
            model_name='explanationtradeoutcome',
            index=models.Index(fields=['decision_type', '-evaluated_at'], name='explanation_trade_type_idx'),
        ),
        migrations.AddIndex(
            model_name='explanationtradeoutcome',
            index=models.Index(fields=['confidence_bucket', '-evaluated_at'], name='explanation_trade_conf_idx'),
        ),
    ]
