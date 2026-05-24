from django.db import migrations, models


def backfill_reliability_fields(apps, schema_editor):
    WorldModelPrediction = apps.get_model("basecalc", "WorldModelPrediction")
    MarketBar = apps.get_model("basecalc", "MarketBar")
    MarketSnapshot = apps.get_model("basecalc", "MarketSnapshot")

    for prediction in WorldModelPrediction.objects.all().iterator():
        features = prediction.features or {}
        symbol = (features.get("source_symbol") or features.get("symbol") or "").upper()
        source = features.get("source_name") or features.get("source") or ""
        instrument_key, instrument_type = _instrument_from_symbol(symbol)
        score = prediction.data_quality_score
        readiness_level = "blocked"
        if score is not None:
            if score >= 80:
                readiness_level = "ready"
            elif score >= 50:
                readiness_level = "limited"
        WorldModelPrediction.objects.filter(id=prediction.id).update(
            prediction_timestamp=prediction.created_at,
            instrument_key=instrument_key,
            instrument_type=features.get("instrument_type") or instrument_type,
            source_symbol=symbol or features.get("symbol") or "",
            source_name=source,
            readiness_level=readiness_level,
            directional_allowed=readiness_level == "ready" and prediction.direction in ("up", "down", "neutral"),
            readiness_reason_codes=[],
            bar_counts={},
            indicator_validity=features.get("indicator_validity") or {},
            is_backtest=False,
        )

    for bar in MarketBar.objects.all().iterator():
        instrument_key, instrument_type = _instrument_from_symbol(bar.symbol)
        MarketBar.objects.filter(id=bar.id).update(
            instrument_key=instrument_key,
            instrument_type=instrument_type,
        )

    for snapshot in MarketSnapshot.objects.all().iterator():
        instrument_key, instrument_type = _instrument_from_symbol(snapshot.symbol)
        MarketSnapshot.objects.filter(id=snapshot.id).update(
            instrument_key=instrument_key,
            instrument_type=instrument_type,
            source_symbol=snapshot.symbol,
            fetched_at=snapshot.created_at,
        )


def _instrument_from_symbol(symbol):
    normalized = (symbol or "").upper()
    if normalized == "NIY=F":
        return "cme_nikkei_futures", "futures"
    if normalized == "NK.F":
        return "stooq_nikkei_futures", "futures_proxy"
    if normalized == "^NKX":
        return "nikkei_index_fallback", "index_fallback"
    return "unknown", "unknown"


class Migration(migrations.Migration):

    dependencies = [
        ("basecalc", "0003_worldmodelprediction_v2_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketsnapshot",
            name="data_quality_level",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name="marketsnapshot",
            name="data_quality_score",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="marketsnapshot",
            name="fetched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="marketsnapshot",
            name="instrument_key",
            field=models.CharField(db_index=True, default="unknown", max_length=64),
        ),
        migrations.AddField(
            model_name="marketsnapshot",
            name="instrument_type",
            field=models.CharField(default="unknown", max_length=64),
        ),
        migrations.AddField(
            model_name="marketsnapshot",
            name="readiness_level",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name="marketsnapshot",
            name="source_symbol",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="marketbar",
            name="data_quality_score",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="marketbar",
            name="instrument_key",
            field=models.CharField(db_index=True, default="unknown", max_length=64),
        ),
        migrations.AddField(
            model_name="marketbar",
            name="instrument_type",
            field=models.CharField(default="unknown", max_length=64),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="bar_counts",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="directional_allowed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="indicator_validity",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="instrument_key",
            field=models.CharField(db_index=True, default="unknown", max_length=64),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="instrument_type",
            field=models.CharField(default="unknown", max_length=64),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="is_backtest",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="prediction_timestamp",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="readiness_level",
            field=models.CharField(db_index=True, default="blocked", max_length=16),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="readiness_reason_codes",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="source_name",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="worldmodelprediction",
            name="source_symbol",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddIndex(
            model_name="marketbar",
            index=models.Index(
                fields=["instrument_key", "timeframe", "timestamp"],
                name="basecalc_ma_inst_tf_ts_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="worldmodelprediction",
            index=models.Index(
                fields=["instrument_key", "readiness_level", "is_backtest"],
                name="basecalc_wo_instr__e857ed_idx",
            ),
        ),
        migrations.RunPython(backfill_reliability_fields, migrations.RunPython.noop),
    ]
