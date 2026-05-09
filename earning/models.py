from django.db import models


class Stock(models.Model):
    symbol = models.CharField(max_length=16)
    market = models.CharField(max_length=16)
    company = models.CharField(max_length=128)
    industry = models.CharField(max_length=64, blank=True)
    theme = models.CharField(max_length=64, blank=True)
    watch_tier = models.CharField(max_length=16, blank=True)
    watch_role = models.CharField(max_length=64, blank=True)
    nikkei_weight = models.FloatField(null=True, blank=True)
    peer_symbols = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['symbol', 'market'], name='earning_stock_symbol_market_uniq'),
        ]
        indexes = [
            models.Index(fields=['symbol']),
        ]

    def __str__(self):
        return f'{self.symbol} ({self.company})'


class EarningsEvent(models.Model):
    FUNDAMENTAL_CHOICES = [('up', 'up'), ('flat', 'flat'), ('down', 'down')]
    GUIDANCE_CHOICES = [('', '—'), ('up', 'up'), ('flat', 'flat'), ('down', 'down')]
    INTERPRETATION_CHOICES = [('', '—'), ('bullish', 'bullish'), ('neutral', 'neutral'), ('bearish', 'bearish')]

    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='events')
    fiscal_period = models.CharField(max_length=16)
    event_date = models.DateField(null=True, blank=True)

    fundamental = models.CharField(max_length=8, choices=FUNDAMENTAL_CHOICES, default='flat')
    direction = models.CharField(max_length=8, choices=FUNDAMENTAL_CHOICES, default='flat')
    sentiment = models.CharField(max_length=8, choices=FUNDAMENTAL_CHOICES, default='flat')
    risk_value = models.FloatField(null=True, blank=True)

    eps_forecast = models.CharField(max_length=32, blank=True)
    eps_4q_ago = models.CharField(max_length=32, blank=True)
    eps_current = models.CharField(max_length=32, blank=True)
    eps_4q_prior_period = models.CharField(max_length=32, blank=True)
    surp_eps_4q_ago = models.CharField(max_length=32, blank=True)
    surp_eps_current = models.CharField(max_length=32, blank=True)
    surp_eps_4q_prior_period = models.CharField(max_length=32, blank=True)

    sales_forecast = models.CharField(max_length=32, blank=True)
    sales_4q_ago = models.CharField(max_length=32, blank=True)
    sales_current = models.CharField(max_length=32, blank=True)
    sales_4q_prior_period = models.CharField(max_length=32, blank=True)
    surp_4q_ago = models.CharField(max_length=32, blank=True)
    surp_current = models.CharField(max_length=32, blank=True)
    surp_4q_prior_period = models.CharField(max_length=32, blank=True)

    theme_score = models.FloatField(null=True, blank=True)
    gross_margin = models.FloatField(null=True, blank=True)
    operating_margin = models.FloatField(null=True, blank=True)
    relative_strength = models.FloatField(null=True, blank=True)
    guidance_revision = models.CharField(max_length=8, choices=GUIDANCE_CHOICES, default='', blank=True)

    reaction_close = models.FloatField(null=True, blank=True)
    reaction_next_day = models.FloatField(null=True, blank=True)
    market_interpretation = models.CharField(max_length=16, choices=INTERPRETATION_CHOICES, default='', blank=True)
    past_reactions = models.JSONField(default=list, blank=True)

    summary = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-event_date', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['stock', 'fiscal_period'],
                name='earning_event_stock_period_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['event_date']),
        ]

    def __str__(self):
        return f'{self.stock.symbol} {self.fiscal_period} ({self.event_date})'


class EarningsPrediction(models.Model):
    event = models.ForeignKey(EarningsEvent, on_delete=models.CASCADE, related_name='predictions')
    predicted_reaction = models.FloatField()
    confidence = models.FloatField(null=True, blank=True)
    model_version = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'model_version'],
                name='earning_prediction_event_model_uniq',
            ),
        ]

    def __str__(self):
        return f'{self.event} → {self.predicted_reaction:+.2f} ({self.model_version})'


class EarningsPriceWindow(models.Model):
    event = models.ForeignKey(EarningsEvent, on_delete=models.CASCADE, related_name='price_window')
    trade_date = models.DateField()
    offset_days = models.IntegerField()
    open = models.FloatField(null=True, blank=True)
    high = models.FloatField(null=True, blank=True)
    low = models.FloatField(null=True, blank=True)
    close = models.FloatField(null=True, blank=True)
    volume = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['event', 'trade_date']
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'trade_date'],
                name='earning_price_window_event_date_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['event', 'offset_days']),
        ]

    def __str__(self):
        return f'{self.event} {self.trade_date} (T{self.offset_days:+d})'
