import csv
import datetime
import json
import logging
import os
import re

import requests

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
MOF_JGB10Y_CSV_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
)
NIKKEI_PER_DATA_PATH = os.path.join(
    os.path.dirname(__file__),
    "data",
    "nikkei_per.json",
)
NIKKEI_PER_DATA_URL = os.getenv("NIKKEI_PER_DATA_URL")
GROWTH_CORE_WIDTH_DEFAULT = 0.005
GROWTH_WIDE_WIDTH_DEFAULT = 0.01
GROWTH_CORE_RATIO_BASE = 0.1
GROWTH_WIDE_RATIO_BASE = 0.18
GROWTH_CORE_RATIO_DEFAULT = 0.6
GROWTH_WIDE_RATIO_DEFAULT = 0.7
GROWTH_CORE_WIDTH_MIN_DEFAULT = 0.001
GROWTH_WIDE_WIDTH_MIN_DEFAULT = 0.002
GROWTH_RATIO_MIN_DEFAULT = 0.1
GROWTH_RATIO_MAX_DEFAULT = 2.0
FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")
DATE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")
DATE_ANY_RE = re.compile(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})")
PER_INDEX_KEYS = (
    "index_based",
    "index_base",
    "indexBase",
    "indexBased",
    "index",
    "index_value",
    "indexValue",
    "value",
)
DIVIDEND_INDEX_KEYS = (
    "dividend_yield_index_based",
    "dividend_yield_index_percent",
    "dividend_yield_index",
    "dividend_yield",
    "dividendYieldIndexBased",
    "dividendYieldIndex",
    "dividendYield",
    "dividend",
    "dividend_index_based",
    "dividend_index",
)
DIVIDEND_CONTAINER_KEYS = (
    "dividend_yield",
    "dividendYield",
    "dividend",
    "dividend_yield_index",
    "dividendYieldIndex",
)
SERIES_KEYS = ("data", "items", "results", "records", "values")
DATE_KEYS = ("date", "date_text", "as_of", "asof", "timestamp")

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

def _normalize_key(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())

def _extract_numeric_from_mapping(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            value = _extract_numeric(mapping.get(key))
            if value is not None:
                return value
    normalized = {_normalize_key(key): key for key in keys}
    for key, value in mapping.items():
        if _normalize_key(str(key)) in normalized:
            numeric = _extract_numeric(value)
            if numeric is not None:
                return numeric
    return None

def _parse_date_parts_any(text):
    if not text:
        return None
    match = DATE_ANY_RE.search(str(text).strip())
    if not match:
        return None
    try:
        year, month, day = (int(part) for part in match.groups())
    except ValueError:
        return None
    return year, month, day

def _extract_latest_value_from_series(series, keys):
    if not isinstance(series, list):
        return None
    best_value = None
    best_date = None
    for item in series:
        if not isinstance(item, dict):
            continue
        value = _extract_numeric_from_mapping(item, keys)
        if value is None:
            continue
        date_value = None
        for date_key in DATE_KEYS:
            if date_key in item:
                date_value = item.get(date_key)
                break
        date_parts = _parse_date_parts_any(date_value) if date_value else None
        if date_parts is None:
            if best_value is None:
                best_value = value
            continue
        if best_date is None or date_parts > best_date:
            best_date = date_parts
            best_value = value
    return best_value

def _extract_index_based_value(payload):
    if isinstance(payload, dict):
        value = _extract_numeric_from_mapping(payload, PER_INDEX_KEYS)
        if value is not None:
            return value
        for key in SERIES_KEYS:
            value = _extract_latest_value_from_series(
                payload.get(key),
                PER_INDEX_KEYS,
            )
            if value is not None:
                return value
        return None
    if isinstance(payload, list):
        return _extract_latest_value_from_series(payload, PER_INDEX_KEYS)
    return None

def _extract_dividend_index_based_value(payload):
    if isinstance(payload, dict):
        value = _extract_numeric_from_mapping(payload, DIVIDEND_INDEX_KEYS)
        if value is not None:
            return value
        for key in DIVIDEND_CONTAINER_KEYS:
            container = payload.get(key)
            if isinstance(container, dict):
                value = _extract_numeric_from_mapping(
                    container, DIVIDEND_INDEX_KEYS + PER_INDEX_KEYS
                )
                if value is not None:
                    return value
            if isinstance(container, list):
                value = _extract_latest_value_from_series(
                    container,
                    DIVIDEND_INDEX_KEYS + PER_INDEX_KEYS,
                )
                if value is not None:
                    return value
        for key in SERIES_KEYS:
            value = _extract_latest_value_from_series(
                payload.get(key),
                DIVIDEND_INDEX_KEYS,
            )
            if value is not None:
                return value
        return None
    if isinstance(payload, list):
        return _extract_latest_value_from_series(payload, DIVIDEND_INDEX_KEYS)
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

def _extract_nikkei_per_values_from_payload(payload):
    if not isinstance(payload, (dict, list)):
        return None
    index_val = _extract_index_based_value(payload)
    dividend_index_val = _extract_dividend_index_based_value(payload)
    if index_val is None and dividend_index_val is None:
        return None
    result = {}
    if index_val is not None:
        result["index_based"] = index_val
    if dividend_index_val is not None:
        result["dividend_yield_index_based"] = dividend_index_val
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

def _merge_nikkei_per_values(primary, fallback):
    if not primary:
        return fallback
    if not fallback:
        return primary
    merged = dict(primary)
    if "index_based" not in merged and "index_based" in fallback:
        merged["index_based"] = fallback["index_based"]
    if (
        "dividend_yield_index_based" not in merged
        and "dividend_yield_index_based" in fallback
    ):
        merged["dividend_yield_index_based"] = (
            fallback["dividend_yield_index_based"]
        )
    return merged

def get_nikkei_per_values():
    if NIKKEI_PER_DATA_URL:
        primary = _load_nikkei_per_data_url(NIKKEI_PER_DATA_URL)
        if primary and {
            "index_based",
            "dividend_yield_index_based",
        }.issubset(primary.keys()):
            return primary
        fallback = _load_nikkei_per_data_file(NIKKEI_PER_DATA_PATH)
        return _merge_nikkei_per_values(primary, fallback)
    return _load_nikkei_per_data_file(NIKKEI_PER_DATA_PATH)

def calculate_bias(
    price,
    forward_per,
    dividend_yield_index_percent=None,
    jgb10y_yield_percent=None,
    erp_fixed=None,
    growth_center_percent=None,
    growth_core_ratio=None,
    growth_wide_ratio=None,
):
    # --- 3. 入力仕様（固定値） ---
    # price, forward_per は引数から取得

    dividend_yield_index_display = dividend_yield_index_percent
    if dividend_yield_index_percent is None:
        dividend_yield_index_percent = 0.0
    if jgb10y_yield_percent is None:
        jgb10y_yield_percent = 0.0
    if erp_fixed is None:
        erp_fixed = 0.0
    growth_center_decimal = None
    if growth_center_percent is not None:
        try:
            growth_center_decimal = float(growth_center_percent) / 100.0
        except (TypeError, ValueError):
            growth_center_decimal = None
    core_ratio = GROWTH_CORE_RATIO_DEFAULT
    if growth_core_ratio is not None:
        try:
            core_ratio = float(growth_core_ratio)
        except (TypeError, ValueError):
            core_ratio = GROWTH_CORE_RATIO_DEFAULT
    wide_ratio = GROWTH_WIDE_RATIO_DEFAULT
    if growth_wide_ratio is not None:
        try:
            wide_ratio = float(growth_wide_ratio)
        except (TypeError, ValueError):
            wide_ratio = GROWTH_WIDE_RATIO_DEFAULT
    if core_ratio <= 0:
        core_ratio = GROWTH_CORE_RATIO_DEFAULT
    if wide_ratio <= 0:
        wide_ratio = GROWTH_WIDE_RATIO_DEFAULT
    core_ratio = min(
        GROWTH_RATIO_MAX_DEFAULT,
        max(GROWTH_RATIO_MIN_DEFAULT, core_ratio),
    )
    wide_ratio = min(
        GROWTH_RATIO_MAX_DEFAULT,
        max(GROWTH_RATIO_MIN_DEFAULT, wide_ratio),
    )
    if price is None or price <= 0:
        price = 0.0
    if forward_per is None or forward_per <= 0:
        forward_per = 0.0

    # --- 9. パラメータ（初期値） ---
    G_IMPLIED_HI = 0.05
    G_IMPLIED_LO = 0.00

    # --- 3.2 単位の正規化 ---
    dividend_yield_index_decimal = dividend_yield_index_percent / 100.0
    jgb10y_yield_decimal = jgb10y_yield_percent / 100.0

    # --- 4. 計算指標 ---
    def safe_divide(numerator, denominator):
        if numerator is None or denominator in (None, 0):
            return 0.0
        return numerator / denominator

    # 4.0 指標D: EPS（PERから逆算）
    forward_eps = safe_divide(price, forward_per)

    # 4.1 指標A：益利回り
    # Method 1: From PER (1 / PER)
    ey_fwd_index_per = safe_divide(1.0, forward_per)
    
    # Method 2: From EPS (EPS / Price)
    ey_fwd_index_eps = safe_divide(forward_eps, price)

    # Default for downstream logic (using PER based as primary)
    earnings_yield_forward = ey_fwd_index_per

    # 4.2 指標B：イールドギャップ（市場の暗黙ERP）
    yield_gap = earnings_yield_forward - jgb10y_yield_decimal

    # 4.3 指標C：暗黙成長率（市場の利回りから推定）
    market_required_return = earnings_yield_forward
    g_implied = market_required_return - dividend_yield_index_decimal
    g_implied_index = None
    if (
        dividend_yield_index_display is not None
        and forward_per > 0
        and price > 0
    ):
        g_implied_index = ey_fwd_index_eps - dividend_yield_index_decimal

    # 4.4 指標D：フェアバリュー用の要求収益率（固定ERP）
    required_return_fair = jgb10y_yield_decimal + erp_fixed

    # 4.5 指標E：フェアバリュー（成長率レンジ）
    def _calc_fair_per(growth_rate):
        spread = required_return_fair - growth_rate
        if spread <= 0:
            return None
        return 1.0 / spread

    growth_center = growth_center_decimal if growth_center_decimal is not None else 0.0
    width_anchor_growth = growth_center
    if growth_center_decimal is None and g_implied_index is not None:
        width_anchor_growth = g_implied_index

    spread_for_width = required_return_fair - width_anchor_growth
    if spread_for_width is None or spread_for_width <= 0:
        spread_for_width = 0.0

    core_ratio_effective = GROWTH_CORE_RATIO_BASE * core_ratio
    wide_ratio_effective = GROWTH_WIDE_RATIO_BASE * wide_ratio

    GROWTH_CORE_WIDTH = min(
        GROWTH_CORE_WIDTH_DEFAULT,
        max(
            GROWTH_CORE_WIDTH_MIN_DEFAULT,
            spread_for_width * core_ratio_effective,
        ),
    )
    GROWTH_WIDE_WIDTH = min(
        GROWTH_WIDE_WIDTH_DEFAULT,
        max(
            GROWTH_WIDE_WIDTH_MIN_DEFAULT,
            spread_for_width * wide_ratio_effective,
        ),
    )
    if GROWTH_WIDE_WIDTH < GROWTH_CORE_WIDTH:
        GROWTH_WIDE_WIDTH = GROWTH_CORE_WIDTH

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
        "forward_eps": round(forward_eps, 2),
        "jgb10y_yield_percent": jgb10y_yield_percent,
        "jgb10y_yield_decimal": round(jgb10y_yield_decimal, 6),
        "earnings_yield_forward": round(earnings_yield_forward, 6),
        "earnings_yield_forward_from_eps": round(ey_fwd_index_eps, 6),
        "yield_gap": round(yield_gap, 6),
        "dividend_yield_index_percent": dividend_yield_index_display,
        "dividend_yield_index_decimal": round(dividend_yield_index_decimal, 6)
        if dividend_yield_index_display is not None
        else None,
        "g_implied_index": round(g_implied_index, 6)
        if g_implied_index is not None
        else None,
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
        "growth_core_width_percent": round(GROWTH_CORE_WIDTH * 100.0, 2),
        "growth_wide_width_percent": round(GROWTH_WIDE_WIDTH * 100.0, 2),
        "regime": regime,
        "regime_note": regime_note,
    }

    return output
