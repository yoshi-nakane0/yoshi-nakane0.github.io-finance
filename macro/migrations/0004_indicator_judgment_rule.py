from django.db import migrations, models


# 各指標の判定ルール初期値。Phase 1 で全アクティブ指標に設定する。
# 構造:
#   metric:    "yoy" | "level" | "mom" | "diff_50"
#   economic:  経済視点の判定。direction と thresholds で5段階に分ける
#   market:    市場視点の判定。同上
# direction:
#   "lower_better"  値が小さいほど良い
#   "higher_better" 値が大きいほど良い
#   "target_band"   中央が良い（U字評価）
JUDGMENT_RULES = {
    # === インフレ ===
    'CPIAUCSL': {
        'metric': 'yoy',
        'economic': {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 2.5, 4.0]},
        'market':   {'direction': 'lower_better', 'thresholds': [1.5, 2.5, 3.5, 4.5]},
    },
    'CPILFESL': {
        'metric': 'yoy',
        'economic': {'direction': 'target_band', 'thresholds': [-0.5, 1.5, 2.5, 4.0]},
        'market':   {'direction': 'lower_better', 'thresholds': [1.5, 2.5, 3.5, 4.5]},
    },
    'PCEPI': {
        'metric': 'yoy',
        'economic': {'direction': 'target_band', 'thresholds': [-0.5, 1.5, 2.5, 3.5]},
        'market':   {'direction': 'lower_better', 'thresholds': [1.5, 2.0, 3.0, 4.0]},
    },
    'PCEPILFE': {
        'metric': 'yoy',
        'economic': {'direction': 'target_band', 'thresholds': [-0.5, 1.5, 2.5, 3.5]},
        'market':   {'direction': 'lower_better', 'thresholds': [1.5, 2.0, 3.0, 4.0]},
    },
    'T5YIE': {
        'metric': 'level',
        'economic': {'direction': 'target_band', 'thresholds': [1.0, 1.8, 2.5, 3.5]},
        'market':   {'direction': 'target_band', 'thresholds': [1.0, 1.8, 2.5, 3.5]},
    },

    # === 雇用・労働 ===
    'UNRATE': {
        'metric': 'level',
        'economic': {'direction': 'target_band', 'thresholds': [3.5, 4.5, 5.5, 6.5]},
        'market':   {'direction': 'target_band', 'thresholds': [3.0, 3.8, 4.5, 5.5]},
    },
    'PAYEMS': {
        # 前月差（千人）。雇用統計は通常 +150〜250k あたりが健全。
        'metric': 'mom',
        'economic': {'direction': 'target_band', 'thresholds': [-100, 100, 200, 400]},
        'market':   {'direction': 'target_band', 'thresholds': [50, 150, 250, 400]},
    },
    'CES0500000003': {
        # 平均時給 YoY%
        'metric': 'yoy',
        'economic': {'direction': 'target_band', 'thresholds': [1.0, 2.5, 3.5, 5.0]},
        'market':   {'direction': 'lower_better', 'thresholds': [2.0, 3.0, 4.0, 5.0]},
    },
    'JTSJOL': {
        # 求人数（千件）
        'metric': 'level',
        'economic': {'direction': 'target_band', 'thresholds': [6000, 7500, 9000, 11000]},
        'market':   {'direction': 'target_band', 'thresholds': [6500, 7500, 8500, 10000]},
    },

    # === 景気・成長 ===
    'GDPC1': {
        'metric': 'yoy',
        'economic': {'direction': 'target_band', 'thresholds': [0.0, 1.5, 2.5, 4.0]},
        'market':   {'direction': 'target_band', 'thresholds': [1.0, 2.0, 3.0, 4.0]},
    },
    'INDPRO': {
        'metric': 'yoy',
        'economic': {'direction': 'higher_better', 'thresholds': [-3.0, -1.0, 1.0, 3.0]},
        'market':   {'direction': 'higher_better', 'thresholds': [-2.0, 0.0, 2.0, 4.0]},
    },
    'TCU': {
        'metric': 'level',
        'economic': {'direction': 'target_band', 'thresholds': [70, 76, 80, 84]},
        'market':   {'direction': 'target_band', 'thresholds': [72, 76, 80, 84]},
    },
    'RSAFS': {
        'metric': 'yoy',
        'economic': {'direction': 'higher_better', 'thresholds': [-2.0, 0.0, 2.0, 5.0]},
        'market':   {'direction': 'target_band', 'thresholds': [-1.0, 1.0, 4.0, 7.0]},
    },
    'UMCSENT': {
        'metric': 'level',
        'economic': {'direction': 'higher_better', 'thresholds': [60, 75, 90, 100]},
        'market':   {'direction': 'higher_better', 'thresholds': [60, 75, 90, 100]},
    },
    'HOUST': {
        'metric': 'level',
        'economic': {'direction': 'higher_better', 'thresholds': [900, 1200, 1500, 1800]},
        'market':   {'direction': 'higher_better', 'thresholds': [900, 1200, 1500, 1800]},
    },

    # === 金利・通貨 ===
    'DFF': {
        'metric': 'level',
        'economic': {'direction': 'target_band', 'thresholds': [1.0, 2.0, 3.5, 5.0]},
        'market':   {'direction': 'lower_better', 'thresholds': [2.0, 3.0, 4.0, 5.0]},
    },
    'DGS10': {
        'metric': 'level',
        'economic': {'direction': 'target_band', 'thresholds': [2.0, 3.0, 4.5, 5.5]},
        'market':   {'direction': 'lower_better', 'thresholds': [3.0, 3.5, 4.5, 5.0]},
    },
    'DGS2': {
        'metric': 'level',
        'economic': {'direction': 'target_band', 'thresholds': [1.5, 2.5, 4.0, 5.5]},
        'market':   {'direction': 'lower_better', 'thresholds': [2.5, 3.5, 4.5, 5.5]},
    },
    'T10Y2Y': {
        'metric': 'level',
        'economic': {'direction': 'higher_better', 'thresholds': [-0.5, 0.0, 0.5, 1.5]},
        'market':   {'direction': 'higher_better', 'thresholds': [-0.5, 0.0, 0.5, 1.5]},
    },
    'DTWEXBGS': {
        'metric': 'yoy',
        'economic': {'direction': 'target_band', 'thresholds': [-10, -3, 3, 10]},
        'market':   {'direction': 'target_band', 'thresholds': [-10, -5, 5, 10]},
    },
    'DEXJPUS': {
        'metric': 'level',
        'economic': {'direction': 'target_band', 'thresholds': [110, 130, 150, 165]},
        'market':   {'direction': 'target_band', 'thresholds': [110, 130, 150, 165]},
    },

    # === 市場ストレス ===
    'VIXCLS': {
        'metric': 'level',
        'economic': {'direction': 'lower_better', 'thresholds': [15, 20, 25, 30]},
        'market':   {'direction': 'lower_better', 'thresholds': [15, 20, 25, 30]},
    },
    'BAMLH0A0HYM2': {
        'metric': 'level',
        'economic': {'direction': 'lower_better', 'thresholds': [3.0, 4.0, 5.5, 7.0]},
        'market':   {'direction': 'lower_better', 'thresholds': [3.0, 4.0, 5.5, 7.0]},
    },
}


def seed_judgment_rules(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    for series_id, rule in JUDGMENT_RULES.items():
        Indicator.objects.filter(fred_series_id=series_id).update(judgment_rule=rule)


def reverse_seed_judgment_rules(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    Indicator.objects.filter(fred_series_id__in=JUDGMENT_RULES.keys()).update(judgment_rule=None)


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0003_priceobservation_priceobservation_uq_ticker_month'),
    ]

    operations = [
        migrations.AddField(
            model_name='indicator',
            name='judgment_rule',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(seed_judgment_rules, reverse_seed_judgment_rules),
    ]
