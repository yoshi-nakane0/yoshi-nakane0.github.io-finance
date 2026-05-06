"""Phase 4: 外部データソース5系列の登録。

source 別に取得元が分かれる：
  - Cboe : SKEW
  - FINRA: Margin Debt
  - AAII : Bullish ratio
  - NAAIM: Exposure index
  - yfinance: Russell 2000
"""

from django.db import migrations


NEW_INDICATORS = [
    {
        'fred_series_id': 'CBOE_SKEW',
        'source': 'cboe',
        'name_ja': 'SKEW指数',
        'name_en': 'CBOE SKEW Index',
        'category': 'market',
        'importance': 'B',
        'frequency': 'daily',
        'unit': 'index',
        'description': 'SP500のテールリスク警戒度（オプション市場のスキュー）。値が高いほど警戒。',
        'display_order': 425,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [120, 130, 140, 150]},
            'market':   {'direction': 'lower_better', 'thresholds': [120, 130, 140, 150]},
        },
    },
    {
        'fred_series_id': 'FINRA_MARGIN_DEBT',
        'source': 'finra',
        'name_ja': '信用取引残高',
        'name_en': 'FINRA Customer Debit Balances (Margin)',
        'category': 'market',
        'importance': 'B',
        'frequency': 'monthly',
        'unit': '百万$',
        'description': '証券会社の信用取引残高。極端な急増はバブル兆候として有名。',
        'display_order': 510,
        'rule': {
            'metric': 'yoy',
            'economic': {'direction': 'target_band', 'thresholds': [-15, 0, 15, 30]},
            'market':   {'direction': 'target_band', 'thresholds': [-15, 0, 15, 30]},
        },
    },
    {
        'fred_series_id': 'AAII_BULLISH',
        'source': 'aaii',
        'name_ja': 'AAII強気%',
        'name_en': 'AAII Investor Sentiment Bullish %',
        'category': 'market',
        'importance': 'B',
        'frequency': 'weekly',
        'unit': '%',
        'description': '個人投資家の強気比率。極端な楽観/悲観は逆指標として有名。',
        'display_order': 520,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'target_band', 'thresholds': [20, 30, 45, 55]},
            'market':   {'direction': 'target_band', 'thresholds': [20, 30, 45, 55]},
        },
    },
    {
        'fred_series_id': 'NAAIM_EXPOSURE',
        'source': 'naaim',
        'name_ja': 'NAAIMエクスポージャー',
        'name_en': 'NAAIM Exposure Index',
        'category': 'market',
        'importance': 'B',
        'frequency': 'weekly',
        'unit': '%',
        'description': 'アクティブマネージャーの株式エクスポージャー。極端は反転シグナル。',
        'display_order': 530,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'target_band', 'thresholds': [20, 50, 80, 95]},
            'market':   {'direction': 'target_band', 'thresholds': [20, 50, 80, 95]},
        },
    },
    {
        'fred_series_id': 'RUT_INDEX',
        'source': 'yfinance',
        'name_ja': 'Russell 2000',
        'name_en': 'Russell 2000 Index',
        'category': 'market',
        'importance': 'B',
        'frequency': 'daily',
        'unit': 'index',
        'description': '小型株指数。景気感応度が高くリスクオン/オフを反映。',
        'display_order': 455,
        'rule': {
            'metric': 'yoy',
            'economic': {'direction': 'higher_better', 'thresholds': [-20, -8, 5, 20]},
            'market':   {'direction': 'higher_better', 'thresholds': [-20, -8, 5, 20]},
        },
    },
]


def add_indicators(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    for entry in NEW_INDICATORS:
        Indicator.objects.update_or_create(
            fred_series_id=entry['fred_series_id'],
            defaults={
                'source': entry['source'],
                'name_ja': entry['name_ja'],
                'name_en': entry['name_en'],
                'category': entry['category'],
                'importance': entry['importance'],
                'frequency': entry['frequency'],
                'unit': entry['unit'],
                'description': entry['description'],
                'display_order': entry['display_order'],
                'is_active': True,
                'judgment_rule': entry['rule'],
            },
        )


def remove_indicators(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    series_ids = [e['fred_series_id'] for e in NEW_INDICATORS]
    Indicator.objects.filter(fred_series_id__in=series_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0006_indicator_source'),
    ]

    operations = [
        migrations.RunPython(add_indicators, remove_indicators),
    ]
