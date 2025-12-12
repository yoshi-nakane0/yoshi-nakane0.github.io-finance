from django.db import models

class Analyst(models.Model):
    CATEGORY_CHOICES = [
        ('macro', 'Macroeconomics'),
        ('stock', 'Stock Market'),
    ]

    name = models.CharField(max_length=100)
    affiliation = models.CharField(max_length=100)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    score = models.IntegerField(default=3, help_text="1 (Bearish) to 5 (Bullish)")

    def __str__(self):
        return f"{self.name} ({self.affiliation})"