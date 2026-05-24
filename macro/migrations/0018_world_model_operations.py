from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0017_regime_probabilities_and_risks'),
    ]

    operations = [
        migrations.CreateModel(
            name='RawArchiveManifest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('reason', models.CharField(max_length=64)),
                ('storage_backend', models.CharField(default='local', max_length=32)),
                ('path', models.TextField()),
                ('row_count', models.IntegerField(default=0)),
                ('observation_count', models.IntegerField(default=0)),
                ('price_count', models.IntegerField(default=0)),
                ('regime_count', models.IntegerField(default=0)),
                ('size_bytes', models.BigIntegerField(default=0)),
                ('checksum', models.CharField(blank=True, max_length=64)),
                ('metadata', models.JSONField(blank=True, default=dict)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='WorldModelRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cadence', models.CharField(choices=[('daily', '日次'), ('weekly', '週次'), ('monthly', '月次'), ('archive', 'アーカイブ'), ('manual', '手動')], max_length=16)),
                ('name', models.CharField(max_length=96)),
                ('status', models.CharField(choices=[('running', '実行中'), ('success', '成功'), ('partial', '一部失敗'), ('failed', '失敗')], default='running', max_length=16)),
                ('started_at', models.DateTimeField()),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('steps', models.JSONField(blank=True, default=list)),
                ('summary', models.JSONField(blank=True, default=dict)),
                ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-started_at'],
            },
        ),
        migrations.AddField(
            model_name='forecastsnapshot',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='forecastsnapshot',
            name='realized_at',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='rawarchivemanifest',
            index=models.Index(fields=['-created_at'], name='macro_rawar_created_9e52fb_idx'),
        ),
        migrations.AddIndex(
            model_name='rawarchivemanifest',
            index=models.Index(fields=['reason', '-created_at'], name='macro_rawar_reason_18fd6e_idx'),
        ),
        migrations.AddIndex(
            model_name='worldmodelrun',
            index=models.Index(fields=['cadence', '-started_at'], name='macro_world_cadence_d64f71_idx'),
        ),
        migrations.AddIndex(
            model_name='worldmodelrun',
            index=models.Index(fields=['status', '-started_at'], name='macro_world_status_c949d6_idx'),
        ),
    ]
