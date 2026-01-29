import csv
import logging
import os
from datetime import date, datetime

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render

logger = logging.getLogger(__name__)

GUIDANCE_LABELS = {
    'up': '上振れ',
    'flat': '維持',
    'down': '下振れ',
}

GUIDANCE_ICONS = {
    'up': 'bi-arrow-up-right',
    'flat': 'bi-dash',
    'down': 'bi-arrow-down-right',
}

GUIDANCE_DIRECTION_LABELS = {
    'up': '上方',
    'flat': '維持',
    'down': '下方',
}

RATING_CHANGE_LABELS = {
    'upgrade': '格上げ',
    'unchanged': '据え置き',
    'downgrade': '格下げ',
}

RATING_CHANGE_ICONS = {
    'upgrade': 'bi-arrow-up',
    'unchanged': 'bi-dash',
    'downgrade': 'bi-arrow-down',
}

STATUS_CLASS_MAP = {
    'up': 'status-up',
    'flat': 'status-flat',
    'down': 'status-down',
}

RATING_STATUS_CLASS_MAP = {
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


def parse_risk_tags(value):
    if not value:
        return []
    text = str(value).replace(',', '、')
    return [tag.strip() for tag in text.split('、') if tag.strip()]


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


def accuracy_class(value):
    if value is None:
        return 'metric-muted'
    if value >= 75:
        return 'metric-positive'
    if value <= 45:
        return 'metric-negative'
    return 'metric-neutral'


def score_from_value(value):
    if value is None:
        return 0
    if value >= 15:
        return 12
    if value >= 5:
        return 7
    if value >= 0:
        return 3
    if value <= -5:
        return -10
    return -5


def compute_rating_score(item):
    score = 50
    score += score_from_value(item.get('revenue_growth_value'))
    score += score_from_value(item.get('eps_growth_value'))
    surprise = item.get('surprise_rate_value')
    if surprise is not None:
        if surprise >= 5:
            score += 6
        elif surprise <= -5:
            score -= 6
    guidance_surprise = item.get('guidance_surprise')
    if guidance_surprise == 'up':
        score += 7
    elif guidance_surprise == 'down':
        score -= 7
    guidance_direction = item.get('guidance_direction')
    if guidance_direction == 'up':
        score += 4
    elif guidance_direction == 'down':
        score -= 4
    rating_change = item.get('rating_change')
    if rating_change == 'upgrade':
        score += 4
    elif rating_change == 'downgrade':
        score -= 4
    accuracy = item.get('forecast_accuracy_value')
    if accuracy is not None:
        if accuracy >= 75:
            score += 3
        elif accuracy <= 45:
            score -= 3
    return max(0, min(100, score))


def rating_bucket(score):
    if score >= 70:
        return 'strong', '好調'
    if score >= 50:
        return 'neutral', '普通'
    return 'weak', '不調'


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
                    guidance_surprise = normalize_choice(
                        row.get('guidance_surprise'),
                        {'up', 'flat', 'down'},
                        'flat',
                    )
                    surprise_rate = parse_float(row.get('surprise_rate'))
                    next_consensus = (row.get('next_consensus') or '').strip()
                    forecast_accuracy = parse_float(row.get('forecast_accuracy'))
                    guidance_direction = normalize_choice(
                        row.get('guidance_direction'),
                        {'up', 'flat', 'down'},
                        'flat',
                    )
                    rating_change = normalize_choice(
                        row.get('rating_change'),
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
                    fiscal_period = (row.get('fiscal_period') or '').strip()

                    summary = (row.get('summary') or '').strip()
                    risk_tags = parse_risk_tags(row.get('risk_factors'))
                    
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
                        'guidance_surprise': guidance_surprise,
                        'surprise_rate_value': surprise_rate,
                        'next_consensus': next_consensus,
                        'forecast_accuracy_value': forecast_accuracy,
                        'guidance_direction': guidance_direction,
                        'rating_change': rating_change,
                        'sales_current': sales_current,
                        'sales_forecast': sales_forecast,
                        'sales_4q_ago': sales_4q_ago,
                        'sales_4q_prior_period': sales_4q_prior_period,
                        'sales_surprise': sales_surprise,
                        'eps_current': eps_current,
                        'eps_forecast': eps_forecast,
                        'eps_4q_ago': eps_4q_ago,
                        'eps_4q_prior_period': eps_4q_prior_period,
                        'eps_surprise': eps_surprise,
                        'fiscal_period': fiscal_period,
                        'summary': summary,
                        'risk_tags': risk_tags,
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

            score = compute_rating_score(item)
            rating_key, rating_label = rating_bucket(score)

            revenue_value = item.get('revenue_growth_value')
            eps_value = item.get('eps_growth_value')
            surprise_value = item.get('surprise_rate_value')
            accuracy_value = item.get('forecast_accuracy_value')

            # Parse new surprise values for display
            sales_surprise_val = parse_float(item.get('sales_surprise'))
            eps_surprise_val = parse_float(item.get('eps_surprise'))

            guidance_surprise = item.get('guidance_surprise')
            guidance_direction = item.get('guidance_direction')
            rating_change = item.get('rating_change')

            surprise_meter = None
            if surprise_value is not None:
                clamped = max(-10, min(10, surprise_value))
                surprise_meter = int(round((clamped + 10) * 5))

            summary_text = item.get('summary') or '要約未取得'
            risk_tags = item.get('risk_tags') or []

            countdown_label = '日程未定'
            if days_to_earnings is not None:
                if days_to_earnings <= 0:
                    countdown_label = '本日'
                else:
                    countdown_label = f'あと{days_to_earnings}日'

            item.update({
                'days_to_earnings': days_to_earnings,
                'days_to_earnings_sort': days_to_earnings if days_to_earnings is not None else 9999,
                'rating_score': score,
                'rating_key': rating_key,
                'rating_label': rating_label,
                'revenue_growth_display': format_percent(revenue_value),
                'revenue_growth_class': metric_class(revenue_value),
                'eps_growth_display': format_percent(eps_value),
                'eps_growth_class': metric_class(eps_value),
                'surprise_display': format_percent(surprise_value),
                'surprise_class': metric_class(surprise_value),
                'surprise_meter': surprise_meter,
                'forecast_accuracy_display': '—' if accuracy_value is None else f'{accuracy_value:.0f}%',
                'forecast_accuracy_class': accuracy_class(accuracy_value),
                'guidance_surprise_label': GUIDANCE_LABELS.get(guidance_surprise, '維持'),
                'guidance_surprise_class': STATUS_CLASS_MAP.get(guidance_surprise, 'status-flat'),
                'guidance_surprise_icon': GUIDANCE_ICONS.get(guidance_surprise, 'bi-dash'),
                'guidance_direction_label': GUIDANCE_DIRECTION_LABELS.get(guidance_direction, '維持'),
                'guidance_direction_class': STATUS_CLASS_MAP.get(guidance_direction, 'status-flat'),
                'guidance_direction_icon': GUIDANCE_ICONS.get(guidance_direction, 'bi-dash'),
                'rating_change_label': RATING_CHANGE_LABELS.get(rating_change, '据え置き'),
                'rating_change_class': RATING_STATUS_CLASS_MAP.get(rating_change, 'status-flat'),
                'rating_change_icon': RATING_CHANGE_ICONS.get(rating_change, 'bi-dash'),
                'sales_surprise_display': format_percent(sales_surprise_val),
                'sales_surprise_class': metric_class(sales_surprise_val),
                'eps_surprise_display': format_percent(eps_surprise_val),
                'eps_surprise_class': metric_class(eps_surprise_val),
                'summary': summary_text,
                'risk_tags': risk_tags,
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

    summary = {
        'total': 0,
        'strong': 0,
        'neutral': 0,
        'weak': 0,
        'guidance_up': 0,
        'guidance_down': 0,
        'upcoming_week': 0,
    }

    for group in grouped_earnings or []:
        for item in group.get('companies', []):
            summary['total'] += 1
            rating_key = item.get('rating_key')
            if rating_key == 'strong':
                summary['strong'] += 1
            elif rating_key == 'weak':
                summary['weak'] += 1
            else:
                summary['neutral'] += 1

            guidance_direction = item.get('guidance_direction')
            if guidance_direction == 'up':
                summary['guidance_up'] += 1
            elif guidance_direction == 'down':
                summary['guidance_down'] += 1

            days_to_earnings = item.get('days_to_earnings')
            if days_to_earnings is not None and days_to_earnings <= 7:
                summary['upcoming_week'] += 1

    if summary['strong'] > summary['weak']:
        insight = '好調見通しが優勢'
    elif summary['weak'] > summary['strong']:
        insight = '慎重見通しが優勢'
    else:
        insight = '好調・不調が拮抗'

    subtext = (
        f"上方ガイダンス {summary['guidance_up']}件 / "
        f"下方 {summary['guidance_down']}件 ・ "
        f"7日以内 {summary['upcoming_week']}件"
    )

    context = {
        'earnings_data': grouped_earnings,
        'updated_date': date.today().strftime('%Y.%m.%d'),
        'earnings_summary': {
            **summary,
            'insight': insight,
            'subtext': subtext,
        }
    }
    
    return render(request, 'earning/index.html', context)
