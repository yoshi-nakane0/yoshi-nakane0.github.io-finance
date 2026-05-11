from django.db import models


class SentimentTopic(models.Model):
    """GDELT トピックマスタ"""

    class Category(models.TextChoices):
        INFLATION = 'inflation', 'インフレ'
        GEOPOLITICAL = 'geopolitical', '地政学'
        FED = 'fed', 'FRB'
        RECESSION = 'recession', 'リセッション'

    slug = models.SlugField(max_length=32, unique=True)
    name_ja = models.CharField(max_length=64)
    category = models.CharField(max_length=16, choices=Category.choices)
    query = models.TextField()
    display_order = models.IntegerField(default=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'slug']

    def __str__(self):
        return f'{self.slug} ({self.name_ja})'


class SentimentObservation(models.Model):
    """トピックごとの日次集計値"""

    topic = models.ForeignKey(
        SentimentTopic,
        on_delete=models.CASCADE,
        related_name='observations',
    )
    observation_date = models.DateField()
    articles_count = models.IntegerField(default=0)
    tone_avg = models.FloatField(null=True, blank=True)
    tone_min = models.FloatField(null=True, blank=True)
    tone_max = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['topic', '-observation_date']
        constraints = [
            models.UniqueConstraint(
                fields=['topic', 'observation_date'],
                name='uq_sentiment_topic_date',
            ),
        ]
        indexes = [
            models.Index(fields=['topic', '-observation_date']),
        ]

    def __str__(self):
        return (
            f'{self.topic.slug} @ {self.observation_date}: '
            f'{self.articles_count} articles'
        )


class SentimentArticle(models.Model):
    """ネガティブ記事リスト用の個別記事キャッシュ"""

    topic = models.ForeignKey(
        SentimentTopic,
        on_delete=models.CASCADE,
        related_name='articles',
    )
    published_at = models.DateTimeField()
    title = models.TextField()
    url = models.URLField(max_length=512)
    domain = models.CharField(max_length=128, blank=True)
    tone = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-published_at']
        constraints = [
            models.UniqueConstraint(
                fields=['topic', 'url'],
                name='uq_sentiment_article_topic_url',
            ),
        ]
        indexes = [
            models.Index(fields=['topic', '-published_at']),
            models.Index(fields=['-published_at', 'tone']),
        ]

    def __str__(self):
        return f'{self.title[:50]} ({self.tone})'
