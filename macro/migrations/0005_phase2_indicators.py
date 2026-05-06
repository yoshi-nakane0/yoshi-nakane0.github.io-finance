"""Phase 2: 追加 FRED 系列の登録と判定ルール設定。"""

from django.db import migrations


# 追加する24系列。Phase 2 で新規登録する。
NEW_INDICATORS = [
    # === 株価指数 ===
    {
        'fred_series_id': 'SP500', 'name_ja': 'S&P 500', 'name_en': 'S&P 500 Index',
        'category': 'market', 'importance': 'A', 'frequency': 'daily',
        'unit': 'index', 'description': '米国大型株500の代表指数。', 'display_order': 420,
        'rule': {
            'metric': 'yoy',
            'economic': {'direction': 'higher_better', 'thresholds': [-15, -5, 5, 15]},
            'market':   {'direction': 'higher_better', 'thresholds': [-15, -5, 5, 15]},
        },
    },
    {
        'fred_series_id': 'DJIA', 'name_ja': 'NYダウ', 'name_en': 'Dow Jones Industrial Average',
        'category': 'market', 'importance': 'B', 'frequency': 'daily',
        'unit': 'index', 'description': '工業30銘柄のダウ平均。', 'display_order': 430,
        'rule': {
            'metric': 'yoy',
            'economic': {'direction': 'higher_better', 'thresholds': [-15, -5, 5, 15]},
            'market':   {'direction': 'higher_better', 'thresholds': [-15, -5, 5, 15]},
        },
    },
    {
        'fred_series_id': 'NASDAQCOM', 'name_ja': 'NASDAQ総合', 'name_en': 'NASDAQ Composite Index',
        'category': 'market', 'importance': 'B', 'frequency': 'daily',
        'unit': 'index', 'description': 'ハイテク・グロース寄りの広範指数。', 'display_order': 440,
        'rule': {
            'metric': 'yoy',
            'economic': {'direction': 'higher_better', 'thresholds': [-20, -8, 5, 20]},
            'market':   {'direction': 'higher_better', 'thresholds': [-20, -8, 5, 20]},
        },
    },
    # 注：Russell 2000 は FRED の安定した無料系列がないため Phase 3 で外部ソース対応とする
    # === 金利・スプレッド ===
    {
        'fred_series_id': 'FEDFUNDS', 'name_ja': 'FF金利（月次）', 'name_en': 'Federal Funds Rate (Monthly)',
        'category': 'rates', 'importance': 'B', 'frequency': 'monthly',
        'unit': '%', 'description': '政策金利の月次平均。', 'display_order': 305,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'target_band', 'thresholds': [1.0, 2.0, 3.5, 5.0]},
            'market':   {'direction': 'lower_better', 'thresholds': [2.0, 3.0, 4.0, 5.0]},
        },
    },
    {
        'fred_series_id': 'DFII10', 'name_ja': '10年実質金利', 'name_en': '10-Year TIPS Yield',
        'category': 'rates', 'importance': 'B', 'frequency': 'daily',
        'unit': '%', 'description': '実質金利。株式割引率に直接影響。', 'display_order': 315,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'target_band', 'thresholds': [-0.5, 0.5, 1.5, 2.5]},
            'market':   {'direction': 'lower_better', 'thresholds': [0.0, 1.0, 1.8, 2.5]},
        },
    },
    {
        'fred_series_id': 'SOFR', 'name_ja': 'SOFR', 'name_en': 'Secured Overnight Financing Rate',
        'category': 'rates', 'importance': 'B', 'frequency': 'daily',
        'unit': '%', 'description': '担保付翌日物資金調達金利。LIBOR後継。', 'display_order': 325,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'target_band', 'thresholds': [1.0, 2.0, 3.5, 5.0]},
            'market':   {'direction': 'lower_better', 'thresholds': [2.0, 3.0, 4.0, 5.5]},
        },
    },
    {
        'fred_series_id': 'T10Y3M', 'name_ja': '米3M-10年スプレッド', 'name_en': '10Y-3M Treasury Spread',
        'category': 'rates', 'importance': 'A', 'frequency': 'daily',
        'unit': '%', 'description': 'NY連銀がリセッション予測に使う逆イールド指標。', 'display_order': 335,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'higher_better', 'thresholds': [-0.5, 0.0, 0.5, 1.5]},
            'market':   {'direction': 'higher_better', 'thresholds': [-0.5, 0.0, 0.5, 1.5]},
        },
    },
    # === 信用・金融状況 ===
    {
        'fred_series_id': 'BAMLC0A0CM', 'name_ja': 'IG社債スプレッド', 'name_en': 'ICE BofA US Corporate Index OAS',
        'category': 'market', 'importance': 'B', 'frequency': 'daily',
        'unit': '%', 'description': '投資適格社債のクレジット警戒度。', 'display_order': 415,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [1.0, 1.5, 2.0, 3.0]},
            'market':   {'direction': 'lower_better', 'thresholds': [1.0, 1.5, 2.0, 3.0]},
        },
    },
    {
        'fred_series_id': 'STLFSI4', 'name_ja': '金融ストレス指数', 'name_en': 'St. Louis Fed Financial Stress Index',
        'category': 'market', 'importance': 'B', 'frequency': 'weekly',
        'unit': 'index', 'description': 'セントルイス連銀の総合金融ストレス指数。', 'display_order': 460,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [-1.0, 0.0, 0.5, 1.5]},
            'market':   {'direction': 'lower_better', 'thresholds': [-1.0, 0.0, 0.5, 1.5]},
        },
    },
    {
        'fred_series_id': 'NFCI', 'name_ja': '全米金融状況指数', 'name_en': 'Chicago Fed National Financial Conditions Index',
        'category': 'market', 'importance': 'B', 'frequency': 'weekly',
        'unit': 'index', 'description': 'シカゴ連銀の金融環境指数。0より上で引き締め気味。', 'display_order': 470,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [-0.5, 0.0, 0.5, 1.0]},
            'market':   {'direction': 'lower_better', 'thresholds': [-0.5, 0.0, 0.5, 1.0]},
        },
    },
    # === 家計信用 ===
    {
        'fred_series_id': 'TOTALSL', 'name_ja': '消費者信用残高', 'name_en': 'Total Consumer Credit',
        'category': 'growth', 'importance': 'C', 'frequency': 'monthly',
        'unit': '十億$', 'description': '消費者の借入総額。', 'display_order': 260,
        'rule': {
            'metric': 'yoy',
            'economic': {'direction': 'target_band', 'thresholds': [-2.0, 2.0, 5.0, 9.0]},
            'market':   {'direction': 'target_band', 'thresholds': [-2.0, 2.0, 5.0, 9.0]},
        },
    },
    {
        'fred_series_id': 'REVOLSL', 'name_ja': 'リボ残高', 'name_en': 'Revolving Consumer Credit',
        'category': 'growth', 'importance': 'C', 'frequency': 'monthly',
        'unit': '十億$', 'description': 'クレジットカード等のリボルビング残高。', 'display_order': 270,
        'rule': {
            'metric': 'yoy',
            'economic': {'direction': 'target_band', 'thresholds': [-3.0, 2.0, 7.0, 12.0]},
            'market':   {'direction': 'target_band', 'thresholds': [-3.0, 2.0, 7.0, 12.0]},
        },
    },
    {
        'fred_series_id': 'TDSP', 'name_ja': '家計債務返済比率', 'name_en': 'Household Debt Service Payments / Disposable Income',
        'category': 'growth', 'importance': 'C', 'frequency': 'quarterly',
        'unit': '%', 'description': '家計が可処分所得のうち返済に充てる割合。', 'display_order': 280,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [9.0, 10.0, 11.0, 13.0]},
            'market':   {'direction': 'lower_better', 'thresholds': [9.0, 10.0, 11.0, 13.0]},
        },
    },
    # === 延滞・貸倒 ===
    {
        'fred_series_id': 'DRCCLACBS', 'name_ja': 'カード延滞率', 'name_en': 'Delinquency Rate on Credit Card Loans',
        'category': 'market', 'importance': 'C', 'frequency': 'quarterly',
        'unit': '%', 'description': '商業銀行のクレジットカード延滞率。', 'display_order': 480,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [2.0, 2.5, 3.5, 5.0]},
            'market':   {'direction': 'lower_better', 'thresholds': [2.0, 2.5, 3.5, 5.0]},
        },
    },
    {
        'fred_series_id': 'CORCCACBS', 'name_ja': 'カード貸倒償却率', 'name_en': 'Charge-Off Rate on Credit Card Loans',
        'category': 'market', 'importance': 'C', 'frequency': 'quarterly',
        'unit': '%', 'description': 'クレジットカードの貸倒償却率。', 'display_order': 490,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [2.0, 3.0, 4.5, 7.0]},
            'market':   {'direction': 'lower_better', 'thresholds': [2.0, 3.0, 4.5, 7.0]},
        },
    },
    # === 銀行貸出 ===
    {
        'fred_series_id': 'DRTSCILM', 'name_ja': 'SLOOS：大企業向け融資基準', 'name_en': 'Net % Tightening Standards: C&I Loans Large/Medium',
        'category': 'market', 'importance': 'B', 'frequency': 'quarterly',
        'unit': '%', 'description': '銀行融資基準の変化。プラス＝引き締め＝景気悪化先行。', 'display_order': 500,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [-10, 0, 15, 35]},
            'market':   {'direction': 'lower_better', 'thresholds': [-10, 0, 15, 35]},
        },
    },
    {
        'fred_series_id': 'BUSLOANS', 'name_ja': '商業銀行事業貸出', 'name_en': 'Commercial and Industrial Loans, All Commercial Banks',
        'category': 'growth', 'importance': 'C', 'frequency': 'weekly',
        'unit': '十億$', 'description': '銀行から事業者への貸出残高。', 'display_order': 290,
        'rule': {
            'metric': 'yoy',
            'economic': {'direction': 'higher_better', 'thresholds': [-5.0, -1.0, 3.0, 8.0]},
            'market':   {'direction': 'higher_better', 'thresholds': [-5.0, -1.0, 3.0, 8.0]},
        },
    },
    # === 景気 ===
    {
        'fred_series_id': 'PERMIT', 'name_ja': '住宅建設許可', 'name_en': 'New Private Housing Units Authorized by Permits',
        'category': 'growth', 'importance': 'B', 'frequency': 'monthly',
        'unit': '千件', 'description': '住宅市場の先行指標。HOUSTより先行。', 'display_order': 255,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'higher_better', 'thresholds': [900, 1200, 1500, 1800]},
            'market':   {'direction': 'higher_better', 'thresholds': [900, 1200, 1500, 1800]},
        },
    },
    # === インフレ ===
    {
        'fred_series_id': 'T10YIE', 'name_ja': '10年期待インフレ率', 'name_en': '10-Year Breakeven Inflation Rate',
        'category': 'inflation', 'importance': 'B', 'frequency': 'daily',
        'unit': '%', 'description': '債券市場が織り込む10年先の期待インフレ率。', 'display_order': 60,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'target_band', 'thresholds': [1.0, 1.8, 2.5, 3.5]},
            'market':   {'direction': 'target_band', 'thresholds': [1.0, 1.8, 2.5, 3.5]},
        },
    },
    # === 流動性 ===
    {
        'fred_series_id': 'WALCL', 'name_ja': 'Fed総資産', 'name_en': 'Total Assets: Federal Reserve',
        'category': 'rates', 'importance': 'B', 'frequency': 'weekly',
        'unit': '百万$', 'description': 'Fedバランスシート規模。流動性の源泉。', 'display_order': 360,
        'rule': {
            'metric': 'yoy',
            'economic': {'direction': 'higher_better', 'thresholds': [-15.0, -5.0, 0.0, 8.0]},
            'market':   {'direction': 'higher_better', 'thresholds': [-15.0, -5.0, 0.0, 8.0]},
        },
    },
    {
        'fred_series_id': 'WRESBAL', 'name_ja': '準備預金', 'name_en': 'Reserve Balances with Federal Reserve',
        'category': 'rates', 'importance': 'C', 'frequency': 'weekly',
        'unit': '百万$', 'description': '銀行が連銀に置く準備預金。減少すると流動性ひっ迫リスク。', 'display_order': 370,
        'rule': {
            'metric': 'yoy',
            'economic': {'direction': 'higher_better', 'thresholds': [-30.0, -15.0, 0.0, 15.0]},
            'market':   {'direction': 'higher_better', 'thresholds': [-30.0, -15.0, 0.0, 15.0]},
        },
    },
    {
        'fred_series_id': 'RRPONTSYD', 'name_ja': 'リバースレポ', 'name_en': 'Overnight Reverse Repo Operations',
        'category': 'rates', 'importance': 'C', 'frequency': 'daily',
        'unit': '十億$', 'description': '短期市場の余剰資金量。減少すると流動性が市場へ流入。', 'display_order': 380,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'lower_better', 'thresholds': [100, 500, 1500, 2300]},
            'market':   {'direction': 'lower_better', 'thresholds': [100, 500, 1500, 2300]},
        },
    },
    {
        'fred_series_id': 'WTREGEN', 'name_ja': '財務省一般口座（TGA）', 'name_en': 'Treasury General Account',
        'category': 'rates', 'importance': 'C', 'frequency': 'weekly',
        'unit': '百万$', 'description': '財務省が連銀に置く現金残高。残高変動が市場流動性に影響。', 'display_order': 390,
        'rule': {
            'metric': 'level',
            'economic': {'direction': 'target_band', 'thresholds': [200000, 400000, 700000, 900000]},
            'market':   {'direction': 'target_band', 'thresholds': [200000, 400000, 700000, 900000]},
        },
    },
]


def add_indicators(apps, schema_editor):
    Indicator = apps.get_model('macro', 'Indicator')
    for entry in NEW_INDICATORS:
        Indicator.objects.update_or_create(
            fred_series_id=entry['fred_series_id'],
            defaults={
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
        ('macro', '0004_indicator_judgment_rule'),
    ]

    operations = [
        migrations.RunPython(add_indicators, remove_indicators),
    ]
