from django.db import models


class OutlookItem(models.Model):
    TAB_WATCH = "watch"
    TAB_NOTES = "notes"
    TAB_CHOICES = (
        (TAB_WATCH, "Watch"),
        (TAB_NOTES, "Notes"),
    )

    id = models.CharField(primary_key=True, max_length=32, editable=False)
    tab = models.CharField(max_length=10, choices=TAB_CHOICES)
    created_at = models.CharField(max_length=16)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    watch_until = models.DateField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at", "-id")


class TradePlanEntry(models.Model):
    plan_date = models.DateField(unique=True)
    long_text = models.TextField(blank=True)
    long_continue = models.BooleanField(default=False)
    short_text = models.TextField(blank=True)
    short_continue = models.BooleanField(default=False)
    square_text = models.TextField(blank=True)
    square_continue = models.BooleanField(default=False)

    class Meta:
        ordering = ("plan_date",)


class TradePlanPosition(models.Model):
    POSITION_LONG = "long"
    POSITION_SHORT = "short"
    POSITION_CHOICES = (
        (POSITION_LONG, "Long"),
        (POSITION_SHORT, "Short"),
    )

    id = models.CharField(primary_key=True, max_length=32, editable=False)
    position_type = models.CharField(max_length=10, choices=POSITION_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ("start_date", "end_date", "position_type", "id")
