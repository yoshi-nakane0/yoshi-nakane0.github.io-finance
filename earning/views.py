import csv
import logging
import os
from datetime import date, datetime

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render

logger = logging.getLogger(__name__)

FUNDAMENTAL_LABELS = {
    'up': '上振れ',
    'flat': '維持',
    'down': '下振れ',
}

FUNDAMENTAL_ICONS = {
    'up': 'bi-arrow-up-right',
    'flat': 'bi-dash',
    'down': 'bi-arrow-down-right',
}

DIRECTION_LABELS = {
    'up': '上方',
    'flat': '維持',
    'down': '下方',
}

DIRECTION_ICONS = FUNDAMENTAL_ICONS

SENTIMENT_LABELS = {
    'upgrade': '格上げ',
    'unchanged': '据え置き',
    'downgrade': '格下げ',
}

SENTIMENT_ICONS = {
    'upgrade': 'bi-arrow-up',
    'unchanged': 'bi-dash',
    'downgrade': 'bi-arrow-down',
}

STATUS_CLASS_MAP = {
    'up': 'status-up',
    'flat': 'status-flat',
    'down': 'status-down',
}

SENTIMENT_STATUS_CLASS_MAP = {
    'upgrade': 'status-up',
    'unchanged': 'status-flat',
    'downgrade': 'status-down',
}

DEFAULT_TREND_POINTS = [0.0, 0.0, 0.0, 0.0]


def parse_float(value):
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def parse_trend_points(value):
    if not value:
        return []
    points = []
    for part in str(value).replace(' ', '').split(','):
        if not part:
            continue
        try:
            points.append(float(part))
        except ValueError:
            continue
    return points


def normalize_choice(value, choices, default):
    if not value:
        return default
    value = str(value).strip().lower()
    return value if value in choices else default


def format_percent(value):
    if value is None:
        return '—'
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.1f}%'


def metric_class(value):
    if value is None:
        return 'metric-muted'
    if value > 0:
        return 'metric-positive'
    if value < 0:
        return 'metric-negative'
    return 'metric-neutral'


def risk_class(value):
    if value is None:
        return 'metric-muted'
    if value >= 75:
        return 'metric-positive'
    if value <= 45:
        return 'metric-negative'
    return 'metric-neutral'


def fetch_earnings_from_csv():
    """
    static/earning/data/data.csvから決算データを読み込む
    """
    try:
        # CSVファイルのパスを構築
        csv_path = os.path.join(settings.BASE_DIR, 'static', 'earning', 'data', 'data.csv')
        
        if not os.path.exists(csv_path):
            logger.warning("CSV file not found at %s", csv_path)
            return []
        
        earnings_data = []
        
        with open(csv_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            
            for row in reader:
                try:
                    # CSVの各行をパース
                    date_str = (row.get('date') or '').strip()
                    market = (row.get('market') or '').strip()
                    symbol = (row.get('symbol') or '').strip()
                    company = (row.get('company') or '').strip()
                    industry = (row.get('industry') or '').strip()
                    revenue_growth = parse_float(row.get('revenue_growth'))
                    eps_growth = parse_float(row.get('eps_growth'))
                    fundamental = normalize_choice(
                        row.get('Fundamental'),
                        {'up', 'flat', 'down'},
                        'flat',
                    )
                    surprise_rate = parse_float(row.get('surprise_rate'))
                    next_consensus = (row.get('next_consensus') or '').strip()
                    risk_value = parse_float(row.get('Risk'))
                    direction = normalize_choice(
                        row.get('Direction'),
                        {'up', 'flat', 'down'},
                        'flat',
                    )
                    sentiment = normalize_choice(
                        row.get('Sentiment'),
                        {'upgrade', 'unchanged', 'downgrade'},
                        'unchanged',
                    )
                    
                    # New Absolute Values
                    sales_current = row.get('sales_current')
                    sales_forecast = row.get('sales_forecast')
                    sales_4q_ago = row.get('sales_4q_ago')
                    sales_4q_prior_period = row.get('sales_4q_prior_period')

                    eps_current = row.get('eps_current')
                    eps_forecast = row.get('eps_forecast')
                    eps_4q_ago = row.get('eps_4q_ago')
                    eps_4q_prior_period = row.get('eps_4q_prior_period')

                    sales_surprise = row.get('sales_surprise')
                    eps_surprise = row.get('eps_surprise')
                    surp_4q_ago = row.get('surp_4q_ago')
                    surp_current = row.get('surp_current')
                    surp_4q_prior_period = row.get('surp_4q_prior_period')
                    surp_eps_4q_ago = row.get('surp_eps_4q_ago')
                    surp_eps_current = row.get('surp_eps_current')
                    surp_eps_4q_prior_period = row.get('surp_eps_4q_prior_period')
                    fiscal_period = (row.get('fiscal_period') or '').strip()

                    summary = (row.get('summary') or '').strip()
                    
                    # 必須フィールドのチェック
                    if not all([date_str, symbol, company]):
                        continue
                    
                    # 日付の形式をチェック
                    earnings_date = '決算日未定'
                    if date_str and date_str != '決算日未定':
                        try:
                            datetime.strptime(date_str, '%Y-%m-%d')
                            earnings_date = date_str
                        except ValueError:
                            logger.warning("Invalid date format for %s: %s", symbol, date_str)
                            earnings_date = '決算日未定'
                    
                    earnings_data.append({
                        'date': earnings_date,
                        'company': company,
                        'industry': industry,
                        'market': market,
                        'symbol': symbol,
                        'revenue_growth_value': revenue_growth,
                        'eps_growth_value': eps_growth,
                        'fundamental': fundamental,
                        'surprise_rate_value': surprise_rate,
                        'next_consensus': next_consensus,
                        'risk_value': risk_value,
                        'direction': direction,
                        'sentiment': sentiment,
                        'sales_current': sales_current,
                        'sales_forecast': sales_forecast,
                        'sales_4q_ago': sales_4q_ago,
                        'sales_4q_prior_period': sales_4q_prior_period,
                        'sales_surprise': sales_surprise,
                        'surp_forecast': '-',
                        'surp_4q_ago': surp_4q_ago,
                        'surp_current': surp_current,
                        'surp_4q_prior_period': surp_4q_prior_period,
                        'eps_current': eps_current,
                        'eps_forecast': eps_forecast,
                        'eps_4q_ago': eps_4q_ago,
                        'eps_4q_prior_period': eps_4q_prior_period,
                        'eps_surprise': eps_surprise,
                        'surp_eps_forecast': '-',
                        'surp_eps_4q_ago': surp_eps_4q_ago,
                        'surp_eps_current': surp_eps_current,
                        'surp_eps_4q_prior_period': surp_eps_4q_prior_period,
                        'fiscal_period': fiscal_period,
                        'summary': summary,
                    })
                    
                except Exception as e:
                    logger.warning("Error parsing CSV row %s: %s", row, e)
                    continue

        logger.info("CSV: Successfully loaded %s earnings announcements", len(earnings_data))
        return earnings_data
        
    except Exception as e:
        logger.warning("Error reading CSV file: %s", e)
        return []

def index(request):
    """
    決算カレンダーページの表示
    """
    # キャッシュキー
    cache_key = 'earnings_data_grouped_v2'
    # キャッシュからデータを取得
    # grouped_earnings = cache.get(cache_key)
    grouped_earnings = None # Force refresh for dev

    if grouped_earnings is None:
        earnings_data = fetch_earnings_from_csv()

        today = date.today()

        future_earnings = []
        for item in earnings_data:
            if item['date'] == '決算日未定':
                future_earnings.append(item)
            else:
                try:
                    earnings_date = datetime.strptime(item['date'], '%Y-%m-%d').date()
                    if earnings_date >= today:
                        future_earnings.append(item)
                except ValueError:
                    continue

        try:
            def sort_key(x):
                if x['date'] == '決算日未定':
                    return datetime.max
                try:
                    return datetime.strptime(x['date'], '%Y-%m-%d')
                except ValueError:
                    return datetime.max
            future_earnings.sort(key=sort_key)
        except Exception as e:
            logger.warning("Error sorting data: %s", e)

        def enrich_item(item):
            earnings_date = None
            if item['date'] != '決算日未定':
                try:
                    earnings_date = datetime.strptime(item['date'], '%Y-%m-%d').date()
                except ValueError:
                    earnings_date = None

            days_to_earnings = None
            if earnings_date:
                days_to_earnings = (earnings_date - today).days

            revenue_value = item.get('revenue_growth_value')
            eps_value = item.get('eps_growth_value')
            surprise_value = item.get('surprise_rate_value')
            risk_value = item.get('risk_value')

            # Parse new surprise values for display
            sales_surprise_val = parse_float(item.get('sales_surprise'))
            eps_surprise_val = parse_float(item.get('eps_surprise'))

            fundamental = item.get('fundamental')
            direction = item.get('direction')
            sentiment = item.get('sentiment')

            surprise_meter = None
            if surprise_value is not None:
                clamped = max(-10, min(10, surprise_value))
                surprise_meter = int(round((clamped + 10) * 5))

            summary_text = item.get('summary') or '要約未取得'

            countdown_label = '日程未定'
            if days_to_earnings is not None:
                if days_to_earnings <= 0:
                    countdown_label = '本日'
                else:
                    countdown_label = f'あと{days_to_earnings}日'

            item.update({
                'days_to_earnings': days_to_earnings,
                'days_to_earnings_sort': days_to_earnings if days_to_earnings is not None else 9999,
                'revenue_growth_display': format_percent(revenue_value),
                'revenue_growth_class': metric_class(revenue_value),
                'eps_growth_display': format_percent(eps_value),
                'eps_growth_class': metric_class(eps_value),
                'surprise_display': format_percent(surprise_value),
                'surprise_class': metric_class(surprise_value),
                'surprise_meter': surprise_meter,
                'risk_display': '—' if risk_value is None else f'{risk_value:.0f}%',
                'risk_class': risk_class(risk_value),
                'fundamental_label': FUNDAMENTAL_LABELS.get(fundamental, '維持'),
                'fundamental_class': STATUS_CLASS_MAP.get(fundamental, 'status-flat'),
                'fundamental_icon': FUNDAMENTAL_ICONS.get(fundamental, 'bi-dash'),
                'direction_label': DIRECTION_LABELS.get(direction, '維持'),
                'direction_class': STATUS_CLASS_MAP.get(direction, 'status-flat'),
                'direction_icon': DIRECTION_ICONS.get(direction, 'bi-dash'),
                'sentiment_label': SENTIMENT_LABELS.get(sentiment, '据え置き'),
                'sentiment_class': SENTIMENT_STATUS_CLASS_MAP.get(sentiment, 'status-flat'),
                'sentiment_icon': SENTIMENT_ICONS.get(sentiment, 'bi-dash'),
                'sales_surprise_display': format_percent(sales_surprise_val),
                'sales_surprise_class': metric_class(sales_surprise_val),
                'eps_surprise_display': format_percent(eps_surprise_val),
                'eps_surprise_class': metric_class(eps_surprise_val),
                'summary': summary_text,
                'fiscal_period': item.get('fiscal_period') or '—',
                'countdown_label': countdown_label,
                'is_soon': days_to_earnings is not None and days_to_earnings <= 7,
                'market_label': 'JP' if item.get('market') == 'TSE' else 'US / Global',
            })

        for item in future_earnings:
            enrich_item(item)

        grouped_earnings = []
        if future_earnings:
            try:
                current_date = None
                current_companies = []

                for item in future_earnings:
                    if current_date != item['date']:
                        if current_date is not None:
                            grouped_earnings.append({
                                'date': current_date,
                                'companies': current_companies
                            })
                        current_date = item['date']
                        current_companies = []
                    current_companies.append(item)

                if current_date is not None:
                    grouped_earnings.append({
                        'date': current_date,
                        'companies': current_companies
                    })
            except Exception as e:
                logger.warning("Error grouping data: %s", e)
                for item in future_earnings:
                    grouped_earnings.append({
                        'date': item['date'],
                        'companies': [item]
                    })

        cache.set(cache_key, grouped_earnings, 86400)

    context = {
        'earnings_data': grouped_earnings,
        'updated_date': date.today().strftime('%Y.%m.%d'),
    }
    
    return render(request, 'earning/index.html', context)
