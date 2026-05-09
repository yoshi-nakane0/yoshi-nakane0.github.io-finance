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
