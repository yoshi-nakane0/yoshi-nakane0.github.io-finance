import datetime
import json
import logging

from .nikkei_bias import NIKKEI_PER_DATA_PATH, calculate_bias

logger = logging.getLogger(__name__)

ANCHOR_CONFIG_KEY = "basecalc_anchor"
ANCHOR_SCHEMA_VERSION = 1
DEFAULT_ERP_METHOD = "method_a"
DEFAULT_GROWTH_CORE_RATIO = 0.6
DEFAULT_GROWTH_WIDE_RATIO = 0.7
ALLOWED_ERP_METHODS = {"method_a", "method_b", "method_c"}
ALLOWED_GROWTH_VALUES = {1.7, 2.1, 2.7}


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_positive_float(value):
    normalized = _to_float(value)
    if normalized is None or normalized <= 0:
        return None
    return normalized


def normalize_erp_method(value):
    if value in ALLOWED_ERP_METHODS:
        return value
    return DEFAULT_ERP_METHOD


def normalize_growth_percent(value, erp_method):
    if erp_method == "method_c":
        return 0.0
    normalized = _to_float(value)
    if normalized is None:
        return 2.1 if erp_method == "method_b" else None
    rounded = round(normalized, 1)
    if rounded in ALLOWED_GROWTH_VALUES:
        return rounded
    return 2.1 if erp_method == "method_b" else None


def normalize_ratio(value, default_value):
    normalized = _to_float(value)
    if normalized is None or normalized <= 0:
        return default_value
    if normalized < 0.1:
        return 0.1
    if normalized > 2.0:
        return 2.0
    return normalized


def calculate_erp_fixed(
    erp_method,
    forward_per,
    jgb10y_yield_percent,
    dividend_yield_index_percent,
    erp_growth_percent,
):
    forward_per = _to_float(forward_per) or 0.0
    jgb_decimal = (_to_float(jgb10y_yield_percent) or 0.0) / 100.0
    growth_decimal = (_to_float(erp_growth_percent) or 0.0) / 100.0
    dividend_percent = _to_float(dividend_yield_index_percent) or 0.0
    if erp_method == "method_a" and forward_per > 0:
        return (1.0 / forward_per) - jgb_decimal
    if erp_method == "method_b" and forward_per > 0:
        return (1.0 / forward_per) + growth_decimal - jgb_decimal
    if erp_method == "method_c":
        return max(0.0, (dividend_percent / 100.0) + growth_decimal)
    return 0.0


def calculate_growth_center_percent(erp_method, erp_growth_percent):
    if erp_method == "method_b":
        return erp_growth_percent
    if erp_method == "method_c":
        return 0.0
    return None


def calculate_valuation_label(
    price,
    fair_price_core_low,
    fair_price_core_high,
    fair_price_wide_low,
    fair_price_wide_high,
):
    if (
        price is None
        or fair_price_core_low is None
        or fair_price_core_high is None
        or fair_price_wide_low is None
        or fair_price_wide_high is None
    ):
        return "判定不可"
    if price > fair_price_wide_high:
        return "Over +"
    if price < fair_price_wide_low:
        return "Deep Under"
    if price > fair_price_core_high:
        return "Over"
    if price < fair_price_core_low:
        return "Under"
    return "Fair"


def build_anchor_snapshot(
    anchor_price,
    forward_per,
    jgb10y_yield_percent,
    dividend_yield_index_percent=None,
    erp_method=DEFAULT_ERP_METHOD,
    erp_growth_percent=None,
    growth_core_ratio=DEFAULT_GROWTH_CORE_RATIO,
    growth_wide_ratio=DEFAULT_GROWTH_WIDE_RATIO,
    as_of_date=None,
):
    normalized_anchor_price = _normalize_positive_float(anchor_price)
    normalized_forward_per = _normalize_positive_float(forward_per)
    normalized_jgb10y = _to_float(jgb10y_yield_percent)
    if (
        normalized_anchor_price is None
        or normalized_forward_per is None
        or normalized_jgb10y is None
    ):
        return None
    normalized_dividend = _to_float(dividend_yield_index_percent)
    normalized_method = normalize_erp_method(erp_method)
    normalized_growth = normalize_growth_percent(
        erp_growth_percent,
        normalized_method,
    )
    normalized_core_ratio = normalize_ratio(
        growth_core_ratio,
        DEFAULT_GROWTH_CORE_RATIO,
    )
    normalized_wide_ratio = normalize_ratio(
        growth_wide_ratio,
        DEFAULT_GROWTH_WIDE_RATIO,
    )
    erp_fixed = calculate_erp_fixed(
        normalized_method,
        normalized_forward_per,
        normalized_jgb10y,
        normalized_dividend,
        normalized_growth,
    )
    growth_center_percent = calculate_growth_center_percent(
        normalized_method,
        normalized_growth,
    )
    bias = calculate_bias(
        normalized_anchor_price,
        normalized_forward_per,
        dividend_yield_index_percent=normalized_dividend,
        jgb10y_yield_percent=normalized_jgb10y,
        erp_fixed=erp_fixed,
        growth_center_percent=growth_center_percent,
        growth_core_ratio=normalized_core_ratio,
        growth_wide_ratio=normalized_wide_ratio,
    )
    anchor_date = as_of_date or datetime.date.today().isoformat()
    return {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "anchor_date": anchor_date,
        "anchor_price": round(normalized_anchor_price, 0),
        "forward_per": normalized_forward_per,
        "jgb10y_yield_percent": normalized_jgb10y,
        "dividend_yield_index_percent": normalized_dividend,
        "erp_method": normalized_method,
        "erp_growth_percent": normalized_growth,
        "growth_core_ratio": normalized_core_ratio,
        "growth_wide_ratio": normalized_wide_ratio,
        "forward_eps": bias.get("forward_eps"),
        "fair_price_mid": bias.get("fair_price_mid"),
        "fair_price_core_low": bias.get("fair_price_core_low"),
        "fair_price_core_high": bias.get("fair_price_core_high"),
        "fair_price_wide_low": bias.get("fair_price_wide_low"),
        "fair_price_wide_high": bias.get("fair_price_wide_high"),
        "erp_percent": bias.get("erp_percent"),
        "growth_core_width_percent": bias.get("growth_core_width_percent"),
        "growth_wide_width_percent": bias.get("growth_wide_width_percent"),
    }


def is_valid_anchor_snapshot(payload):
    if not isinstance(payload, dict):
        return False
    required_keys = (
        "anchor_date",
        "anchor_price",
        "forward_per",
        "jgb10y_yield_percent",
        "fair_price_core_low",
        "fair_price_core_high",
        "fair_price_wide_low",
        "fair_price_wide_high",
    )
    for key in required_keys:
        if key not in payload:
            return False
    if _normalize_positive_float(payload.get("anchor_price")) is None:
        return False
    if _normalize_positive_float(payload.get("forward_per")) is None:
        return False
    if _to_float(payload.get("jgb10y_yield_percent")) is None:
        return False
    for key in (
        "fair_price_core_low",
        "fair_price_core_high",
        "fair_price_wide_low",
        "fair_price_wide_high",
    ):
        if _to_float(payload.get(key)) is None:
            return False
    return True


def is_valid_anchor_config(payload):
    if not isinstance(payload, dict):
        return False
    if _normalize_positive_float(payload.get("anchor_price")) is None:
        return False
    forward_per = payload.get("forward_per")
    if forward_per is not None and _normalize_positive_float(forward_per) is None:
        return False
    if _to_float(payload.get("jgb10y_yield_percent")) is None:
        return False
    anchor_date = payload.get("anchor_date")
    if anchor_date is not None and not isinstance(anchor_date, str):
        return False
    return True


def _extract_anchor_config(payload):
    if not isinstance(payload, dict):
        return None
    config = payload.get(ANCHOR_CONFIG_KEY)
    if not isinstance(config, dict):
        return None
    return config


def load_anchor_snapshot(path=NIKKEI_PER_DATA_PATH):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load anchor config (%s): %s", path, exc)
        return None
    config = _extract_anchor_config(payload)
    if config is None:
        return None
    if not is_valid_anchor_config(config):
        logger.warning("Invalid anchor config (%s)", path)
        return None
    forward_per = config.get("forward_per")
    if forward_per is None and isinstance(payload, dict):
        forward_per = payload.get("index_based")
    dividend_yield_index_percent = config.get("dividend_yield_index_percent")
    if (
        dividend_yield_index_percent is None
        and isinstance(payload, dict)
        and isinstance(payload.get("dividend_yield"), dict)
    ):
        dividend_yield_index_percent = payload["dividend_yield"].get("index_based")
    snapshot = build_anchor_snapshot(
        anchor_price=config.get("anchor_price"),
        forward_per=forward_per,
        jgb10y_yield_percent=config.get("jgb10y_yield_percent"),
        dividend_yield_index_percent=dividend_yield_index_percent,
        erp_method=config.get("erp_method", DEFAULT_ERP_METHOD),
        erp_growth_percent=config.get("erp_growth_percent"),
        growth_core_ratio=config.get(
            "growth_core_ratio",
            DEFAULT_GROWTH_CORE_RATIO,
        ),
        growth_wide_ratio=config.get(
            "growth_wide_ratio",
            DEFAULT_GROWTH_WIDE_RATIO,
        ),
        as_of_date=config.get("anchor_date"),
    )
    if not snapshot or not is_valid_anchor_snapshot(snapshot):
        logger.warning("Failed to build anchor snapshot (%s)", path)
        return None
    return snapshot
