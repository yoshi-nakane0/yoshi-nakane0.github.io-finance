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
    indicator_vector = models.JSONField(default=dict, blank=True)
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


class DashboardCache(models.Model):
    """重い計算結果を JSON で保存し、ビューが高速に読めるようにする。"""

    cache_key = models.CharField(max_length=64, unique=True)
    payload = models.JSONField()
    computed_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.cache_key} @ {self.computed_at:%Y-%m-%d %H:%M}'
