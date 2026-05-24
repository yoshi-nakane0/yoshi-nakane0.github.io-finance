from django.db import models


class MarketSnapshot(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    symbol = models.CharField(max_length=32)
    price = models.FloatField()
    open = models.FloatField(null=True, blank=True)
    high = models.FloatField(null=True, blank=True)
    low = models.FloatField(null=True, blank=True)
    close = models.FloatField(null=True, blank=True)
    volume = models.FloatField(null=True, blank=True)
    timeframe = models.CharField(max_length=16)
    source = models.CharField(max_length=64)

    class Meta:
        indexes = [
            models.Index(fields=["symbol", "-created_at"]),
            models.Index(fields=["timeframe", "-created_at"]),
        ]


class MarketBar(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    symbol = models.CharField(max_length=32)
    timeframe = models.CharField(max_length=16)
    timestamp = models.DateTimeField()
    open = models.FloatField(null=True, blank=True)
    high = models.FloatField(null=True, blank=True)
    low = models.FloatField(null=True, blank=True)
    close = models.FloatField()
    volume = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "timeframe", "timestamp"],
                name="unique_basecalc_market_bar",
            ),
        ]
        indexes = [
            models.Index(
                fields=["symbol", "timeframe", "timestamp"],
                name="basecalc_ma_symbol_bf4cf8_idx",
            ),
            models.Index(
                fields=["timeframe", "-timestamp"],
                name="basecalc_ma_timefra_a4436d_idx",
            ),
        ]


class TechnicalSnapshot(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    market_snapshot = models.ForeignKey(MarketSnapshot, on_delete=models.CASCADE)
    ema5 = models.FloatField(null=True, blank=True)
    ema20 = models.FloatField(null=True, blank=True)
    ema60 = models.FloatField(null=True, blank=True)
    vwap = models.FloatField(null=True, blank=True)
    rsi14 = models.FloatField(null=True, blank=True)
    macd = models.FloatField(null=True, blank=True)
    macd_signal = models.FloatField(null=True, blank=True)
    adx14 = models.FloatField(null=True, blank=True)
    atr14 = models.FloatField(null=True, blank=True)
    bb_upper = models.FloatField(null=True, blank=True)
    bb_mid = models.FloatField(null=True, blank=True)
    bb_lower = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["-created_at"]),
        ]


class WorldModelPrediction(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    price = models.FloatField()
    state_key = models.CharField(max_length=64)
    state_label = models.CharField(max_length=64)
    direction = models.CharField(max_length=16)
    sentiment_score = models.IntegerField()
    continuation_score = models.IntegerField()
    shock_score = models.IntegerField()
    confidence = models.CharField(max_length=16)
    main_scenario = models.TextField()
    sub_scenario = models.TextField(blank=True)
    invalidation_price = models.FloatField(null=True, blank=True)
    upside_targets = models.JSONField(default=list)
    downside_targets = models.JSONField(default=list)
    evidence = models.JSONField(default=list)
    features = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["state_key", "-created_at"]),
            models.Index(fields=["direction", "-created_at"]),
        ]


class PredictionOutcome(models.Model):
    prediction = models.ForeignKey(WorldModelPrediction, on_delete=models.CASCADE)
    horizon = models.CharField(max_length=16)
    evaluated_at = models.DateTimeField()
    price_at_evaluation = models.FloatField()
    realized_return_pct = models.FloatField()
    direction_hit = models.BooleanField()
    upside_t1_hit = models.BooleanField(default=False)
    upside_t2_hit = models.BooleanField(default=False)
    downside_t1_hit = models.BooleanField(default=False)
    downside_t2_hit = models.BooleanField(default=False)
    invalidation_hit = models.BooleanField(default=False)
    mfe_pct = models.FloatField(null=True, blank=True)
    mae_pct = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["prediction", "horizon"],
                name="unique_basecalc_prediction_horizon",
            ),
        ]
        indexes = [
            models.Index(fields=["horizon", "-evaluated_at"]),
        ]
