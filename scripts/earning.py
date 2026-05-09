import os
import sys
import re
import shutil
import datetime
from pathlib import Path

import os as _os
import sys as _sys
import django as _django

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))
_os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
_django.setup()

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ────────────────────────────────────────────────
# パス設定
# ────────────────────────────────────────────────
BASE_DIR = Path('/Users/naka/yoshi-nakane0.github.io-finance/static/earning/data')
SOURCE_CSV = BASE_DIR / 'data.csv'     # コピー元
WORK_CSV = Path('data.csv')            # スクリプト実行フォルダー直下
FINAL_CSV = BASE_DIR / 'data.csv'      # 完成データの保存先（上書き）

EPS_TARGET_PERIODS = []

SALES_TARGET_PERIODS = []

EPS_COLUMNS = {period: f'eps_{suffix}' for period, suffix in EPS_TARGET_PERIODS}
SALES_COLUMNS = {period: f'sales_{suffix}' for period, suffix in SALES_TARGET_PERIODS}
PERIOD_COLUMN = 'fiscal_period'
FORECAST_COLUMNS = [
    'eps_forecast',
    'sales_forecast',
    'eps_4q_ago',
    'sales_4q_ago',
    'eps_4q_prior_period',
    'sales_4q_prior_period',
]
SURP_REVENUE_COLUMNS = [
    'surp_4q_ago',
    'surp_current',
    'surp_4q_prior_period',
]
SURP_EPS_COLUMNS = [
    'surp_eps_4q_ago',
    'surp_eps_current',
    'surp_eps_4q_prior_period',
]

EARNINGS_URL_TEMPLATE = (
    'https://jp.tradingview.com/symbols/{market}-{symbol}/financials-earnings/'
    '?earnings-period=FQ&revenues-period=FQ'
)

VALUES_SELECTOR = '[class*="values-"]'

_PERIOD_LABEL_PATTERNS = [
    re.compile(r'(?P<year>20\d{2})\s*年?\s*第\s*(?P<q>[1-4])\s*四半期', re.I),
    re.compile(r'第\s*(?P<q>[1-4])\s*四半期\s*(?P<year>20\d{2})', re.I),
    re.compile(r'(?P<year>20\d{2})\s*[-/ ]?Q\s*(?P<q>[1-4])', re.I),
    re.compile(r'Q\s*(?P<q>[1-4])\s*[-/ ]?(?P<year>20\d{2})', re.I),
    re.compile(r'(?P<year>20\d{2})\s*(?P<q>[1-4])Q', re.I),
    re.compile(r'(?P<q>[1-4])Q\s*(?P<year>20\d{2})', re.I),
    re.compile(r"(?:')?(?P<year>\d{2})\s*Q\s*(?P<q>[1-4])", re.I),
    re.compile(r"Q\s*(?P<q>[1-4])\s*(?:')?(?P<year>\d{2})", re.I),
    re.compile(r"(?:')?(?P<year>\d{2})\s*(?P<q>[1-4])Q", re.I),
    re.compile(r"(?P<q>[1-4])Q\s*(?:')?(?P<year>\d{2})", re.I),
]


# ────────────────────────────────────────────────
# ユーティリティ関数
# ────────────────────────────────────────────────

def confirm_and_copy_csv():

    if not SOURCE_CSV.exists():
        print(f'[Error] ソース CSV が存在しません: {SOURCE_CSV}')
        sys.exit(1)

    if WORK_CSV.exists():
        print(f'[Info] {WORK_CSV} が既に存在します。上書きコピーします。')
    else:
        print(f'[Info] {SOURCE_CSV} を作業フォルダにコピーします。')

    shutil.copy2(SOURCE_CSV, WORK_CSV)
    print(f'[Info] {SOURCE_CSV} → {WORK_CSV} へコピーしました。')


def build_tradingview_url(market: str, symbol: str) -> str:
    market = str(market).strip()
    symbol = str(symbol).strip()
    if not market or not symbol or market == 'nan' or symbol == 'nan':
        return ''
    return EARNINGS_URL_TEMPLATE.format(market=market, symbol=symbol)


def _wait_for_values(driver, timeout: int = 25) -> bool:
    def _has_values(drv):
        for elem in drv.find_elements(By.CSS_SELECTOR, VALUES_SELECTOR):
            if elem.text.strip():
                return True
        return False

    try:
        WebDriverWait(driver, timeout).until(_has_values)
    except TimeoutException:
        return False
    return True


def load_earnings_page(driver, url: str) -> bool:
    driver.get(url)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '#js-category-content'))
        )
    except TimeoutException:
        print(f'[Warning] コンテンツが見つかりません: {url}')
        return False

    if not _wait_for_values(driver, 30):
        print(f'[Warning] EPS/売上高の値が読み込まれません: {url}')
        return False
    return True


def _normalize_earnings_date_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ''

    m = re.search(r'(\d+)\s*日後', cleaned)
    if m:
        days = int(m.group(1))
        return (datetime.date.today() + datetime.timedelta(days=days)).strftime('%Y-%m-%d')

    if cleaned.startswith('明後日'):
        return (datetime.date.today() + datetime.timedelta(days=2)).strftime('%Y-%m-%d')

    if cleaned.startswith('明日'):
        return (datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

    if cleaned.startswith('今日'):
        return datetime.date.today().strftime('%Y-%m-%d')

    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y年%m月%d日'):
        try:
            return datetime.datetime.strptime(cleaned, fmt).date().strftime('%Y-%m-%d')
        except ValueError:
            continue

    return ''


def scrape_earnings_date(driver) -> str:
    '''TradingView の銘柄ページから決算発表日を取得。取得できない場合は空文字を返す。'''
    try:
        container = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[class*="dateContainer"]'))
        )
    except TimeoutException:
        return ''

    try:
        elem = container.find_element(By.TAG_NAME, 'time')
    except NoSuchElementException:
        elem = container

    text = elem.text.strip()

    normalized = _normalize_earnings_date_text(text)
    if normalized:
        return normalized

    # datetime 属性
    attr = elem.get_attribute('datetime') or ''
    if attr:
        normalized = _normalize_earnings_date_text(attr.split('T')[0])
        if normalized:
            return normalized

    raw = text or attr
    if raw:
        print(f'[Warning] 日付の変換に失敗しました: {raw}')
    return ''


def sort_by_first_column(df: pd.DataFrame) -> pd.DataFrame:
    '''1 行目ヘッダーを固定し、2 行目以降を A 列昇順に並べ替える。'''
    header = df.iloc[:1]
    body = df.iloc[1:].copy()

    # 日付を昇順で確実に並べ替えるために datetime 化
    body[0] = pd.to_datetime(body[0], errors='coerce')
    body.sort_values(by=0, inplace=True)
    body[0] = body[0].dt.strftime('%Y-%m-%d')  # 文字列へ戻す

    return pd.concat([header, body], ignore_index=True)


def build_header_index(df: pd.DataFrame) -> dict:
    header = {}
    for i in range(len(df.columns)):
        name = str(df.iat[0, i]).strip()
        if name and name not in header:
            header[name] = i
    return header


def ensure_columns(df: pd.DataFrame, columns: list) -> None:
    header = [str(df.iat[0, i]).strip() for i in range(len(df.columns))]
    for col in columns:
        if col in header:
            continue
        new_idx = len(df.columns)
        df[new_idx] = ''
        df.iat[0, new_idx] = col
        header.append(col)


def replace_header_label(df: pd.DataFrame, old: str, new: str) -> None:
    for i in range(len(df.columns)):
        name = str(df.iat[0, i]).strip()
        if not name:
            continue
        if old in name:
            df.iat[0, i] = name.replace(old, new)


def _is_valid_number(value) -> bool:
    try:
        if value is None:
            return False
        if isinstance(value, float) and (value != value):
            return False
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _sanitize_text(value: str) -> str:
    text = str(value)
    text = text.replace('−', '-').replace('–', '-').replace('—', '-')
    return re.sub(r'[^\x20-\x7E]', '', text).strip()


def _sanitize_period_label(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ''
    text = text.replace('−', '-').replace('–', '-').replace('—', '-')
    text = text.replace('’', "'").replace('‘', "'")
    text = re.sub(r'[\u00A0\u2007\u202F]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _parse_numeric_value(value: str):
    if value is None:
        return None
    raw = _sanitize_text(value)
    if not raw or raw in {'—', '-', '–', 'N/A', 'n/a'}:
        return None

    negative = False
    if raw.startswith('(') and raw.endswith(')'):
        negative = True
        raw = raw[1:-1]

    raw = raw.replace(',', '')
    raw = re.sub(r'[^0-9.KMBTkmbt-]', '', raw)
    if not raw:
        return None

    match = re.match(r'^-?\d+(?:\.\d+)?([KMBT])?$', raw, re.I)
    if not match:
        return None

    suffix = match.group(1)
    number_text = raw[:-1] if suffix else raw
    try:
        number = float(number_text)
    except ValueError:
        return None

    if suffix:
        scale = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000, 'T': 1_000_000_000_000}
        number *= scale[suffix.upper()]

    if negative and number > 0:
        number = -number

    return number


def _normalize_period_label(label: str):
    # 決算期間の取得処理: 期間ラベルを正規化して標準形式（YYYY-Q#）に変換
    text = _sanitize_period_label(label)
    if not text:
        return None
    text = text.replace('FY', '').replace('FQ', '').strip()

    for pattern in _PERIOD_LABEL_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        year = match.group('year')
        quarter = match.group('q')
        if len(year) == 2:
            year = str(2000 + int(year))
        return f'{year}-Q{quarter}'

    return None


def _format_fiscal_period(period: str) -> str:
    if not period:
        return ''
    match = re.match(r'^(?P<year>\d{4})-Q(?P<q>[1-4])$', period)
    if not match:
        return ''
    year = int(match.group('year')) % 100
    quarter = match.group('q')
    return f"Q{quarter} '{year:02d}"


def _period_sort_key(period: str):
    match = re.match(r'^(?P<year>\d{4})-Q(?P<q>[1-4])$', period)
    if not match:
        return None
    return (int(match.group('year')), int(match.group('q')))


def _shift_period(period: str, quarters: int):
    if not period:
        return None
    match = re.match(r'^(?P<year>\d{4})-Q(?P<q>[1-4])$', period)
    if not match:
        return None
    year = int(match.group('year'))
    quarter = int(match.group('q'))
    total = year * 4 + (quarter - 1) + quarters
    if total < 0:
        return None
    new_year = total // 4
    new_quarter = (total % 4) + 1
    return f'{new_year}-Q{new_quarter}'


def _find_latest_actual_period(periods: list, actuals: list):
    if not periods or not actuals:
        return None
    for label, actual in reversed(list(zip(periods, actuals))):
        period = _normalize_period_label(label)
        if not period:
            continue
        if _parse_numeric_value(actual) is None:
            continue
        return period
    return None


def _extract_cell_texts(container, keep_empty: bool = False, sanitize_fn=None) -> list:
    if sanitize_fn is None:
        sanitize_fn = _sanitize_text

    children = container.find_elements(By.XPATH, './*')
    if children:
        values = [sanitize_fn(child.text) for child in children]
        return values if keep_empty else [value for value in values if value]

    text = sanitize_fn(container.text)
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines()]
    return lines if keep_empty else [line for line in lines if line]


def _align_values(values: list, target_len: int, align_right: bool = False) -> list:
    if len(values) >= target_len:
        return values[-target_len:] if align_right else values[:target_len]
    padding = [''] * (target_len - len(values))
    return padding + values if align_right else values + padding


def _build_period_value_map(period_labels: list, values: list) -> dict:
    if not period_labels:
        return {}
    aligned_values = _align_values(values or [], len(period_labels))
    period_map = {}
    for label, value in zip(period_labels, aligned_values):
        period = _normalize_period_label(label)
        if not period:
            continue
        period_map[period] = _parse_numeric_value(value)
    return period_map


def _format_eps(value) -> str:
    if not _is_valid_number(value):
        return ''
    return f'{float(value):.2f}'


def _format_revenue(value) -> str:
    if not _is_valid_number(value):
        return ''
    return f'{float(value) / 1_000_000_000:.2f}'


def _format_surprise(value) -> str:
    if not _is_valid_number(value):
        return ''
    return f'{float(value):+.2f}'


def _find_table_by_heading(driver, titles: list):
    try:
        root = driver.find_element(By.CSS_SELECTOR, '#js-category-content')
    except NoSuchElementException:
        return None

    for title in titles:
        try:
            heading = root.find_element(
                By.XPATH,
                f".//div[contains(@class,'heading')][.//div[normalize-space(text())='{title}']]"
            )
            return heading.find_element(
                By.XPATH,
                "./following-sibling::*//div[contains(@class,'table')][1]"
            )
        except NoSuchElementException:
            continue
    return None


def _find_horizontal_scroll_container(driver, element):
    candidates = [element]
    try:
        candidates.extend(element.find_elements(By.XPATH, './ancestor::*'))
    except Exception:
        return None

    for candidate in candidates:
        try:
            scroll_width = driver.execute_script("return arguments[0].scrollWidth", candidate)
            client_width = driver.execute_script("return arguments[0].clientWidth", candidate)
        except Exception:
            continue
        if scroll_width and client_width and scroll_width > client_width + 1:
            return candidate
    return None


def _scroll_table_to_latest(driver, table, timeout: int = 8) -> bool:
    container = _find_horizontal_scroll_container(driver, table)
    if not container:
        return False

    driver.execute_script("arguments[0].scrollLeft = arguments[0].scrollWidth", container)
    try:
        WebDriverWait(driver, timeout).until(
            lambda drv: drv.execute_script(
                "return Math.abs(arguments[0].scrollWidth - arguments[0].clientWidth - arguments[0].scrollLeft)",
                container
            ) < 2
        )
    except TimeoutException:
        return False
    return True


def scrape_report_period(driver) -> str:
    selector = '#js-category-content span[class*="data-"]'
    try:
        WebDriverWait(driver, 15).until(
            lambda drv: any(
                _normalize_period_label(_sanitize_period_label(elem.text))
                for elem in drv.find_elements(By.CSS_SELECTOR, selector)
            )
        )
    except TimeoutException:
        pass

    candidates = driver.find_elements(By.CSS_SELECTOR, selector)
    best_period = ''
    best_key = None
    for elem in candidates:
        text = _sanitize_period_label(elem.text)
        if not text:
            continue
        period_norm = _normalize_period_label(text)
        if not period_norm:
            continue
        key = _period_sort_key(period_norm)
        if key is None:
            continue
        if best_key is None or key > best_key:
            best_key = key
            best_period = period_norm
    return best_period


def _extract_table_series(table):
    # 決算期間の取得処理: テーブルから期間ラベル、実績値、予測値を抽出
    values = table.find_elements(By.CSS_SELECTOR, VALUES_SELECTOR)
    if len(values) < 2:
        return [], [], [], []
    periods = _extract_cell_texts(values[0], keep_empty=True, sanitize_fn=_sanitize_period_label)
    actuals = _extract_cell_texts(values[1], keep_empty=True)
    forecasts = _extract_cell_texts(values[2], keep_empty=True) if len(values) > 2 else []
    surprises = _extract_cell_texts(values[3], keep_empty=True) if len(values) > 3 else []
    if not periods or not actuals:
        return [], [], [], []
    period_len = len(periods)
    length = max(period_len, len(actuals), len(forecasts), len(surprises))
    periods = _align_values(periods, length)
    actuals = _align_values(actuals, length, align_right=len(actuals) < period_len)
    forecasts = _align_values(forecasts, length, align_right=len(forecasts) < period_len)
    surprises = _align_values(surprises, length, align_right=len(surprises) < period_len)
    return periods, actuals, forecasts, surprises


def _select_decision_period(periods: list, actuals: list, forecasts: list) -> str:
    # 決算期間の取得処理: 予測のみ存在する期間または最新期間を決算期間として選択
    for label, actual, forecast in zip(periods, actuals, forecasts):
        if not _normalize_period_label(label):
            continue
        if _parse_numeric_value(actual) is None and _parse_numeric_value(forecast) is not None:
            return _sanitize_text(label)
    for label in reversed(periods):
        if _normalize_period_label(label):
            return _sanitize_text(label)
    return ''


def scrape_eps_sales(driver) -> tuple:
    eps_table = _find_table_by_heading(driver, ['EPS'])
    sales_table = _find_table_by_heading(driver, ['売上', 'Revenue'])
    if not eps_table or not sales_table:
        print('[Warning] EPS/売上のテーブル取得に失敗しました。')
        return {}, {}, {}, {}, {}, {}, '', '', ''

    _scroll_table_to_latest(driver, eps_table)
    _scroll_table_to_latest(driver, sales_table)

    eps_table = _find_table_by_heading(driver, ['EPS']) or eps_table
    sales_table = _find_table_by_heading(driver, ['売上', 'Revenue']) or sales_table

    # EPS/売上の取得処理: テーブルからEPS値と売上値を取り出す
    eps_periods, eps_values, eps_forecasts, eps_surprises = _extract_table_series(eps_table)
    sales_periods, sales_values, sales_forecasts, sales_surprises = _extract_table_series(sales_table)

    if eps_periods:
        decision_periods = eps_periods
        decision_actuals = eps_values
        decision_forecasts = eps_forecasts
    else:
        decision_periods = sales_periods
        decision_actuals = sales_values
        decision_forecasts = sales_forecasts

    if not decision_periods:
        print('[Warning] 期間ラベルの取得に失敗しました。')
        return {}, {}, {}, {}, {}, {}, '', '', ''

    # 決算期間の取得処理: 期間ラベルと実績/予測から対象の決算期間を決定
    decision_period = _select_decision_period(decision_periods, decision_actuals, decision_forecasts)
    decision_period_norm = _normalize_period_label(decision_period) if decision_period else ''
    report_period_norm = scrape_report_period(driver)
    if report_period_norm:
        decision_period_norm = report_period_norm
        decision_period = _format_fiscal_period(report_period_norm) or decision_period
    current_period_norm = (
        _find_latest_actual_period(eps_periods, eps_values)
        or _find_latest_actual_period(sales_periods, sales_values)
        or ''
    )

    eps_map = _build_period_value_map(eps_periods, eps_values)
    sales_map = _build_period_value_map(sales_periods, sales_values)
    eps_forecast_map = _build_period_value_map(eps_periods, eps_forecasts)
    sales_forecast_map = _build_period_value_map(sales_periods, sales_forecasts)
    eps_surprise_map = _build_period_value_map(eps_periods, eps_surprises)
    sales_surprise_map = _build_period_value_map(sales_periods, sales_surprises)

    return (
        eps_map,
        sales_map,
        eps_forecast_map,
        sales_forecast_map,
        eps_surprise_map,
        sales_surprise_map,
        decision_period,
        decision_period_norm,
        current_period_norm,
    )

def upsert_event_to_db(row_dict):
    """Mirror the CSV row into Stock + EarningsEvent. Idempotent on (stock, fiscal_period)."""
    from earning.models import EarningsEvent, Stock

    def _s(name):
        v = row_dict.get(name)
        if v is None:
            return ''
        try:
            if pd.isna(v):
                return ''
        except (TypeError, ValueError):
            pass
        text = str(v).strip()
        return '' if text.lower() == 'nan' else text

    symbol = _s('symbol')
    market = _s('market')
    company = _s('company')
    fiscal_period = _s('fiscal_period')
    if not symbol or not market or not company or not fiscal_period:
        return None

    def _f(name):
        v = row_dict.get(name)
        if v is None:
            return None
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        text = str(v).strip()
        if not text or text.lower() == 'nan':
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    def _norm(name, allowed, default):
        v = row_dict.get(name)
        if v is None:
            return default
        try:
            if pd.isna(v):
                return default
        except (TypeError, ValueError):
            pass
        text = str(v).strip().lower()
        if not text or text == 'nan':
            return default
        return text if text in allowed else default

    def _date(name):
        import datetime as _dt
        v = row_dict.get(name)
        if v is None:
            return None
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        text = str(v).strip()
        if not text or text == '決算日未定' or text.lower() == 'nan':
            return None
        try:
            return _dt.datetime.strptime(text, '%Y-%m-%d').date()
        except ValueError:
            return None

    stock, _ = Stock.objects.update_or_create(
        symbol=symbol, market=market,
        defaults={
            'company': company,
            'industry': _s('industry'),
            'theme': _s('theme'),
            'watch_tier': _s('watch_tier'),
            'watch_role': _s('watch_role'),
            'nikkei_weight': _f('nikkei_weight'),
        },
    )

    EarningsEvent.objects.update_or_create(
        stock=stock, fiscal_period=fiscal_period,
        defaults={
            'event_date': _date('date'),
            'fundamental': _norm('Fundamental', {'up', 'flat', 'down'}, 'flat'),
            'direction': _norm('Direction', {'up', 'flat', 'down'}, 'flat'),
            'sentiment': _norm('Sentiment', {'up', 'flat', 'down'}, 'flat'),
            'risk_value': _f('Risk'),
            'eps_forecast': _s('eps_forecast'),
            'eps_4q_ago': _s('eps_4q_ago'),
            'eps_current': _s('eps_current'),
            'eps_4q_prior_period': _s('eps_4q_prior_period'),
            'surp_eps_4q_ago': _s('surp_eps_4q_ago'),
            'surp_eps_current': _s('surp_eps_current'),
            'surp_eps_4q_prior_period': _s('surp_eps_4q_prior_period'),
            'sales_forecast': _s('sales_forecast'),
            'sales_4q_ago': _s('sales_4q_ago'),
            'sales_current': _s('sales_current'),
            'sales_4q_prior_period': _s('sales_4q_prior_period'),
            'surp_4q_ago': _s('surp_4q_ago'),
            'surp_current': _s('surp_current'),
            'surp_4q_prior_period': _s('surp_4q_prior_period'),
            'theme_score': _f('theme_score'),
            'gross_margin': _f('gross_margin'),
            'operating_margin': _f('operating_margin'),
            'relative_strength': _f('relative_strength'),
            'guidance_revision': _norm('guidance_revision', {'up', 'flat', 'down'}, ''),
            'reaction_close': _f('reaction_close'),
            'reaction_next_day': _f('reaction_next_day'),
            'market_interpretation': _norm('market_interpretation', {'bullish', 'neutral', 'bearish'}, ''),
            'past_reactions': [_f('past_q1'), _f('past_q2'), _f('past_q3'), _f('past_q4')],
            'summary': _s('summary'),
        },
    )


# ────────────────────────────────────────────────
# メイン処理
# ────────────────────────────────────────────────

def main():
    # 1) CSV コピー（無条件実行）
    confirm_and_copy_csv()

    # 2) CSV 読み込み
    if not WORK_CSV.exists():
        print(f'[Error] {WORK_CSV} が見つからないため終了します。')
        sys.exit(1)

    df = pd.read_csv(WORK_CSV, header=None)
    df = sort_by_first_column(df)
    replace_header_label(df, 'VAR', 'SURP')
    ensure_columns(
        df,
        [PERIOD_COLUMN]
        + list(EPS_COLUMNS.values())
        + list(SALES_COLUMNS.values())
        + FORECAST_COLUMNS
        + SURP_REVENUE_COLUMNS
        + SURP_EPS_COLUMNS
    )
    header_index = build_header_index(df)

    # 3) スクレイピング対象数の入力
    count = int(input('スクレイピングする銘柄数を入力してください: '))
    print(f'{count} 銘柄分の決算日を取得します...')

    # 4) Selenium 起動
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')  # 必要に応じてヘッドレスに
    options.add_argument('--disable-gpu')

    driver = webdriver.Chrome(options=options)

    try:
        market_idx = header_index.get('market', 1)
        symbol_idx = header_index.get('symbol', 2)
        date_idx = header_index.get('date', 0)

        total = min(count, len(df) - 1)
        for i in range(total):
            market = str(df.iat[i + 1, market_idx]).strip()
            symbol = str(df.iat[i + 1, symbol_idx]).strip()
            progress = f'[{i + 1}/{total}] {market}-{symbol}'
            if not market or not symbol or market == 'nan' or symbol == 'nan':
                print(f'{progress} スキップ（market/symbol 未入力）', flush=True)
                continue

            url = build_tradingview_url(market, symbol)
            if not url:
                print(f'{progress} スキップ（URL 作成失敗）', flush=True)
                continue

            print(f'{progress} 取得中...', flush=True)
            try:
                if not load_earnings_page(driver, url):
                    print(f'{progress} 失敗（ページ読み込み不可）', flush=True)
                    continue
                df.iat[i + 1, date_idx] = scrape_earnings_date(driver)
                (
                    eps_values,
                    sales_values,
                    eps_forecasts,
                    sales_forecasts,
                    eps_surprises,
                    sales_surprises,
                    decision_period,
                    decision_period_norm,
                    current_period_norm,
                ) = scrape_eps_sales(driver)

                period_idx = header_index.get(PERIOD_COLUMN)
                if period_idx is not None:
                    display_period = _format_fiscal_period(decision_period_norm)
                    df.iat[i + 1, period_idx] = display_period or decision_period

                decision_prior_period_norm = (
                    _shift_period(decision_period_norm, -4) if decision_period_norm else None
                )
                current_prior_period_norm = (
                    _shift_period(current_period_norm, -4) if current_period_norm else None
                )

                eps_forecast_value = eps_forecasts.get(decision_period_norm) if decision_period_norm else None
                sales_forecast_value = sales_forecasts.get(decision_period_norm) if decision_period_norm else None
                eps_same_period_prior_value = (
                    eps_values.get(decision_prior_period_norm) if decision_prior_period_norm else None
                )
                sales_same_period_prior_value = (
                    sales_values.get(decision_prior_period_norm) if decision_prior_period_norm else None
                )
                eps_current_value = eps_values.get(current_period_norm) if current_period_norm else None
                sales_current_value = sales_values.get(current_period_norm) if current_period_norm else None
                eps_current_prior_value = (
                    eps_values.get(current_prior_period_norm) if current_prior_period_norm else None
                )
                sales_current_prior_value = (
                    sales_values.get(current_prior_period_norm) if current_prior_period_norm else None
                )

                sales_surp_same_period_prior_value = (
                    sales_surprises.get(decision_prior_period_norm) if decision_prior_period_norm else None
                )
                sales_surp_current_value = (
                    sales_surprises.get(current_period_norm) if current_period_norm else None
                )
                sales_surp_current_prior_value = (
                    sales_surprises.get(current_prior_period_norm) if current_prior_period_norm else None
                )
                eps_surp_same_period_prior_value = (
                    eps_surprises.get(decision_prior_period_norm) if decision_prior_period_norm else None
                )
                eps_surp_current_value = (
                    eps_surprises.get(current_period_norm) if current_period_norm else None
                )
                eps_surp_current_prior_value = (
                    eps_surprises.get(current_prior_period_norm) if current_prior_period_norm else None
                )

                eps_forecast_idx = header_index.get('eps_forecast')
                if eps_forecast_idx is not None:
                    df.iat[i + 1, eps_forecast_idx] = _format_eps(eps_forecast_value)
                sales_forecast_idx = header_index.get('sales_forecast')
                if sales_forecast_idx is not None:
                    df.iat[i + 1, sales_forecast_idx] = _format_revenue(sales_forecast_value)

                eps_current_idx = header_index.get('eps_current')
                if eps_current_idx is not None:
                    df.iat[i + 1, eps_current_idx] = _format_eps(eps_current_value)
                sales_current_idx = header_index.get('sales_current')
                if sales_current_idx is not None:
                    df.iat[i + 1, sales_current_idx] = _format_revenue(sales_current_value)

                eps_4q_ago_idx = header_index.get('eps_4q_ago')
                if eps_4q_ago_idx is not None:
                    df.iat[i + 1, eps_4q_ago_idx] = _format_eps(eps_same_period_prior_value)
                sales_4q_ago_idx = header_index.get('sales_4q_ago')
                if sales_4q_ago_idx is not None:
                    df.iat[i + 1, sales_4q_ago_idx] = _format_revenue(sales_same_period_prior_value)

                eps_4q_prior_idx = header_index.get('eps_4q_prior_period')
                if eps_4q_prior_idx is not None:
                    df.iat[i + 1, eps_4q_prior_idx] = _format_eps(eps_current_prior_value)
                sales_4q_prior_idx = header_index.get('sales_4q_prior_period')
                if sales_4q_prior_idx is not None:
                    df.iat[i + 1, sales_4q_prior_idx] = _format_revenue(sales_current_prior_value)

                surp_4q_ago_idx = header_index.get('surp_4q_ago')
                if surp_4q_ago_idx is not None:
                    df.iat[i + 1, surp_4q_ago_idx] = _format_surprise(sales_surp_same_period_prior_value)
                surp_current_idx = header_index.get('surp_current')
                if surp_current_idx is not None:
                    df.iat[i + 1, surp_current_idx] = _format_surprise(sales_surp_current_value)
                surp_4q_prior_idx = header_index.get('surp_4q_prior_period')
                if surp_4q_prior_idx is not None:
                    df.iat[i + 1, surp_4q_prior_idx] = _format_surprise(sales_surp_current_prior_value)

                surp_eps_4q_ago_idx = header_index.get('surp_eps_4q_ago')
                if surp_eps_4q_ago_idx is not None:
                    df.iat[i + 1, surp_eps_4q_ago_idx] = _format_surprise(eps_surp_same_period_prior_value)
                surp_eps_current_idx = header_index.get('surp_eps_current')
                if surp_eps_current_idx is not None:
                    df.iat[i + 1, surp_eps_current_idx] = _format_surprise(eps_surp_current_value)
                surp_eps_4q_prior_idx = header_index.get('surp_eps_4q_prior_period')
                if surp_eps_4q_prior_idx is not None:
                    df.iat[i + 1, surp_eps_4q_prior_idx] = _format_surprise(eps_surp_current_prior_value)

                for period, col in EPS_COLUMNS.items():
                    col_idx = header_index.get(col)
                    if col_idx is not None:
                        df.iat[i + 1, col_idx] = _format_eps(eps_values.get(period))
                for period, col in SALES_COLUMNS.items():
                    col_idx = header_index.get(col)
                    if col_idx is not None:
                        df.iat[i + 1, col_idx] = _format_revenue(sales_values.get(period))

                earning_date = df.iat[i + 1, date_idx] or '-'
                period_display = _format_fiscal_period(decision_period_norm) or decision_period or '-'
                print(f'{progress} 完了 決算日={earning_date} 期間={period_display}', flush=True)
                row_dict = {str(df.iat[0, c]).strip(): df.iat[i + 1, c] for c in range(len(df.columns))}
                try:
                    upsert_event_to_db(row_dict)
                    print(f'{progress} DB upsert 完了', flush=True)
                except Exception as e:
                    print(f'{progress} DB upsert 失敗: {e}', flush=True)
            except Exception as e:
                print(f'{progress} 失敗: {e}', flush=True)
    finally:
        driver.quit()

    # 5) 並び替え (A 列昇順)
    df_sorted = sort_by_first_column(df)

    # 6) 保存 (作業フォルダ)
    df_sorted.to_csv(WORK_CSV, header=False, index=False)
    print(f'[Info] 更新済み CSV を {WORK_CSV} に保存しました。')

    # 7) 保存 (本番ディレクトリ) ※上書き
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        df_sorted.to_csv(FINAL_CSV, header=False, index=False)
        print(f'[Info] 完成データを {FINAL_CSV} に保存しました。')
    except Exception as e:
        print(f'[Warning] {FINAL_CSV} への保存に失敗しました: {e}')

    # 8) 作業用CSVを削除
    try:
        if WORK_CSV.exists():
            WORK_CSV.unlink()
            print(f'[Info] 作業用CSVを削除しました: {WORK_CSV}')
    except Exception as e:
        print(f'[Warning] 作業用CSVの削除に失敗しました: {e}')


if __name__ == '__main__':
    main()
