"""Phase 5: 主要指数の価格アクション派生指標を登録する。

各指数（S&P500, 日経225, NYダウ, NASDAQ）について以下の 3 指標を作成する。
- DD200: 200日移動平均からの乖離率（%）
- DD52W: 52週高値からの下落率（%）
- MOM20: 20営業日リターン（%）

`yfinance_daily` ソースから price_action_client が日次で算出する。
"""

from django.db import migrations


SYMBOLS = [
    {'key': 'GSPC', 'jp': 'S&P500', 'display_base': 600},
    {'key': 'N225', 'jp': '日経225', 'display_base': 603},
    {'key': 'DJI', 'jp': 'NYダウ', 'display_base': 606},
    {'key': 'IXIC', 'jp': 'NASDAQ', 'display_base': 609},
]


def _build_entries():
    entries = []
    for sym in SYMBOLS:
        entries.append({
            'fred_series_id': f"PA_{sym['key']}_DD200",
            'name_ja': f"{sym['jp']} 200日線乖離",
            'name_en': f"{sym['key']} % vs 200DMA",
            'unit': '%',
            'description': (
                f"{sym['jp']}の終値が 200日移動平均から何 % 離れているか。"
                "負値は弱気（200日線割れ）。"
            ),
            'display_order': sym['display_base'],
            'value_min': -90.0,
            'value_max': 200.0,
            'rule': {
                'metric': 'level',
                'economic': {'direction': 'higher_better', 'thresholds': [-10, -5, 5, 15]},
                'market':   {'direction': 'higher_better', 'thresholds': [-10, -5, 5, 15]},
            },
        })
        entries.append({
            'fred_series_id': f"PA_{sym['key']}_DD52W",
            'name_ja': f"{sym['jp']} 52週高値乖離",
            'name_en': f"{sym['key']} drawdown from 52w high",
            'unit': '%',
            'description': (
                f"{sym['jp']}の終値が直近 52 週高値からどれだけ下げているか（負値）。"
                "0 に近いほど高値圏、-20% 以下は調整局面。"
            ),
            'display_order': sym['display_base'] + 1,
            'value_min': -100.0,
            'value_max': 0.5,
            'rule': {
                'metric': 'level',
                'economic': {'direction': 'higher_better', 'thresholds': [-20, -10, -5, -1]},
                'market':   {'direction': 'higher_better', 'thresholds': [-20, -10, -5, -1]},
            },
        })
        entries.append({
            'fred_series_id': f"PA_{sym['key']}_MOM20",
            'name_ja': f"{sym['jp']} 20日リターン",
            'name_en': f"{sym['key']} 20-day return",
            'unit': '%',
            'description': (
                f"{sym['jp']}の直近 20 営業日リターン（%）。"
                "急落局面では強い負値となり初動シグナルになる。"
            ),
            'display_order': sym['display_base'] + 2,
            'value_min': -90.0,
            'value_max': 100.0,
            'rule': {
                'metric': 'level',
                'economic': {'direction': 'higher_better', 'thresholds': [-10, -5, 0, 5]},
                'market':   {'direction': 'higher_better', 'thresholds': [-10, -5, 0, 5]},
            },
        })
    return entries


def add_indicators(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    for entry in _build_entries():
        Indicator.objects.update_or_create(
            fred_series_id=entry['fred_series_id'],
            defaults={
                'source': 'yfinance_daily',
                'name_ja': entry['name_ja'],
                'name_en': entry['name_en'],
                'category': 'market',
                'importance': 'C',
                'frequency': 'daily',
                'unit': entry['unit'],
                'description': entry['description'],
                'display_order': entry['display_order'],
                'is_active': True,
                'judgment_rule': entry['rule'],
                'value_min': entry['value_min'],
                'value_max': entry['value_max'],
            },
        )


def remove_indicators(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    series_ids = [e['fred_series_id'] for e in _build_entries()]
    Indicator.objects.filter(fred_series_id__in=series_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('macro', '0010_indicator_source_yfinance_daily'),
    ]

    operations = [
        migrations.RunPython(add_indicators, remove_indicators),
    ]
