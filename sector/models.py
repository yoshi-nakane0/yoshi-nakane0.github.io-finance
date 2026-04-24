from django.db import models

class SectorSnapshot(models.Model):
    sectors = models.JSONField()
    benchmarks = models.JSONField()
    update_time = models.CharField(max_length=32)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"SectorSnapshot ({self.update_time})"
