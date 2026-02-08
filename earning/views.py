import csv
import logging
from datetime import date, datetime

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render
from django.views.decorators.cache import cache_control
from django.views.decorators.gzip import gzip_page

logger = logging.getLogger(__name__)

FUNDAMENTAL_ICONS = {
    'up': 'bi-arrow-up-right',
    'flat': 'bi-dash',
    'down': 'bi-arrow-down-right',
}

DIRECTION_ICONS = FUNDAMENTAL_ICONS

SENTIMENT_ICONS = {
    'up': 'bi-arrow-up',
    'flat': 'bi-dash',
    'down': 'bi-arrow-down',
}

STATUS_CLASS_MAP = {
    'up': 'status-up',
    'flat': 'status-flat',
    'down': 'status-down',
}


def parse_float(value):
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def normalize_choice(value, choices, default):
    if not value:
        return default
    value = str(value).strip().lower()
    return value if value in choices else default


def risk_class(value):
    if value is None:
        return 'metric-muted'
    if value >= 75:
        return 'metric-positive'
    if value <= 45:
        return 'metric-negative'
    return 'metric-neutral'


def fetch_earnings_from_csv(csv_path):
    """
    static/earning/data/data.csvから決算データを読み込む
    """
    try:
        if not csv_path.exists():
            logger.warning("CSV file not found at %s", csv_path)
            return []

        earnings_data = []

        with csv_path.open('r', encoding='utf-8', newline='') as csvfile:
            reader = csv.DictReader(csvfile)

            for row in reader:
                try:
                    # CSVの各行をパース
                    date_str = (row.get('date') or '').strip()
                    market = (row.get('market') or '').strip()
                    symbol = (row.get('symbol') or '').strip()
                    company = (row.get('company') or '').strip()
                    industry = (row.get('industry') or '').strip()
                    fundamental = normalize_choice(
                        row.get('Fundamental'),
                        {'up', 'flat', 'down'},
                        'flat',
                    )
                    risk_value = parse_float(row.get('Risk'))
                    direction = normalize_choice(
                        row.get('Direction'),
                        {'up', 'flat', 'down'},
                        'flat',
                    )
                    sentiment = normalize_choice(
                        row.get('Sentiment'),
                        {'up', 'flat', 'down'},
                        'flat',
                    )

                    sales_current = row.get('sales_current')
                    sales_forecast = row.get('sales_forecast')
                    sales_4q_ago = row.get('sales_4q_ago')
                    sales_4q_prior_period = row.get('sales_4q_prior_period')

                    eps_current = row.get('eps_current')
                    eps_forecast = row.get('eps_forecast')
                    eps_4q_ago = row.get('eps_4q_ago')
                    eps_4q_prior_period = row.get('eps_4q_prior_period')

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

                    earnings_date_obj = None
                    earnings_date_display = '決算日未定'
                    if date_str and date_str != '決算日未定':
                        try:
                            earnings_date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                            earnings_date_display = date_str
                        except ValueError:
                            logger.warning("Invalid date format for %s: %s", symbol, date_str)

                    earnings_data.append({
                        'date': earnings_date_display,
                        'date_obj': earnings_date_obj,
                        'company': company,
                        'industry': industry,
                        'market': market,
                        'symbol': symbol,
                        'fundamental': fundamental,
                        'risk_value': risk_value,
                        'direction': direction,
                        'sentiment': sentiment,
                        'sales_current': sales_current,
                        'sales_forecast': sales_forecast,
                        'sales_4q_ago': sales_4q_ago,
                        'sales_4q_prior_period': sales_4q_prior_period,
                        'surp_forecast': '-',
                        'surp_4q_ago': surp_4q_ago,
                        'surp_current': surp_current,
                        'surp_4q_prior_period': surp_4q_prior_period,
                        'eps_current': eps_current,
                        'eps_forecast': eps_forecast,
                        'eps_4q_ago': eps_4q_ago,
                        'eps_4q_prior_period': eps_4q_prior_period,
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


def build_cache_key(today, csv_path):
    """
    「日付」と「CSV更新」で自動的に無効化されるキャッシュキー。
    仕様を変えずに、毎リクエストの再計算を避ける。
    """
    try:
        mtime_ns = int(csv_path.stat().st_mtime_ns)
    except OSError:
        mtime_ns = 0
    return f'earnings_data_grouped_v4:{today.isoformat()}:{mtime_ns}'


@cache_control(public=True, max_age=0, s_maxage=300, stale_while_revalidate=86400)
@gzip_page
def index(request):
    """
    決算カレンダーページの表示
    """
    today = date.today()
    csv_path = settings.BASE_DIR / 'static' / 'earning' / 'data' / 'data.csv'

    cache_key = build_cache_key(today, csv_path)
    cached_payload = cache.get(cache_key)

    if cached_payload is None:
        earnings_data = fetch_earnings_from_csv(csv_path)

        future_earnings = []
        completed_earnings = []
        for item in earnings_data:
            item_date = item.get('date_obj')
            if item_date is None:
                future_earnings.append(item)
            else:
                if item_date >= today:
                    future_earnings.append(item)
                else:
                    completed_earnings.append(item)

        try:
            future_earnings.sort(key=lambda x: (x.get('date_obj') is None, x.get('date_obj') or date.max))
            completed_earnings.sort(key=lambda x: x.get('date_obj') or date.min, reverse=True)
        except Exception as e:
            logger.warning("Error sorting data: %s", e)

        def enrich_item(item):
            risk_value = item.get('risk_value')

            fundamental = item.get('fundamental')
            direction = item.get('direction')
            sentiment = item.get('sentiment')

            summary_text = item.get('summary') or '要約未取得'

            item.update({
                'risk_display': '—' if risk_value is None else f'{risk_value:.0f}%',
                'risk_class': risk_class(risk_value),
                'fundamental_class': STATUS_CLASS_MAP.get(fundamental, 'status-flat'),
                'fundamental_icon': FUNDAMENTAL_ICONS.get(fundamental, 'bi-dash'),
                'direction_class': STATUS_CLASS_MAP.get(direction, 'status-flat'),
                'direction_icon': DIRECTION_ICONS.get(direction, 'bi-dash'),
                'sentiment_class': STATUS_CLASS_MAP.get(sentiment, 'status-flat'),
                'sentiment_icon': SENTIMENT_ICONS.get(sentiment, 'bi-dash'),
                'summary': summary_text,
                'fiscal_period': item.get('fiscal_period') or '—',
            })

        for item in future_earnings:
            enrich_item(item)
        for item in completed_earnings:
            enrich_item(item)

        def group_by_date(items, is_past=False):
            grouped = []
            if not items:
                return grouped
            try:
                current_date = None
                current_companies = []

                for item in items:
                    if current_date != item['date']:
                        if current_date is not None:
                            grouped.append({
                                'date': current_date,
                                'companies': current_companies,
                                'is_past': is_past,
                            })
                        current_date = item['date']
                        current_companies = []
                    current_companies.append(item)

                if current_date is not None:
                    grouped.append({
                        'date': current_date,
                        'companies': current_companies,
                        'is_past': is_past,
                    })
            except Exception as e:
                logger.warning("Error grouping data: %s", e)
                for item in items:
                    grouped.append({
                        'date': item['date'],
                        'companies': [item],
                        'is_past': is_past,
                    })
            return grouped

        grouped_earnings = group_by_date(future_earnings, is_past=False)
        past_earnings_data = group_by_date(completed_earnings, is_past=True)

        cache_payload = {
            'upcoming': grouped_earnings,
            'completed': past_earnings_data,
        }
        cache.set(cache_key, cache_payload, 86400)
    else:
        grouped_earnings = cached_payload.get('upcoming', [])
        past_earnings_data = cached_payload.get('completed', [])

    combined_earnings_data = grouped_earnings + past_earnings_data
    context = {
        'earnings_data': combined_earnings_data,
        'has_past_earnings': bool(past_earnings_data),
        'past_group_count': len(past_earnings_data),
        'updated_date': today.strftime('%Y.%m.%d'),
    }

    return render(request, 'earning/index.html', context)
