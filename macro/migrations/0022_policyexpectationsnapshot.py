from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0021_delete_macroconclusionsnapshot'),
    ]

    operations = [
        migrations.CreateModel(
            name='PolicyExpectationSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('as_of', models.DateTimeField(db_index=True)),
                ('central_bank', models.CharField(db_index=True, default='FED', max_length=16)),
                ('effective_rate', models.FloatField(blank=True, null=True)),
                ('target_lower', models.FloatField(blank=True, null=True)),
                ('target_upper', models.FloatField(blank=True, null=True)),
                ('implied_next_meeting_delta_bp', models.FloatField(blank=True, null=True)),
                ('implied_3m_delta_bp', models.FloatField(blank=True, null=True)),
                ('implied_6m_delta_bp', models.FloatField(blank=True, null=True)),
                ('implied_12m_delta_bp', models.FloatField(blank=True, null=True)),
                ('rate_shock_1d_bp', models.FloatField(blank=True, null=True)),
                ('rate_shock_5d_bp', models.FloatField(blank=True, null=True)),
                ('policy_bias', models.CharField(default='neutral', max_length=32)),
                ('data_quality', models.FloatField(default=0.0)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-as_of'],
                'indexes': [
                    models.Index(fields=['central_bank', '-as_of'], name='macro_polic_central_feb443_idx'),
                    models.Index(fields=['policy_bias', '-as_of'], name='macro_polic_policy__08d91b_idx'),
                ],
            },
        ),
    ]
