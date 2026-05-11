import os
import sys
import re
import shutil
import datetime
import queue
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

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
EPS_SALES_CSV = BASE_DIR / 'eps_sales.csv'     # 当期から過去8期分の EPS/売上 を1銘柄1行で保存
EPS_SALES_QUARTER_COUNT = 8
EPS_SALES_HEADERS = ['date', 'market', 'symbol', 'company', 'fiscal_period', 'industry']
for _q in range(EPS_SALES_QUARTER_COUNT):
    EPS_SALES_HEADERS.extend([
        f'q{_q}_eps_actual',
        f'q{_q}_eps_forecast',
        f'q{_q}_eps_surprise',
        f'q{_q}_sales_actual',
        f'q{_q}_sales_forecast',
        f'q{_q}_sales_surprise',
    ])
del _q

EPS_SALES_UPDATE_COLUMNS = ['date', 'fiscal_period']
for _q in range(EPS_SALES_QUARTER_COUNT):
    EPS_SALES_UPDATE_COLUMNS.extend([
        f'q{_q}_eps_actual',
        f'q{_q}_eps_forecast',
        f'q{_q}_eps_surprise',
        f'q{_q}_sales_actual',
        f'q{_q}_sales_forecast',
        f'q{_q}_sales_surprise',
    ])
del _q

PERIOD_COLUMN = 'fiscal_period'
FORECAST_COLUMNS = [
    'eps_forecast',
    'sales_forecast',
]
SURP_REVENUE_COLUMNS = [
    'surp_current',
]
SURP_EPS_COLUMNS = [
    'surp_eps_current',
]

EARNINGS_URL_TEMPLATE = (
    'https://jp.tradingview.com/symbols/{market}-{symbol}/financials-earnings/'
    '?earnings-period=FQ&revenues-period=FQ'
)

VALUES_SELECTOR = '[class*="values-"]'

PARALLEL_WORKERS = 3
BROWSER_TIMEZONE = 'Asia/Tokyo'

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

    m = re.search(r'(\d+)\s*日前', cleaned)
    if m:
        days = int(m.group(1))
        return (datetime.date.today() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')

    if cleaned.startswith('明後日'):
        return (datetime.date.today() + datetime.timedelta(days=2)).strftime('%Y-%m-%d')

    if cleaned.startswith('明日'):
        return (datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

    if cleaned.startswith('今日'):
        return datetime.date.today().strftime('%Y-%m-%d')

    if cleaned.startswith('昨日'):
        return (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')

    m = re.search(r'(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日', cleaned)
    if m:
        year = int(m.group(1)) if m.group(1) else datetime.date.today().year
        month = int(m.group(2))
        day = int(m.group(3))
        try:
            return datetime.date(year, month, day).strftime('%Y-%m-%d')
        except ValueError:
            return ''

    _MONTHS_EN = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    m = re.search(r'\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:,)?\s+(20\d{2})\b', cleaned)
    if m and m.group(1)[:3].lower() in _MONTHS_EN:
        try:
            return datetime.date(
                int(m.group(3)), _MONTHS_EN[m.group(1)[:3].lower()], int(m.group(2))
            ).strftime('%Y-%m-%d')
        except ValueError:
            return ''
    m = re.search(r'\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(20\d{2})\b', cleaned)
    if m and m.group(2)[:3].lower() in _MONTHS_EN:
        try:
            return datetime.date(
                int(m.group(3)), _MONTHS_EN[m.group(2)[:3].lower()], int(m.group(1))
            ).strftime('%Y-%m-%d')
        except ValueError:
            return ''

    m = re.search(r'(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})', cleaned)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime('%Y-%m-%d')
        except ValueError:
            return ''

    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y年%m月%d日'):
        try:
            return datetime.datetime.strptime(cleaned, fmt).date().strftime('%Y-%m-%d')
        except ValueError:
            continue

    return ''


def _normalize_earnings_date_after_keyword(text: str, keyword: str) -> str:
    if not text or keyword not in text:
        return ''

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if keyword not in line:
            continue
        inline_tail = line.split(keyword, 1)[1].strip()
        for candidate in [inline_tail] + lines[idx + 1:idx + 5]:
            normalized = _normalize_earnings_date_text(candidate)
            if normalized:
                return normalized

    tail = text.split(keyword, 1)[1]
    for stop_word in ('決算期間', 'EPS', '売上高'):
        if stop_word in tail:
            tail = tail.split(stop_word, 1)[0]
            break
    return _normalize_earnings_date_text(tail[:120])


def _extract_earnings_date_from_elements(elements: list, raw_seen: list) -> str:
    for elem in elements:
        try:
            text = elem.text.strip()
        except Exception:
            text = ''
        try:
            attr = (elem.get_attribute('datetime') or '').strip()
        except Exception:
            attr = ''

        if attr:
            raw_seen.append(attr)
            normalized = _normalize_earnings_date_text(attr.split('T')[0])
            if normalized:
                return normalized

        if text:
            raw_seen.append(text)
            normalized = _normalize_earnings_date_text(text)
            if normalized:
                return normalized
    return ''


def scrape_earnings_date(driver) -> str:
    '''TradingView の銘柄ページから決算発表日を取得。取得できない場合は空文字を返す。'''
    candidates = []

    try:
        container = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[class*="dateContainer"]'))
        )
        try:
            candidates.append(container.find_element(By.TAG_NAME, 'time'))
        except NoSuchElementException:
            candidates.append(container)
    except TimeoutException:
        pass

    raw_seen = []
    normalized = _extract_earnings_date_from_elements(candidates, raw_seen)
    if normalized:
        return normalized

    # ラベルベースのフォールバック（次回が未確定で「最新の決算報告日」が表示されているケース等）
    for keyword in ('最新の決算報告日', '次回決算報告日', '次回の決算報告日', '前回の決算報告日'):
        try:
            elements = driver.find_elements(By.XPATH, f"//*[contains(., '{keyword}')]")
        except Exception:
            continue
        for elem in elements:
            try:
                text = (elem.text or '').strip()
            except Exception:
                continue
            if keyword not in text:
                continue
            normalized = _normalize_earnings_date_after_keyword(text, keyword)
            if normalized:
                return normalized
            raw_seen.append(text[:120])

    generic_candidates = []
    try:
        generic_candidates.extend(driver.find_elements(By.CSS_SELECTOR, '#js-category-content time[datetime]'))
    except Exception:
        pass

    if not generic_candidates:
        try:
            generic_candidates.extend(driver.find_elements(By.CSS_SELECTOR, 'time[datetime]'))
        except Exception:
            pass

    normalized = _extract_earnings_date_from_elements(generic_candidates, raw_seen)
    if normalized:
        return normalized

    if raw_seen:
        sample = next((s for s in raw_seen if s), '')
        if sample:
            print(f'[Warning] 日付の変換に失敗しました: {sample}')
    return ''


def sort_by_first_column(df: pd.DataFrame) -> pd.DataFrame:
    '''1 行目ヘッダーを固定し、2 行目以降を A 列昇順に並べ替える（空欄は先頭）。'''
    header = df.iloc[:1]
    body = df.iloc[1:].copy()

    # 日付を昇順で確実に並べ替えるために datetime 化（空欄は先頭に配置）
    body[0] = pd.to_datetime(body[0], errors='coerce')
    body.sort_values(by=0, inplace=True, na_position='first')
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


def _round_or_blank(value, precision=2):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return round(float(value), precision)
    except (TypeError, ValueError):
        return None


def _sales_to_billion(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return round(float(value) / 1_000_000_000, 2)
    except (TypeError, ValueError):
        return None


def _df_cell_text(df, row_idx, col_idx):
    if col_idx is None:
        return ''
    raw = df.iat[row_idx, col_idx]
    if raw is None:
        return ''
    text = str(raw).strip()
    if not text or text.lower() == 'nan':
        return ''
    return text


def build_eps_sales_row(date_str, market, symbol, company,
                        fiscal_period_display, industry, anchor_period,
                        eps_map, sales_map,
                        eps_forecast_map, sales_forecast_map,
                        eps_surprise_map, sales_surprise_map):
    row = {
        'date': date_str,
        'market': market,
        'symbol': symbol,
        'company': company,
        'fiscal_period': fiscal_period_display,
        'industry': industry,
    }
    for q in range(EPS_SALES_QUARTER_COUNT):
        period = _shift_period(anchor_period, -q) if anchor_period else None
        row[f'q{q}_eps_actual'] = _round_or_blank(eps_map.get(period) if period else None, 2)
        row[f'q{q}_eps_forecast'] = _round_or_blank(eps_forecast_map.get(period) if period else None, 2)
        row[f'q{q}_eps_surprise'] = _round_or_blank(eps_surprise_map.get(period) if period else None, 2)
        row[f'q{q}_sales_actual'] = _sales_to_billion(sales_map.get(period) if period else None)
        row[f'q{q}_sales_forecast'] = _sales_to_billion(sales_forecast_map.get(period) if period else None)
        row[f'q{q}_sales_surprise'] = _round_or_blank(sales_surprise_map.get(period) if period else None, 2)
    return row


def merge_eps_sales_rows(new_rows: list) -> pd.DataFrame:
    if not EPS_SALES_CSV.exists():
        print(f'[Warning] {EPS_SALES_CSV} が存在しません。事前に銘柄行を用意してください。')
        return pd.DataFrame(columns=EPS_SALES_HEADERS)

    existing_df = pd.read_csv(EPS_SALES_CSV)
    existing_df = existing_df.reindex(columns=EPS_SALES_HEADERS)

    if existing_df.empty:
        return existing_df

    existing_df = existing_df.astype(object)
    market_keys = existing_df['market'].astype(str).str.strip()
    symbol_keys = existing_df['symbol'].astype(str).str.strip()

    for new_row in new_rows:
        market = str(new_row.get('market', '')).strip()
        symbol = str(new_row.get('symbol', '')).strip()
        if not market or not symbol:
            continue
        mask = (market_keys == market) & (symbol_keys == symbol)
        matching_idx = existing_df.index[mask]
        if len(matching_idx) == 0:
            print(f'[Warning] {market}-{symbol} は eps_sales.csv に存在しないためスキップしました。')
            continue
        idx = matching_idx[0]
        for col in EPS_SALES_UPDATE_COLUMNS:
            existing_df.at[idx, col] = new_row.get(col)

    date_sort = pd.to_datetime(existing_df['date'], errors='coerce')
    existing_df = existing_df.assign(_date_sort=date_sort)
    existing_df.sort_values(by='_date_sort', inplace=True, kind='stable', na_position='first')
    existing_df.drop(columns='_date_sort', inplace=True)
    existing_df.reset_index(drop=True, inplace=True)

    return existing_df


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


def _find_best_report_period(driver, selector: str) -> str:
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


def scrape_report_period(driver, timeout: int = 3) -> str:
    selector = '#js-category-content span[class*="data-"]'
    best_period = _find_best_report_period(driver, selector)
    if best_period:
        return best_period

    try:
        return WebDriverWait(driver, timeout).until(
            lambda drv: _find_best_report_period(drv, selector) or False
        )
    except TimeoutException:
        return ''


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

    event, _ = EarningsEvent.objects.update_or_create(
        stock=stock, fiscal_period=fiscal_period,
        defaults={
            'event_date': _date('date'),
            'fundamental': _norm('Fundamental', {'up', 'flat', 'down'}, 'flat'),
            'direction': _norm('Direction', {'up', 'flat', 'down'}, 'flat'),
            'sentiment': _norm('Sentiment', {'up', 'flat', 'down'}, 'flat'),
            'risk_value': _f('Risk'),
            'eps_forecast': _s('eps_forecast'),
            'surp_eps_current': _s('surp_eps_current'),
            'sales_forecast': _s('sales_forecast'),
            'surp_current': _s('surp_current'),
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

    try:
        from earning.services.yfinance import fetch_price_window
        rows = fetch_price_window(event)
        if rows:
            print(f'  price window: {rows} rows', flush=True)
    except Exception as exc:
        print(f'  price window failed: {exc}', flush=True)

    try:
        from earning.services.macro import attach_macro_snapshot
        cols = attach_macro_snapshot(event)
        if cols:
            print(f'  macro snapshot: {cols} columns filled', flush=True)
    except Exception as exc:
        print(f'  macro snapshot failed: {exc}', flush=True)


def _load_eps_sales_period_map():
    """eps_sales.csv を読み込み、(market, symbol) → fiscal_period の辞書を返す。"""
    if not EPS_SALES_CSV.exists():
        return {}
    df = pd.read_csv(EPS_SALES_CSV)
    period_map = {}
    for _, row in df.iterrows():
        market = str(row.get('market', '')).strip()
        symbol = str(row.get('symbol', '')).strip()
        period = str(row.get('fiscal_period', '')).strip()
        if not market or not symbol or market.lower() == 'nan' or symbol.lower() == 'nan':
            continue
        period_map[(market, symbol)] = '' if period.lower() == 'nan' else period
    return period_map


def _sort_eps_sales_targets(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or 'date' not in df.columns:
        return df
    sorted_df = df.copy()
    sorted_df['_date_sort'] = pd.to_datetime(sorted_df['date'], errors='coerce')
    sorted_df.sort_values(by='_date_sort', inplace=True, kind='stable', na_position='first')
    sorted_df.drop(columns='_date_sort', inplace=True)
    return sorted_df


def _load_eps_sales_targets() -> pd.DataFrame:
    if not EPS_SALES_CSV.exists():
        print(f'[Warning] {EPS_SALES_CSV} が存在しないため data.csv の順序で取得します。')
        return pd.DataFrame(columns=['date', 'market', 'symbol', 'fiscal_period'])
    return _sort_eps_sales_targets(pd.read_csv(EPS_SALES_CSV))


def _build_data_row_map(df: pd.DataFrame, market_idx: int, symbol_idx: int) -> dict:
    row_map = {}
    for i in range(1, len(df)):
        market = _df_cell_text(df, i, market_idx)
        symbol = _df_cell_text(df, i, symbol_idx)
        if not market or not symbol:
            continue
        row_map.setdefault((market, symbol), i)
    return row_map


def _create_driver():
    options = webdriver.ChromeOptions()
    options.page_load_strategy = 'eager'
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--blink-settings=imagesEnabled=false')
    options.add_argument('--lang=ja-JP')
    options.add_experimental_option('prefs', {'intl.accept_languages': 'ja-JP,ja'})
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd('Emulation.setTimezoneOverride', {'timezoneId': BROWSER_TIMEZONE})
    return driver


def _scrape_symbol(driver, market, symbol):
    """1銘柄をスクレイプ。'scraped_date' と 'eps_sales_data' を含む dict、または 'error' を返す。"""
    url = build_tradingview_url(market, symbol)
    if not url:
        return {'error': 'URL生成失敗'}
    if not load_earnings_page(driver, url):
        return {'error': 'ページ読み込み不可'}
    try:
        scraped_date = scrape_earnings_date(driver)
        eps_sales_data = scrape_eps_sales(driver)
        return {'scraped_date': scraped_date, 'eps_sales_data': eps_sales_data}
    except Exception as e:
        return {'error': str(e)}


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
        + FORECAST_COLUMNS
        + SURP_REVENUE_COLUMNS
        + SURP_EPS_COLUMNS
    )
    header_index = build_header_index(df)

    # 3) スクレイピング対象数の入力
    count = int(input('スクレイピングする銘柄数を入力してください: '))
    print(f'{count} 銘柄分の決算日を取得します...')

    market_idx = header_index.get('market', 1)
    symbol_idx = header_index.get('symbol', 2)
    date_idx = header_index.get('date', 0)
    company_idx = header_index.get('company')
    industry_idx = header_index.get('industry')
    period_idx = header_index.get(PERIOD_COLUMN)
    eps_forecast_idx = header_index.get('eps_forecast')
    sales_forecast_idx = header_index.get('sales_forecast')
    surp_current_idx = header_index.get('surp_current')
    surp_eps_current_idx = header_index.get('surp_eps_current')
    header_names = [str(df.iat[0, c]).strip() for c in range(len(df.columns))]

    eps_sales_rows = []

    # 4) eps_sales.csv の date 空白行を優先してスクレイプ対象を決定
    eps_sales_period_map = _load_eps_sales_period_map()
    eps_sales_targets = _load_eps_sales_targets()
    data_row_map = _build_data_row_map(df, market_idx, symbol_idx)
    targets = []
    target_source = eps_sales_targets.itertuples(index=False) if not eps_sales_targets.empty else None

    if target_source is None:
        target_source = [
            {
                'market': _df_cell_text(df, i, market_idx),
                'symbol': _df_cell_text(df, i, symbol_idx),
            }
            for i in range(1, len(df))
        ]

    for row in target_source:
        if len(targets) >= count:
            break
        if isinstance(row, dict):
            market = str(row.get('market', '')).strip()
            symbol = str(row.get('symbol', '')).strip()
        else:
            market = str(getattr(row, 'market', '')).strip()
            symbol = str(getattr(row, 'symbol', '')).strip()
        progress = f'[{len(targets) + 1}/{count}] {market}-{symbol}'
        if not market or not symbol or market == 'nan' or symbol == 'nan':
            print(f'{progress} スキップ（market/symbol 未入力）', flush=True)
            continue
        row_idx = data_row_map.get((market, symbol))
        if row_idx is None:
            print(f'{progress} スキップ（data.csv に未登録）', flush=True)
            continue
        data_period = _df_cell_text(df, row_idx, period_idx) if period_idx is not None else ''
        existing_period = eps_sales_period_map.get((market, symbol), '')
        if data_period and existing_period and data_period == existing_period:
            print(f'{progress} スキップ（最新と同じ {data_period}）', flush=True)
            continue
        targets.append((row_idx - 1, market, symbol))

    # 5) 並列スクレイプ
    if targets:
        worker_count = min(PARALLEL_WORKERS, len(targets))
        driver_pool = queue.Queue()
        for _ in range(worker_count):
            driver_pool.put(_create_driver())

        def _worker_task(market, symbol):
            driver = driver_pool.get()
            try:
                return _scrape_symbol(driver, market, symbol)
            finally:
                driver_pool.put(driver)

        print(f'[Info] {len(targets)} 銘柄を {worker_count} 並列でスクレイプ中...', flush=True)
        try:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_target = {
                    executor.submit(_worker_task, m, s): (idx, m, s)
                    for idx, m, s in targets
                }
                done_count = 0
                for future in as_completed(future_to_target):
                    i, market, symbol = future_to_target[future]
                    done_count += 1
                    progress = f'[{done_count}/{len(targets)}] {market}-{symbol}'
                    try:
                        result = future.result()
                    except Exception as e:
                        print(f'{progress} 失敗: {e}', flush=True)
                        continue
                    if 'error' in result:
                        print(f'{progress} 失敗（{result["error"]}）', flush=True)
                        continue

                    try:
                        scraped_date = result['scraped_date']
                        existing_date = _df_cell_text(df, i + 1, date_idx)
                        if scraped_date:
                            df.iat[i + 1, date_idx] = scraped_date
                        elif existing_date:
                            df.iat[i + 1, date_idx] = existing_date

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
                        ) = result['eps_sales_data']

                        if period_idx is not None:
                            display_period = _format_fiscal_period(decision_period_norm)
                            df.iat[i + 1, period_idx] = display_period or decision_period

                        eps_forecast_value = eps_forecasts.get(decision_period_norm) if decision_period_norm else None
                        sales_forecast_value = sales_forecasts.get(decision_period_norm) if decision_period_norm else None
                        sales_surp_current_value = (
                            sales_surprises.get(current_period_norm) if current_period_norm else None
                        )
                        eps_surp_current_value = (
                            eps_surprises.get(current_period_norm) if current_period_norm else None
                        )

                        if eps_forecast_idx is not None:
                            df.iat[i + 1, eps_forecast_idx] = _format_eps(eps_forecast_value)
                        if sales_forecast_idx is not None:
                            df.iat[i + 1, sales_forecast_idx] = _format_revenue(sales_forecast_value)

                        if surp_current_idx is not None:
                            df.iat[i + 1, surp_current_idx] = _format_surprise(sales_surp_current_value)
                        if surp_eps_current_idx is not None:
                            df.iat[i + 1, surp_eps_current_idx] = _format_surprise(eps_surp_current_value)

                        date_value = _df_cell_text(df, i + 1, date_idx)
                        company_value = _df_cell_text(df, i + 1, company_idx)
                        fiscal_period_value = _df_cell_text(df, i + 1, period_idx)
                        industry_value = _df_cell_text(df, i + 1, industry_idx)
                        eps_sales_rows.append(build_eps_sales_row(
                            date_value, market, symbol, company_value,
                            fiscal_period_value, industry_value,
                            current_period_norm,
                            eps_values, sales_values,
                            eps_forecasts, sales_forecasts,
                            eps_surprises, sales_surprises,
                        ))

                        earning_date = df.iat[i + 1, date_idx] or '-'
                        period_display = _format_fiscal_period(decision_period_norm) or decision_period or '-'
                        print(f'{progress} 完了 決算日={earning_date} 期間={period_display}', flush=True)
                        row_dict = {name: df.iat[i + 1, c] for c, name in enumerate(header_names)}
                        try:
                            upsert_event_to_db(row_dict)
                            print(f'{progress} DB upsert 完了', flush=True)
                        except Exception as e:
                            print(f'{progress} DB upsert 失敗: {e}', flush=True)
                    except Exception as e:
                        print(f'{progress} 反映処理失敗: {e}', flush=True)
        finally:
            while not driver_pool.empty():
                try:
                    driver_pool.get_nowait().quit()
                except Exception:
                    pass
    else:
        print('[Info] スクレイプ対象なし。')

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

    # 7.5) eps_sales.csv 保存（当期から過去8期分の EPS/売上 を1銘柄1行で出力）
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        if eps_sales_rows:
            df2 = merge_eps_sales_rows(eps_sales_rows)
            df2.to_csv(EPS_SALES_CSV, index=False)
            print(f'[Info] eps_sales を {EPS_SALES_CSV} に保存しました（{len(df2)} 行）。')
        else:
            print('[Info] eps_sales 用の行が無いため保存をスキップしました。')
    except Exception as e:
        print(f'[Warning] {EPS_SALES_CSV} への保存に失敗しました: {e}')

    # 7.6) eps_sales.csv の内容を DB に同期
    try:
        if EPS_SALES_CSV.exists():
            from earning.services.eps_sales_sync import sync_eps_sales_csv_to_db
            q0, q1, skipped = sync_eps_sales_csv_to_db(str(EPS_SALES_CSV))
            print(f'[Info] eps_sales を DB に同期しました（q0={q0} q1={q1} skipped={skipped}）。')
    except Exception as e:
        print(f'[Warning] eps_sales の DB 同期に失敗しました: {e}')

    # 8) 作業用CSVを削除
    try:
        if WORK_CSV.exists():
            WORK_CSV.unlink()
            print(f'[Info] 作業用CSVを削除しました: {WORK_CSV}')
    except Exception as e:
        print(f'[Warning] 作業用CSVの削除に失敗しました: {e}')


if __name__ == '__main__':
    main()
