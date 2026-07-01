from django.db import models


class ExplanationSnapshot(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    as_of = models.DateTimeField(db_index=True)

    final_label = models.CharField(max_length=64)
    final_stance = models.CharField(max_length=64, db_index=True)
    action_posture = models.CharField(max_length=64)
    confidence_score = models.IntegerField()
    confidence_grade = models.CharField(max_length=16)

    macro_bias = models.CharField(max_length=64)
    basecalc_bias = models.CharField(max_length=64)
    alignment_status = models.CharField(max_length=32)

    data_quality_score = models.IntegerField()
    audit_level = models.CharField(max_length=32)
    audit_items = models.JSONField(default=list)

    scenario = models.JSONField(default=dict)
    trade_decision = models.JSONField(default=dict)
    evidence = models.JSONField(default=list)
    source_snapshots = models.JSONField(default=dict)
    score_breakdown = models.JSONField(default=dict)

    version = models.CharField(max_length=32, default='explanation_v2')

    class Meta:
        ordering = ['-as_of', '-created_at']
        indexes = [
            models.Index(fields=['-as_of'], name='explanation_as_of_idx'),
            models.Index(fields=['final_stance', '-as_of'], name='explanation_stance_idx'),
            models.Index(fields=['audit_level', '-as_of'], name='explanation_audit_idx'),
        ]

    def __str__(self):
        return f'{self.as_of:%Y-%m-%d %H:%M}: {self.final_label}'


class ExplanationOutcome(models.Model):
    explanation = models.ForeignKey(ExplanationSnapshot, on_delete=models.CASCADE)
    horizon = models.CharField(max_length=16)
    evaluated_at = models.DateTimeField()
    price_at_evaluation = models.FloatField()
    realized_return_pct = models.FloatField()
    direction_hit = models.BooleanField()
    invalidation_hit = models.BooleanField(default=False)

    class Meta:
        ordering = ['-evaluated_at']
        indexes = [
            models.Index(fields=['horizon', '-evaluated_at'], name='explanation_horizon_idx'),
            models.Index(fields=['direction_hit', '-evaluated_at'], name='explanation_hit_idx'),
        ]

    def __str__(self):
        return f'{self.horizon} {self.evaluated_at:%Y-%m-%d}: {self.realized_return_pct:+.2f}%'


class ExplanationTradeOutcome(models.Model):
    explanation = models.ForeignKey(ExplanationSnapshot, on_delete=models.CASCADE)
    horizon = models.CharField(max_length=16)
    evaluated_at = models.DateTimeField()

    selected_side = models.CharField(max_length=16)
    decision_type = models.CharField(max_length=32)
    trend_or_reversal = models.CharField(max_length=32, blank=True, default='')
    entry_price = models.FloatField(blank=True, null=True)
    target_1_price = models.FloatField(blank=True, null=True)
    target_1_hit = models.BooleanField(default=False)
    target_2_price = models.FloatField(blank=True, null=True)
    target_2_hit = models.BooleanField(default=False)
    stop_price = models.FloatField(blank=True, null=True)
    stop_hit = models.BooleanField(default=False)
    max_favorable_excursion = models.FloatField(blank=True, null=True)
    max_adverse_excursion = models.FloatField(blank=True, null=True)
    exit_price = models.FloatField(blank=True, null=True)
    exit_reason = models.CharField(max_length=32, blank=True, default='')
    realized_rr = models.FloatField(blank=True, null=True)
    expected_rr = models.FloatField(blank=True, null=True)
    direction_hit = models.BooleanField(blank=True, null=True, default=None)
    is_actionable = models.BooleanField(default=False)
    outcome_kind = models.CharField(max_length=32, default='wait_observed')
    missed_opportunity = models.BooleanField(default=False)
    horizon_return_pct = models.FloatField(blank=True, null=True)
    macro_regime = models.CharField(max_length=64, blank=True, default='')
    technical_regime = models.CharField(max_length=64, blank=True, default='')
    confidence_bucket = models.CharField(max_length=16, blank=True, default='')
    sample_count_at_decision = models.IntegerField(blank=True, null=True)

    class Meta:
        ordering = ['-evaluated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['explanation', 'horizon'],
                name='unique_explanation_trade_horizon',
            ),
        ]
        indexes = [
            models.Index(fields=['selected_side', '-evaluated_at'], name='explanation_trade_side_idx'),
            models.Index(fields=['decision_type', '-evaluated_at'], name='explanation_trade_type_idx'),
            models.Index(fields=['confidence_bucket', '-evaluated_at'], name='explanation_trade_conf_idx'),
        ]

    def __str__(self):
        return f'{self.horizon} {self.selected_side} {self.evaluated_at:%Y-%m-%d}'
