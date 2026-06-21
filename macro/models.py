from django.db import models


class Indicator(models.Model):
    """FRED系列の指標マスタ"""

    class Category(models.TextChoices):
        INFLATION = 'inflation', 'インフレ'
        EMPLOYMENT = 'employment', '雇用・労働'
        GROWTH = 'growth', '景気・成長'
        RATES = 'rates', '金利・通貨'
        MARKET = 'market', '市場ストレス'

    class Importance(models.TextChoices):
        A = 'A', 'A（必須）'
        B = 'B', 'B（重要）'
        C = 'C', 'C（参考）'

    class Frequency(models.TextChoices):
        DAILY = 'daily', '日次'
        WEEKLY = 'weekly', '週次'
        MONTHLY = 'monthly', '月次'
        QUARTERLY = 'quarterly', '四半期'

    class Source(models.TextChoices):
        FRED = 'fred', 'FRED'
        CBOE = 'cboe', 'Cboe'
        FINRA = 'finra', 'FINRA'
        AAII = 'aaii', 'AAII'
        NAAIM = 'naaim', 'NAAIM'
        YFINANCE = 'yfinance', 'Yahoo Finance'
        YFINANCE_DAILY = 'yfinance_daily', 'Yahoo Finance (日次)'

    fred_series_id = models.CharField(max_length=64, unique=True)
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.FRED,
    )
    name_ja = models.CharField(max_length=128)
    name_en = models.CharField(max_length=128, blank=True)
    category = models.CharField(max_length=16, choices=Category.choices)
    importance = models.CharField(
        max_length=1,
        choices=Importance.choices,
        default=Importance.B,
    )
    frequency = models.CharField(
        max_length=16,
        choices=Frequency.choices,
        default=Frequency.MONTHLY,
    )
    unit = models.CharField(max_length=32, blank=True)
    description = models.TextField(blank=True)
    display_order = models.IntegerField(default=100)
    is_active = models.BooleanField(default=True)
    judgment_rule = models.JSONField(null=True, blank=True)
    # 異常値検出用の許容範囲。設定があれば取得時にチェックし範囲外は弾く。
    value_min = models.FloatField(null=True, blank=True)
    value_max = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'fred_series_id']

    def __str__(self):
        return f'{self.fred_series_id} ({self.name_ja})'


class Observation(models.Model):
    """指標の時系列観測値"""

    indicator = models.ForeignKey(
        Indicator,
        on_delete=models.CASCADE,
        related_name='observations',
    )
    observation_date = models.DateField()
    value = models.FloatField()
    prev_value = models.FloatField(null=True, blank=True)
    yoy_change = models.FloatField(null=True, blank=True)
    deviation_from_long_term = models.FloatField(null=True, blank=True)
    expanding_z_score = models.FloatField(null=True, blank=True)
    rolling_10y_z_score = models.FloatField(null=True, blank=True)
    rolling_5y_z_score = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['indicator', '-observation_date']
        constraints = [
            models.UniqueConstraint(
                fields=['indicator', 'observation_date'],
                name='uq_indicator_date',
            ),
        ]
        indexes = [
            models.Index(fields=['indicator', '-observation_date']),
        ]

    def __str__(self):
        return (
            f'{self.indicator.fred_series_id} '
            f'@ {self.observation_date}: {self.value}'
        )


class IndicatorSeries(models.Model):
    """予測エンジン向けの系列マスタ。

    既存の Indicator は画面表示と従来同期処理を支え、このモデルは
    ヴィンテージ管理やカテゴリ別の状態ベクトル生成に必要な属性を明示する。
    """

    class Source(models.TextChoices):
        FRED = 'fred', 'FRED'
        BLS = 'bls', 'BLS'
        BEA = 'bea', 'BEA'
        BOJ = 'boj', 'BOJ'
        ESRI = 'esri', 'ESRI'
        OECD = 'oecd', 'OECD'
        YFINANCE = 'yfinance', 'Yahoo Finance'
        OTHER = 'other', 'Other'

    class Frequency(models.TextChoices):
        DAILY = 'daily', '日次'
        WEEKLY = 'weekly', '週次'
        MONTHLY = 'monthly', '月次'
        QUARTERLY = 'quarterly', '四半期'

    class Category(models.TextChoices):
        GROWTH = 'growth', '成長'
        LABOR = 'labor', '雇用・賃金'
        INFLATION = 'inflation', '物価'
        POLICY = 'policy', '政策'
        CREDIT = 'credit', '信用'
        MARKET = 'market', '市場'
        GLOBAL = 'global', '世界需要'
        JAPAN = 'japan', '日本'

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        default=Source.FRED,
    )
    frequency = models.CharField(
        max_length=16,
        choices=Frequency.choices,
        default=Frequency.MONTHLY,
    )
    category = models.CharField(
        max_length=32,
        choices=Category.choices,
        default=Category.GROWTH,
    )
    direction = models.IntegerField(default=1)
    release_lag_days = models.IntegerField(default=0)
    is_core = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    indicator = models.OneToOneField(
        Indicator,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='series_profile',
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'code']
        indexes = [
            models.Index(fields=['category', 'is_active']),
            models.Index(fields=['source', 'code']),
        ]

    def __str__(self):
        return f'{self.code} ({self.name})'


class VintageObservation(models.Model):
    """取得時点ごとの経済統計値。

    FRED/ALFRED の realtime_start/realtime_end を保存し、後から
    「当時見えていた値」で検証できるようにする。
    """

    indicator = models.ForeignKey(
        Indicator,
        on_delete=models.CASCADE,
        related_name='vintage_observations',
    )
    observation_date = models.DateField()
    realtime_start = models.DateField()
    realtime_end = models.DateField()
    value = models.FloatField()
    collected_at = models.DateTimeField()
    source = models.CharField(max_length=32, default='fred')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['indicator', 'observation_date', '-realtime_start']
        constraints = [
            models.UniqueConstraint(
                fields=['indicator', 'observation_date', 'realtime_start', 'realtime_end'],
                name='uq_vintage_indicator_date_realtime',
            ),
        ]
        indexes = [
            models.Index(fields=['indicator', 'observation_date', '-realtime_start']),
            models.Index(fields=['indicator', 'realtime_start']),
        ]

    def __str__(self):
        return (
            f'{self.indicator.fred_series_id} {self.observation_date} '
            f'vintage {self.realtime_start}: {self.value}'
        )


class ObservationVintage(models.Model):
    """IndicatorSeries に紐づくリアルタイム・ヴィンテージ値。"""

    series = models.ForeignKey(
        IndicatorSeries,
        on_delete=models.CASCADE,
        related_name='vintages',
    )
    observation_date = models.DateField()
    value = models.FloatField()
    realtime_start = models.DateField()
    realtime_end = models.DateField(null=True, blank=True)
    fetched_at = models.DateTimeField()
    source_revision_id = models.CharField(max_length=128, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['series', 'observation_date', '-realtime_start']
        constraints = [
            models.UniqueConstraint(
                fields=['series', 'observation_date', 'realtime_start', 'realtime_end'],
                name='uq_series_observation_vintage',
            ),
        ]
        indexes = [
            models.Index(fields=['series', 'observation_date', '-realtime_start']),
            models.Index(fields=['series', 'realtime_start']),
        ]

    def __str__(self):
        return (
            f'{self.series.code} {self.observation_date} '
            f'vintage {self.realtime_start}: {self.value}'
        )


class RegimeSnapshot(models.Model):
    """マクロレジームの月次スナップショット"""

    class Label(models.TextChoices):
        EXPANSION = 'expansion', '拡大'
        SLOWDOWN = 'slowdown', '減速'
        CONTRACTION = 'contraction', '縮小'
        RECOVERY = 'recovery', '回復'
        UNKNOWN = 'unknown', '判定不能'

    class InflationFlag(models.TextChoices):
        HIGH = 'high', '高止まり'
        EASING = 'easing', '鈍化'
        NORMAL = 'normal', '正常'
        UNKNOWN = 'unknown', '判定不能'

    snapshot_date = models.DateField(unique=True)
    regime_label = models.CharField(
        max_length=16,
        choices=Label.choices,
        default=Label.UNKNOWN,
    )
    inflation_flag = models.CharField(
        max_length=16,
        choices=InflationFlag.choices,
        default=InflationFlag.UNKNOWN,
    )
    confidence = models.FloatField(default=0.0)
    rule_strength = models.FloatField(default=0.0)
    data_quality = models.FloatField(default=0.0)
    evidence = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    model_version = models.CharField(max_length=64, default='regime_v1')
    indicator_vector = models.JSONField(default=dict, blank=True)
    regime_probabilities = models.JSONField(default=dict, blank=True)
    risk_probabilities = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-snapshot_date']
        indexes = [
            models.Index(fields=['-snapshot_date']),
        ]

    def __str__(self):
        return (
            f'{self.snapshot_date}: '
            f'{self.regime_label} × {self.inflation_flag}'
        )


class PriceObservation(models.Model):
    """主要指数の月次終値（Yahoo Finance 由来）"""

    class Ticker(models.TextChoices):
        NIKKEI = 'N225', '日経225'
        SP500 = 'GSPC', 'S&P 500'
        NYDOW = 'DJI', 'NYダウ'
        NASDAQ = 'IXIC', 'NASDAQ'

    ticker = models.CharField(max_length=16, choices=Ticker.choices)
    observation_month = models.DateField()
    close_price = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['ticker', 'observation_month']
        constraints = [
            models.UniqueConstraint(
                fields=['ticker', 'observation_month'],
                name='uq_ticker_month',
            ),
        ]
        indexes = [
            models.Index(fields=['ticker', 'observation_month']),
        ]

    def __str__(self):
        return f'{self.ticker} {self.observation_month}: {self.close_price}'


class DailyPriceObservation(models.Model):
    """主要指数の日次終値。急落検証用。"""

    ticker = models.CharField(max_length=16)
    observation_date = models.DateField()
    close_price = models.FloatField()
    adjusted_close_price = models.FloatField(null=True, blank=True)
    volume = models.BigIntegerField(null=True, blank=True)
    source = models.CharField(max_length=32, default='yfinance')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['ticker', 'observation_date']
        constraints = [
            models.UniqueConstraint(
                fields=['ticker', 'observation_date'],
                name='uq_daily_price_ticker_date',
            ),
        ]
        indexes = [
            models.Index(fields=['ticker', 'observation_date']),
            models.Index(fields=['ticker', '-observation_date']),
        ]

    def __str__(self):
        return f'{self.ticker} {self.observation_date}: {self.close_price}'


class DashboardCache(models.Model):
    """重い計算結果を JSON で保存し、ビューが高速に読めるようにする。"""

    cache_key = models.CharField(max_length=64, unique=True)
    payload = models.JSONField()
    computed_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.cache_key} @ {self.computed_at:%Y-%m-%d %H:%M}'


class RawArchiveManifest(models.Model):
    """表示用DBから分離した履歴アーカイブの台帳。"""

    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=64)
    storage_backend = models.CharField(max_length=32, default='local')
    path = models.TextField()
    row_count = models.IntegerField(default=0)
    observation_count = models.IntegerField(default=0)
    price_count = models.IntegerField(default=0)
    regime_count = models.IntegerField(default=0)
    size_bytes = models.BigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['reason', '-created_at']),
        ]

    def __str__(self):
        return f'{self.reason}: {self.row_count} rows @ {self.created_at:%Y-%m-%d}'


class WorldModelRun(models.Model):
    """日次・週次・月次の運用実行履歴。"""

    class Cadence(models.TextChoices):
        DAILY = 'daily', '日次'
        WEEKLY = 'weekly', '週次'
        MONTHLY = 'monthly', '月次'
        ARCHIVE = 'archive', 'アーカイブ'
        MANUAL = 'manual', '手動'

    class Status(models.TextChoices):
        RUNNING = 'running', '実行中'
        SUCCESS = 'success', '成功'
        PARTIAL = 'partial', '一部失敗'
        FAILED = 'failed', '失敗'

    cadence = models.CharField(max_length=16, choices=Cadence.choices)
    name = models.CharField(max_length=96)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    steps = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['cadence', '-started_at']),
            models.Index(fields=['status', '-started_at']),
        ]

    def __str__(self):
        return f'{self.get_cadence_display()} {self.name}: {self.status}'


class ForecastSnapshot(models.Model):
    """モデル予測を後から検証できる形で保存する。"""

    as_of_date = models.DateField()
    model_version = models.CharField(max_length=64)
    target = models.CharField(max_length=32)
    horizon = models.CharField(max_length=32)
    prediction_value = models.FloatField()
    prediction_interval = models.JSONField(null=True, blank=True)
    features_hash = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    realized_value = models.FloatField(null=True, blank=True)
    error = models.FloatField(null=True, blank=True)
    realized_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-as_of_date', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['as_of_date', 'model_version', 'target', 'horizon'],
                name='uq_forecast_snapshot_identity',
            ),
        ]
        indexes = [
            models.Index(fields=['target', 'as_of_date']),
            models.Index(fields=['model_version', 'as_of_date']),
        ]

    def __str__(self):
        return (
            f'{self.as_of_date}: {self.model_version} '
            f'{self.target} {self.horizon}={self.prediction_value}'
        )


class MacroForecastRun(models.Model):
    """経済状態推定エンジンの1回分の実行結果。"""

    as_of = models.DateField(unique=True)
    forecast = models.OneToOneField(
        ForecastSnapshot,
        on_delete=models.CASCADE,
        related_name='macro_run',
    )
    primary_regime = models.CharField(max_length=32)
    previous_regime = models.CharField(max_length=32, blank=True)
    confidence = models.FloatField(default=0.0)
    data_quality_score = models.FloatField(default=0.0)
    state_vector = models.JSONField(default=dict, blank=True)
    regime_probabilities = models.JSONField(default=dict, blank=True)
    risk_probabilities = models.JSONField(default=dict, blank=True)
    report = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    model_version = models.CharField(max_length=64, default='macro_hatzius_v1')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-as_of']
        indexes = [
            models.Index(fields=['-as_of']),
            models.Index(fields=['primary_regime', '-as_of']),
        ]

    def __str__(self):
        return f'{self.as_of}: {self.primary_regime}'


class MacroScenario(models.Model):
    """基本・上振れ・下振れのシナリオと反証条件。"""

    class Name(models.TextChoices):
        BASELINE = 'baseline', '基本'
        UPSIDE = 'upside', '上振れ'
        DOWNSIDE = 'downside', '下振れ'

    class NikkeiBias(models.TextChoices):
        LONG = 'long', '上昇支援'
        SHORT = 'short', '下落圧力'
        NEUTRAL = 'neutral', '中立'

    run = models.ForeignKey(
        MacroForecastRun,
        on_delete=models.CASCADE,
        related_name='scenarios',
    )
    name = models.CharField(max_length=32, choices=Name.choices)
    probability = models.FloatField()
    growth_view = models.TextField()
    inflation_view = models.TextField()
    policy_view = models.TextField()
    market_view = models.TextField()
    nikkei_bias = models.CharField(
        max_length=16,
        choices=NikkeiBias.choices,
        default=NikkeiBias.NEUTRAL,
    )
    key_drivers = models.JSONField(default=list, blank=True)
    invalidation_triggers = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['run', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['run', 'name'],
                name='uq_macro_scenario_run_name',
            ),
        ]
        indexes = [
            models.Index(fields=['name', '-created_at']),
        ]

    def __str__(self):
        return f'{self.run.as_of} {self.name}: {self.probability:.0%}'


class MacroForecastOutcome(models.Model):
    """保存済み予測が後からどれだけ当たったかを記録する。"""

    forecast = models.ForeignKey(
        ForecastSnapshot,
        on_delete=models.CASCADE,
        related_name='macro_outcomes',
    )
    target_date = models.DateField()
    target_name = models.CharField(max_length=64)
    predicted_value = models.FloatField(null=True, blank=True)
    predicted_prob = models.FloatField(null=True, blank=True)
    actual_value = models.FloatField(null=True, blank=True)
    brier_score = models.FloatField(null=True, blank=True)
    absolute_error = models.FloatField(null=True, blank=True)
    direction_hit = models.BooleanField(null=True, blank=True)
    evaluated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-target_date', '-evaluated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['forecast', 'target_date', 'target_name'],
                name='uq_macro_forecast_outcome_target',
            ),
        ]
        indexes = [
            models.Index(fields=['target_name', '-target_date']),
            models.Index(fields=['forecast', 'target_name']),
        ]

    def __str__(self):
        return f'{self.forecast_id} {self.target_name} @ {self.target_date}'


class WorldStateSnapshot(models.Model):
    """World Model の中核となる経済・市場状態ベクトル。"""

    class Cadence(models.TextChoices):
        DAILY = 'daily', '日次'
        WEEKLY = 'weekly', '週次'
        MONTHLY = 'monthly', '月次'
        MANUAL = 'manual', '手動'

    as_of_date = models.DateField(unique=True)
    cadence = models.CharField(
        max_length=16,
        choices=Cadence.choices,
        default=Cadence.DAILY,
    )

    growth_score = models.FloatField(null=True, blank=True)
    labor_score = models.FloatField(null=True, blank=True)
    inflation_score = models.FloatField(null=True, blank=True)
    policy_pressure_score = models.FloatField(null=True, blank=True)
    liquidity_score = models.FloatField(null=True, blank=True)
    credit_score = models.FloatField(null=True, blank=True)
    risk_appetite_score = models.FloatField(null=True, blank=True)
    market_trend_score = models.FloatField(null=True, blank=True)
    external_shock_score = models.FloatField(null=True, blank=True)

    market_stress_score = models.FloatField(null=True, blank=True)
    recession_risk_score = models.FloatField(null=True, blank=True)
    inflation_reacceleration_score = models.FloatField(null=True, blank=True)
    financial_stress_score = models.FloatField(null=True, blank=True)

    data_quality = models.FloatField(default=0.0)
    source_freshness = models.JSONField(default=dict, blank=True)
    feature_vector = models.JSONField(default=dict, blank=True)
    explanation = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    model_version = models.CharField(max_length=64, default='world_state_v1')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-as_of_date']
        indexes = [
            models.Index(fields=['-as_of_date']),
            models.Index(fields=['cadence', '-as_of_date']),
        ]

    def __str__(self):
        return f'{self.as_of_date}: {self.model_version}'


class PolicyExpectationSnapshot(models.Model):
    """政策金利見通しと金利市場の株価向け逆風/追い風を保存する。"""

    as_of = models.DateTimeField(db_index=True)
    central_bank = models.CharField(max_length=16, db_index=True, default='FED')
    effective_rate = models.FloatField(null=True, blank=True)
    target_lower = models.FloatField(null=True, blank=True)
    target_upper = models.FloatField(null=True, blank=True)

    implied_next_meeting_delta_bp = models.FloatField(null=True, blank=True)
    implied_3m_delta_bp = models.FloatField(null=True, blank=True)
    implied_6m_delta_bp = models.FloatField(null=True, blank=True)
    implied_12m_delta_bp = models.FloatField(null=True, blank=True)

    rate_shock_1d_bp = models.FloatField(null=True, blank=True)
    rate_shock_5d_bp = models.FloatField(null=True, blank=True)
    policy_bias = models.CharField(max_length=32, default='neutral')
    data_quality = models.FloatField(default=0.0)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-as_of']
        indexes = [
            models.Index(fields=['central_bank', '-as_of']),
            models.Index(fields=['policy_bias', '-as_of']),
        ]

    def __str__(self):
        return f'{self.central_bank} {self.as_of:%Y-%m-%d}: {self.policy_bias}'


class FeatureSnapshot(models.Model):
    """予測・検証に使った特徴量を再現可能に保存する。"""

    as_of_date = models.DateField()
    namespace = models.CharField(max_length=64)
    target = models.CharField(max_length=32)
    horizon = models.CharField(max_length=32)
    model_version = models.CharField(max_length=64)
    feature_hash = models.CharField(max_length=64)
    feature_vector = models.JSONField(default=dict, blank=True)
    source_dates = models.JSONField(default=dict, blank=True)
    data_quality = models.FloatField(default=0.0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-as_of_date', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'as_of_date',
                    'namespace',
                    'target',
                    'horizon',
                    'model_version',
                ],
                name='uq_feature_snapshot_identity',
            ),
        ]
        indexes = [
            models.Index(fields=['namespace', 'target', 'as_of_date']),
            models.Index(fields=['model_version', 'as_of_date']),
        ]

    def __str__(self):
        return (
            f'{self.as_of_date}: {self.namespace} '
            f'{self.target} {self.horizon}'
        )


class ModelValidationReport(models.Model):
    """モデル別・対象別の検証結果。"""

    evaluated_at = models.DateTimeField(auto_now_add=True)
    model_version = models.CharField(max_length=64)
    target = models.CharField(max_length=32)
    horizon = models.CharField(max_length=32)
    validation_method = models.CharField(max_length=64, default='walk_forward')
    sample_count = models.IntegerField(default=0)
    event_count = models.IntegerField(null=True, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    rows = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-evaluated_at']
        indexes = [
            models.Index(fields=['model_version', 'target', 'horizon']),
            models.Index(fields=['-evaluated_at']),
        ]

    def __str__(self):
        return f'{self.model_version} {self.target} {self.horizon}'


class MacroEventSurprise(models.Model):
    """経済指標の市場予想との差を後から参照できる形で保存する。"""

    event_date = models.DateField()
    event_name = models.CharField(max_length=128)
    category = models.CharField(max_length=32, default='macro')
    actual = models.FloatField(null=True, blank=True)
    consensus = models.FloatField(null=True, blank=True)
    previous = models.FloatField(null=True, blank=True)
    surprise = models.FloatField(null=True, blank=True)
    revision = models.FloatField(null=True, blank=True)
    unit = models.CharField(max_length=16, blank=True)
    direction = models.CharField(max_length=32, default='unknown')
    market_impact = models.TextField(blank=True)
    next_forecast_impact = models.TextField(blank=True)
    source = models.CharField(max_length=64, default='manual_consensus')
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-event_date', 'event_name']
        constraints = [
            models.UniqueConstraint(
                fields=['event_date', 'event_name', 'source'],
                name='uq_macro_event_surprise_identity',
            ),
        ]
        indexes = [
            models.Index(fields=['category', '-event_date']),
            models.Index(fields=['direction', '-event_date']),
        ]

    def __str__(self):
        return f'{self.event_date}: {self.event_name} {self.direction}'
