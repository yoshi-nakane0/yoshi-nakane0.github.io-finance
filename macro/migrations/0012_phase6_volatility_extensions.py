"""Phase 6: MOVE 指数と VIX/VIX3M 比率を追加する。

- MOVE_INDEX     : 米国債ボラティリティ。クレジット/金利の警戒度。
- VIX_VIX3M_RATIO: 短期VIXと3ヶ月VIXの比率。>1 で逆カーブ（短期 stress 集中）。

Put/Call 比率は Yahoo/Stooq とも無料で安定取得できないため、本フェーズでは未対応。
"""

from django.db import migrations


NEW_INDICATORS = [
    {
        'fred_series_id': 'MOVE_INDEX',
        'name_ja': 'MOVE指数',
        'name_en': 'ICE BofAML MOVE Index',
        'unit': 'index',
        'description': (
            '米国債のインプライドボラティリティ。金利不安が高まると上昇する。'
            '通常 60〜100、120 超で警戒、160 超で危険。'
        ),
        'display_order': 620,
        'value_min': 30.0,
        'value_max': 400.0,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [80, 100, 130, 160]},
            'market':   {'direction': 'lower_better', 'thresholds': [80, 100, 130, 160]},
        },
    },
    {
        'fred_series_id': 'VIX_VIX3M_RATIO',
        'name_ja': 'VIX/VIX3M比',
        'name_en': 'VIX / VIX3M Ratio',
        'unit': 'ratio',
        'description': (
            '短期 VIX と 3 ヶ月 VIX の比。通常は < 1（順カーブ）。'
            '1.0 超は逆カーブで短期ストレス集中、急変サイン。'
        ),
        'display_order': 621,
        'value_min': 0.3,
        'value_max': 3.0,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [0.9, 0.95, 1.0, 1.1]},
            'market':   {'direction': 'lower_better', 'thresholds': [0.9, 0.95, 1.0, 1.1]},
        },
    },
]


def add_indicators(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    for e in NEW_INDICATORS:
        Indicator.objects.update_or_create(
            fred_series_id=e['fred_series_id'],
            defaults={
                'source': 'yfinance_daily',
                'name_ja': e['name_ja'],
                'name_en': e['name_en'],
                'category': 'market',
                'importance': 'B',
                'frequency': 'daily',
                'unit': e['unit'],
                'description': e['description'],
                'display_order': e['display_order'],
                'is_active': True,
                'judgment_rule': e['rule'],
                'value_min': e['value_min'],
                'value_max': e['value_max'],
            },
        )


def remove_indicators(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    series_ids = [e['fred_series_id'] for e in NEW_INDICATORS]
    Indicator.objects.filter(fred_series_id__in=series_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0011_phase5_price_action_indicators'),
    ]

    operations = [
        migrations.RunPython(add_indicators, remove_indicators),
    ]
