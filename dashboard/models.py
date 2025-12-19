from django.db import models

class AiAnalysis(models.Model):
    title = models.CharField(max_length=200, verbose_name="タイトル")
    summary = models.TextField(blank=True, verbose_name="要約")
    content = models.TextField(verbose_name="分析内容")
    analysis_date = models.DateField(verbose_name="分析日")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="作成日時")

    class Meta:
        ordering = ['-analysis_date']
        verbose_name = "AI分析結果"
        verbose_name_plural = "AI分析結果"

    def __str__(self):
        return self.title