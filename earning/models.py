from django.db import models
from django.utils import timezone

class EarningsAnnouncement(models.Model):
    """
    決算発表予定を保存するモデル
    """
    date = models.DateField(verbose_name="決算発表日")
    company = models.CharField(max_length=200, verbose_name="企業名")
    industry = models.CharField(max_length=100, verbose_name="業種", blank=True)
    market = models.CharField(max_length=50, verbose_name="市場", blank=True)
    symbol = models.CharField(max_length=20, verbose_name="銘柄コード", blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="作成日時")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新日時")
    
    class Meta:
        db_table = 'earnings_announcements'
        verbose_name = '決算発表予定'
        verbose_name_plural = '決算発表予定'
        ordering = ['date', 'company']
        unique_together = ['date', 'company', 'symbol']  # 同じ企業の同日重複を防ぐ
    
    def __str__(self):
        return f"{self.date} - {self.company}"

class EarningsDataCache(models.Model):
    """
    決算データのキャッシュを管理するモデル
    """
    cache_key = models.CharField(max_length=100, unique=True, verbose_name="キャッシュキー")
    data = models.JSONField(verbose_name="キャッシュデータ")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="作成日時")
    expires_at = models.DateTimeField(verbose_name="有効期限")
    
    class Meta:
        db_table = 'earnings_data_cache'
        verbose_name = '決算データキャッシュ'
        verbose_name_plural = '決算データキャッシュ'
    
    def is_expired(self):
        return timezone.now() > self.expires_at
    
    def __str__(self):
        return f"{self.cache_key} (expires: {self.expires_at})"
