import csv
import datetime
import json
import logging
import os
import re
import statistics
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ロガー設定
logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SEC = (5, 15)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Connection": "close",
}
HTTP_SILENT_STATUS = {403, 404, 429}
PRICE_SYMBOLS = ("^N225", "1329.T")
MOF_JGB10Y_CSV_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
)
NIKKEI_COM_JAPANIDX_URL = "https://www.nikkei.com/markets/kabu/japanidx/"
STOOQ_QUOTE_URL = "https://stooq.com/q/l/?s={symbol}&i=d"
STOOQ_NIKKEI_SYMBOL = "^nkx"
WORLD_BANK_NOMINAL_GDP_URL = (
    "https://api.worldbank.org/v2/country/JPN/"
    "indicator/NY.GDP.MKTP.CN?format=json&per_page=70"
)
NIKKEI_PER_DATA_PATH = os.path.join(
    os.path.dirname(__file__),
    "data",
    "nikkei_per.json",
)
NIKKEI_PER_DATA_URL = os.getenv("NIKKEI_PER_DATA_URL")
GDP_GROWTH_YEARS = 10
GROWTH_CORE_WIDTH_DEFAULT = 0.005
GROWTH_WIDE_WIDTH_DEFAULT = 0.01
FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")
DATE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")

def _parse_float(text):
    if not text:
        return None
    cleaned = text.replace(",", "").replace("倍", "").strip()
    match = FLOAT_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None

def _parse_mof_jgb10y(text):
    reader = csv.reader(text.splitlines())
    header = None
    ten_year_index = None
    latest_value = None
    for row in reader:
        if not row:
            continue
        first_cell = row[0].lstrip("\ufeff").strip()
        if header is None:
            if first_cell == "Date":
                header = [cell.strip() for cell in row]
                try:
                    ten_year_index = header.index("10Y")
                except ValueError:
                    return None
            continue
        if not DATE_RE.match(first_cell):
            continue
        if ten_year_index is None or len(row) <= ten_year_index:
            continue
        value = _parse_float(row[ten_year_index])
        if value is not None:
            latest_value = value
    return latest_value

def _get_json(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SEC)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in HTTP_SILENT_STATUS:
            logger.debug("HTTP request blocked (%s): %s", url, status_code)
        else:
            logger.warning("HTTP request failed (%s): %s", url, exc)
        return None
    except ValueError as exc:
        logger.warning("JSON decode failed (%s): %s", url, exc)
        return None

def _get_text(url, headers=None):
    try:
        response = requests.get(
            url, headers=headers or HEADERS, timeout=REQUEST_TIMEOUT_SEC
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in HTTP_SILENT_STATUS:
            logger.debug("HTTP request blocked (%s): %s", url, status_code)
        else:
            logger.warning("HTTP request failed (%s): %s", url, exc)
        return None

def _get_yahoo_chart(symbol):
    encoded_symbol = quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"
    data = _get_json(url)
    if not data:
        return None
    results = _get_nested(data, ("chart", "result"))
    if not results:
        return None
    return results[0]

def _get_nested(data, keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current

def _extract_numeric(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("raw", "fmt"):
            if key in value:
                return _extract_numeric(value.get(key))
        return None
    if isinstance(value, str):
        return _parse_float(value)
    return None

def _extract_quote_value(quote, field_names):
    if not isinstance(quote, dict):
        return None
    for field in field_names:
        value = _extract_numeric(quote.get(field))
        if value is not None:
            return value
    return None

def _median(values):
    if not values:
        return None
    return statistics.median(values)

def _extract_last_close(chart):
    if not isinstance(chart, dict):
        return None
    indicators = chart.get("indicators", {})
    quote_list = indicators.get("quote")
    if not quote_list:
        return None
    closes = quote_list[0].get("close")
    if not closes:
        return None
    for value in reversed(closes):
        if value is not None:
            return float(value)
    return None

def _get_stooq_last_close(symbol):
    url = STOOQ_QUOTE_URL.format(symbol=symbol)
    text = _get_text(url)
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    if lines[0].lower().startswith("symbol") and len(lines) > 1:
        line = lines[1]
    else:
        line = lines[0]
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 7:
        return None
    close_value = parts[6]
    if not close_value or close_value == "N/D":
        return None
    try:
        return float(close_value)
    except ValueError:
        return None

def get_nikkei_price():
    """
    Stooqを優先し、取得できない場合はYahoo chartから日経平均株価を取得
    """
    stooq_value = _get_stooq_last_close(STOOQ_NIKKEI_SYMBOL)
    if stooq_value is not None:
        return stooq_value
    for symbol in PRICE_SYMBOLS:
        chart = _get_yahoo_chart(symbol)
        if not chart:
            continue
        meta = chart.get("meta", {})
        value = _extract_quote_value(
            meta, ("regularMarketPrice", "chartPreviousClose")
        )
        if value is None:
            value = _extract_last_close(chart)
        if value is not None:
            return value
    return None

def get_jgb10y_yield_percent():
    """
    財務省CSVから日本国債10年利回りを取得
    """
    text = _get_text(MOF_JGB10Y_CSV_URL)
    if text:
        value = _parse_mof_jgb10y(text)
        if value is not None:
            return value
    return None

def get_nominal_gdp_growth_median(years=GDP_GROWTH_YEARS):
    """
    World Bank APIから名目GDP成長率の中央値を取得
    """
    data = _get_json(WORLD_BANK_NOMINAL_GDP_URL)
    if not data or len(data) < 2 or not isinstance(data[1], list):
        return None

    series = []
    for item in data[1]:
        year = item.get("date")
        value = item.get("value")
        if year is None or value is None:
            continue
        try:
            year_int = int(year)
        except ValueError:
            continue
        try:
            value_float = float(value)
        except (TypeError, ValueError):
            continue
        series.append((year_int, value_float))

    if len(series) < 2:
        return None

    series.sort(key=lambda item: item[0])
    growth_rates = []
    for idx in range(1, len(series)):
        year, value = series[idx]
        prev_year, prev_value = series[idx - 1]
        if prev_value == 0:
            continue
        growth = (value / prev_value) - 1.0
        growth_rates.append((year, growth))

    if not growth_rates:
        return None

    growth_rates.sort(key=lambda item: item[0])
    if years and len(growth_rates) > years:
        growth_rates = growth_rates[-years:]
    values = [value for _, value in growth_rates]
    return _median(values)

def _extract_nikkei_per_values_from_payload(payload):
    if not isinstance(payload, dict):
        return None
    index_val = _extract_numeric(payload.get("index_based"))
    weighted_val = _extract_numeric(payload.get("weighted_average"))
    if index_val is None and weighted_val is None:
        return None
    result = {}
    if index_val is not None:
        result["index_based"] = index_val
    if weighted_val is not None:
        result["weighted_average"] = weighted_val
    return result

def _load_nikkei_per_data_file(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read Nikkei PER data file (%s): %s", path, exc)
        return None
    return _extract_nikkei_per_values_from_payload(payload)

def _load_nikkei_per_data_url(url):
    payload = _get_json(url)
    if not payload:
        return None
    return _extract_nikkei_per_values_from_payload(payload)

def get_nikkei_per_values():
    if NIKKEI_PER_DATA_URL:
        result = _load_nikkei_per_data_url(NIKKEI_PER_DATA_URL)
        if result:
            return result
    return _load_nikkei_per_data_file(NIKKEI_PER_DATA_PATH)

def get_actual_per():
    """
    日経平均の指数ベースPER（実績）を取得
    Source: https://www.nikkei.com/markets/kabu/japanidx/
    Target Table: Headers [項目名, 前期基準, 予想], Row [日経平均, 19.76倍, 20.33倍]
    """
    url = NIKKEI_COM_JAPANIDX_URL
    text = _get_text(url)
    if not text:
        return None
    
    try:
        soup = BeautifulSoup(text, "lxml")
        tables = soup.find_all("table")
        
        for table in tables:
            # We look for a table that likely has these headers. 
            # Note: findAll on table might return all cells, we should look at thead or first row specifically if possible,
            # but flattening all text in table to check for existence is easier first.
            table_text = table.get_text(strip=True)
            if "前期基準" not in table_text or "予想" not in table_text:
                continue
                
            # Check for "日経平均" row
            rows = table.find_all("tr")
            for row in rows:
                cols = row.find_all(["th", "td"])
                if not cols:
                    continue
                
                row_head = cols[0].get_text(strip=True)
                if row_head == "日経平均":
                    # Found the row. Now check value format to distinguish from Yield table (%)
                    if len(cols) < 2:
                        break
                    
                    val_text = cols[1].get_text(strip=True)
                    if "倍" in val_text:
                        # This is the PER table (or PBR, but PBR table usually has different headers or values < 5)
                        # The user specified table has "前期基準" and "予想" headers. PBR table usually only has "純資産倍率" or similar.
                        # Inspection showed Table 4 headers: ['項目名', '前期基準', '予想'] and values with "倍".
                        # This matches.
                        return _parse_float(val_text)
        
        logger.warning("Actual PER table/row not found on Nikkei.com")
        return None

    except Exception as e:
        logger.error(f"Error parsing Actual PER from Nikkei.com: {e}")
        return None

def calculate_bias(
    price,
    forward_per,
    actual_per,
    gdp_growth_median=None,
    jgb10y_yield_percent=None,
    forward_per_weighted=None,
    erp_fixed=None,
):
    # --- 3. 入力仕様（固定値） ---
    # price, forward_per, actual_per は引数から取得

    dividend_yield_percent = 1.60  # %
    if jgb10y_yield_percent is None:
        jgb10y_yield_percent = 0.0
    if gdp_growth_median is None:
        gdp_growth_median = 0.0
    if erp_fixed is None:
        erp_fixed = 0.0
    if price is None or price <= 0:
        price = 0.0
    if forward_per is None or forward_per <= 0:
        forward_per = 0.0
    if actual_per is None or actual_per <= 0:
        actual_per = 0.0
    if forward_per_weighted is None or forward_per_weighted <= 0:
        forward_per_weighted = 0.0

    # --- 9. パラメータ（初期値） ---
    GROWTH_CORE_WIDTH = GROWTH_CORE_WIDTH_DEFAULT
    GROWTH_WIDE_WIDTH = GROWTH_WIDE_WIDTH_DEFAULT
    G_IMPLIED_HI = 0.05
    G_IMPLIED_LO = 0.00

    # --- 3.2 単位の正規化 ---
    dividend_yield_decimal = dividend_yield_percent / 100.0
    jgb10y_yield_decimal = jgb10y_yield_percent / 100.0

    # --- 4. 計算指標 ---
    def safe_divide(numerator, denominator):
        if numerator is None or denominator in (None, 0):
            return 0.0
        return numerator / denominator

    # 4.0 指標D: EPS（PERから逆算）
    forward_eps = safe_divide(price, forward_per)
    forward_eps_weighted = safe_divide(price, forward_per_weighted)
    actual_eps = safe_divide(price, actual_per)

    # 4.1 指標A：益利回り
    # Method 1: From PER (1 / PER)
    ey_fwd_index_per = safe_divide(1.0, forward_per)
    ey_fwd_weighted_per = safe_divide(1.0, forward_per_weighted)
    
    # Method 2: From EPS (EPS / Price)
    ey_fwd_index_eps = safe_divide(forward_eps, price)
    ey_fwd_weighted_eps = safe_divide(forward_eps_weighted, price)

    # Default for downstream logic (using PER based as primary)
    earnings_yield_forward = ey_fwd_index_per
    earnings_yield_forward_weighted = ey_fwd_weighted_per
    earnings_yield_actual = safe_divide(1.0, actual_per)

    # 4.2 指標B：イールドギャップ（市場の暗黙ERP）
    yield_gap = earnings_yield_forward - jgb10y_yield_decimal

    # 4.3 指標C：暗黙成長率（市場の利回りから推定）
    market_required_return = earnings_yield_forward
    g_implied = market_required_return - dividend_yield_decimal

    # 4.4 指標D：フェアバリュー用の要求収益率（固定ERP）
    required_return_fair = jgb10y_yield_decimal + erp_fixed

    # 4.5 指標E：フェアバリュー（成長率レンジ）
    def _calc_fair_per(growth_rate):
        spread = required_return_fair - growth_rate
        if spread <= 0:
            return None
        return 1.0 / spread

    growth_center = gdp_growth_median
    growth_core_low = growth_center - GROWTH_CORE_WIDTH
    growth_core_high = growth_center + GROWTH_CORE_WIDTH
    growth_wide_low = growth_center - GROWTH_WIDE_WIDTH
    growth_wide_high = growth_center + GROWTH_WIDE_WIDTH

    fair_per_mid = _calc_fair_per(growth_center)
    fair_per_core_low = _calc_fair_per(growth_core_low)
    fair_per_core_high = _calc_fair_per(growth_core_high)
    fair_per_wide_low = _calc_fair_per(growth_wide_low)
    fair_per_wide_high = _calc_fair_per(growth_wide_high)

    fair_price_mid = forward_eps * fair_per_mid if fair_per_mid else None
    fair_price_core_low = (
        forward_eps * fair_per_core_low if fair_per_core_low else None
    )
    fair_price_core_high = (
        forward_eps * fair_per_core_high if fair_per_core_high else None
    )
    fair_price_wide_low = (
        forward_eps * fair_per_wide_low if fair_per_wide_low else None
    )
    fair_price_wide_high = (
        forward_eps * fair_per_wide_high if fair_per_wide_high else None
    )
    fair_price_gap_pct = None
    if fair_price_mid:
        fair_price_gap = price - fair_price_mid
        fair_price_gap_pct = (fair_price_gap / fair_price_mid) * 100.0

    # --- 5. 判定ロジック ---
    regime = "LONG_BIAS" if yield_gap >= 0 else "SHORT_BIAS"

    valuation_label = "判定不可"
    if (
        fair_price_core_low is not None
        and fair_price_core_high is not None
        and fair_price_wide_low is not None
        and fair_price_wide_high is not None
    ):
        if price > fair_price_wide_high:
            valuation_label = "Over +"
        elif price < fair_price_wide_low:
            valuation_label = "Deep Under"
        elif price > fair_price_core_high:
            valuation_label = "Over"
        elif price < fair_price_core_low:
            valuation_label = "Under"
        else:
            valuation_label = "Fair"

    # --- 6. 注釈ロジック ---
    regime_note = None
    if regime == "SHORT_BIAS" and g_implied >= G_IMPLIED_HI:
        regime_note = "楽観過多"
    elif regime == "LONG_BIAS" and g_implied <= G_IMPLIED_LO:
        regime_note = "悲観過多"

    # --- 8. 出力仕様 ---
    output = {
        "date": datetime.date.today().isoformat(),
        "price": round(price, 0),
        "forward_per": forward_per,
        "forward_per_weighted": forward_per_weighted,
        "forward_eps": round(forward_eps, 2),
        "forward_eps_weighted": round(forward_eps_weighted, 2)
        if forward_eps_weighted is not None
        else None,
        "actual_per": round(actual_per, 2),
        "actual_eps": round(actual_eps, 2), # 計算値なので丸める
        "jgb10y_yield_percent": jgb10y_yield_percent,
        "jgb10y_yield_decimal": round(jgb10y_yield_decimal, 6),
        "earnings_yield_forward": round(earnings_yield_forward, 6),
        "earnings_yield_forward_weighted": round(earnings_yield_forward_weighted, 6)
        if earnings_yield_forward_weighted is not None
        else None,
        "earnings_yield_forward_from_eps": round(ey_fwd_index_eps, 6),
        "earnings_yield_forward_weighted_from_eps": round(ey_fwd_weighted_eps, 6)
        if ey_fwd_weighted_eps is not None
        else None,
        "earnings_yield_actual": round(earnings_yield_actual, 6),
        "yield_gap": round(yield_gap, 6),
        "dividend_yield_percent": dividend_yield_percent,
        "dividend_yield_decimal": round(dividend_yield_decimal, 6),
        "g_implied": round(g_implied, 6),
        "fair_price_mid": round(fair_price_mid, 0)
        if fair_price_mid is not None
        else None,
        "fair_price_core_low": round(fair_price_core_low, 0)
        if fair_price_core_low is not None
        else None,
        "fair_price_core_high": round(fair_price_core_high, 0)
        if fair_price_core_high is not None
        else None,
        "fair_price_wide_low": round(fair_price_wide_low, 0)
        if fair_price_wide_low is not None
        else None,
        "fair_price_wide_high": round(fair_price_wide_high, 0)
        if fair_price_wide_high is not None
        else None,
        "fair_price_gap_pct": round(fair_price_gap_pct, 2)
        if fair_price_gap_pct is not None
        else None,
        "valuation_label": valuation_label,
        "erp_percent": round(erp_fixed * 100.0, 2),
        "gdp_growth_median_percent": round(gdp_growth_median * 100.0, 2),
        "gdp_growth_years": GDP_GROWTH_YEARS,
        "growth_core_width_percent": round(GROWTH_CORE_WIDTH * 100.0, 2),
        "growth_wide_width_percent": round(GROWTH_WIDE_WIDTH * 100.0, 2),
        "regime": regime,
        "regime_note": regime_note,
    }

    return output
