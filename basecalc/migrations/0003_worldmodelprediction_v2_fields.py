from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basecalc", "0002_marketbar"),
    ]

    operations = [
        migrations.AddField(
            model_name="worldmodelprediction",
            name="confidence_score",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="context",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="data_quality_score",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="expected_returns",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="model_version",
            field=models.CharField(default="wm_v1", max_length=32),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="transition_probs",
            field=models.JSONField(default=list),
        ),
        migrations.AddIndex(
            model_name="worldmodelprediction",
            index=models.Index(
                fields=["model_version", "-created_at"],
                name="basecalc_wo_model_v_d34812_idx",
            ),
        ),
    ]
