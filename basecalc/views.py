from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from .anchor_snapshot import (
    DEFAULT_ERP_METHOD,
    DEFAULT_GROWTH_CORE_RATIO,
    DEFAULT_GROWTH_WIDE_RATIO,
    calculate_erp_fixed,
    calculate_growth_center_percent,
    calculate_valuation_label,
    load_anchor_snapshot,
    normalize_erp_method,
    normalize_growth_percent,
    normalize_ratio,
)
from .futures_sentiment import (
    calculate_futures_sentiment,
    get_nikkei_futures_snapshot,
)
from .nikkei_bias import calculate_bias, get_jgb10y_yield_percent, get_nikkei_per_values


@ensure_csrf_cookie
def index(request):
    can_update_basecalc_data = (
        request.user.is_authenticated and request.user.is_staff
    )
    if request.method == 'POST':
        if request.POST.get('action') != 'update':
            return HttpResponseBadRequest('Invalid action')
        if not can_update_basecalc_data:
            return HttpResponseForbidden('Forbidden')

    try:
        params = request.POST if request.method == 'POST' else request.GET
        # キャッシュキー
        CACHE_KEY_FWD = 'nikkei_forward_per'
        CACHE_KEY_PRICE = 'nikkei_price'
        CACHE_KEY_FUTURES = 'nikkei_futures_snapshot'
        CACHE_KEY_JGB = 'nikkei_jgb10y_yield_percent'
        CACHE_KEY_DIVIDEND_INDEX = 'nikkei_dividend_yield_index'
        CACHE_TTL_PRICE = 300
        CACHE_TTL_FUTURES = 300
        CACHE_TTL_JGB = 3600
        
        # 1. パラメータ確認: 更新は管理者 POST のみ
        force_update = request.method == 'POST'
        erp_fixed = 0.0
        erp_growth_input = None
        erp_growth_percent = None

        def parse_float_param(value):
            if not value:
                return None
            cleaned = value.replace(',', '').replace('%', '').strip()
            try:
                return float(cleaned)
            except ValueError:
                return None

        anchor_snapshot = load_anchor_snapshot()
        anchor_enabled = anchor_snapshot is not None
        default_erp_method = (
            anchor_snapshot.get('erp_method', DEFAULT_ERP_METHOD)
            if anchor_enabled
            else DEFAULT_ERP_METHOD
        )
        erp_method = normalize_erp_method(
            params.get('erp_method', default_erp_method)
        )

        def normalize_price(value):
            if value is None:
                return None
            try:
                normalized = int(float(value))
            except (TypeError, ValueError):
                return None
            return normalized if normalized > 0 else None

        growth_param = params.get('erp_growth')
        default_growth_percent = (
            anchor_snapshot.get('erp_growth_percent')
            if anchor_enabled
            else None
        )
        growth_value = (
            parse_float_param(growth_param)
            if growth_param is not None
            else default_growth_percent
        )
        erp_growth_percent = normalize_growth_percent(
            growth_value,
            erp_method,
        )
        if erp_method == 'method_b' and erp_growth_percent is not None:
            erp_growth_input = f"{erp_growth_percent:.1f}"

        price_override = normalize_price(
            parse_float_param(params.get('price'))
        )

        def format_price_param(value):
            if value is None:
                return None
            return f"{int(value)}"

        price_param = format_price_param(price_override)

        core_ratio_param = params.get('growth_core_ratio')
        wide_ratio_param = params.get('growth_wide_ratio')
        default_growth_core_ratio = (
            anchor_snapshot.get('growth_core_ratio', DEFAULT_GROWTH_CORE_RATIO)
            if anchor_enabled
            else DEFAULT_GROWTH_CORE_RATIO
        )
        default_growth_wide_ratio = (
            anchor_snapshot.get('growth_wide_ratio', DEFAULT_GROWTH_WIDE_RATIO)
            if anchor_enabled
            else DEFAULT_GROWTH_WIDE_RATIO
        )
        growth_core_ratio = normalize_ratio(
            parse_float_param(core_ratio_param),
            default_value=default_growth_core_ratio,
        )
        growth_wide_ratio = normalize_ratio(
            parse_float_param(wide_ratio_param),
            default_value=default_growth_wide_ratio,
        )
        growth_core_ratio_input = f"{growth_core_ratio:.1f}"
        growth_wide_ratio_input = f"{growth_wide_ratio:.1f}"
        
        # 2. キャッシュから取得
        forward_per = cache.get(CACHE_KEY_FWD)
        futures_snapshot = cache.get(CACHE_KEY_FUTURES)
        cached_futures_price = (
            futures_snapshot.get('price')
            if isinstance(futures_snapshot, dict)
            else None
        )
        price = (
            price_override
            if price_override is not None
            else normalize_price(cached_futures_price or cache.get(CACHE_KEY_PRICE))
        )
        jgb10y_yield_percent = cache.get(CACHE_KEY_JGB)
        dividend_yield_index_percent = cache.get(CACHE_KEY_DIVIDEND_INDEX)
        
        # 3. 外部取得と共有キャッシュ更新は管理者 POST のみ
        if force_update:
            with ThreadPoolExecutor() as executor:
                futures = {}
                futures['per_values'] = executor.submit(get_nikkei_per_values)
                futures['jgb'] = executor.submit(get_jgb10y_yield_percent)
                futures['futures'] = executor.submit(get_nikkei_futures_snapshot)

                # 結果の回収と変数・キャッシュ更新
                if 'per_values' in futures:
                    per_vals = futures['per_values'].result()
                    if per_vals:
                        if per_vals.get('index_based'):
                            forward_per = per_vals['index_based']
                            cache.set(CACHE_KEY_FWD, forward_per, timeout=None)
                        if per_vals.get('dividend_yield_index_based') is not None:
                            dividend_yield_index_percent = per_vals['dividend_yield_index_based']
                            cache.set(CACHE_KEY_DIVIDEND_INDEX, dividend_yield_index_percent, timeout=None)

                if 'jgb' in futures:
                    val = futures['jgb'].result()
                    if val is not None:
                        jgb10y_yield_percent = val
                        cache.set(CACHE_KEY_JGB, jgb10y_yield_percent, timeout=CACHE_TTL_JGB)

                if 'futures' in futures:
                    val = futures['futures'].result()
                    if val and val.get('price') is not None:
                        futures_snapshot = val
                        price = normalize_price(val.get('price'))
                        cache.set(CACHE_KEY_FUTURES, val, timeout=CACHE_TTL_FUTURES)
                        cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)

        if price_override is not None and not force_update:
            price = price_override
        if force_update and price is not None:
            cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)
        
        # 4. 欠落時は0.00を使用
        if forward_per is None:
            forward_per = 0.0
        if price is None:
            price = 0.0
        if jgb10y_yield_percent is None:
            jgb10y_yield_percent = 0.0

        calc_price = (
            anchor_snapshot.get('anchor_price')
            if anchor_enabled
            else price
        )
        calc_forward_per = (
            anchor_snapshot.get('forward_per')
            if anchor_enabled
            else forward_per
        )
        calc_jgb10y_yield_percent = (
            anchor_snapshot.get('jgb10y_yield_percent')
            if anchor_enabled
            else jgb10y_yield_percent
        )
        calc_dividend_yield_index_percent = (
            anchor_snapshot.get('dividend_yield_index_percent')
            if anchor_enabled
            else dividend_yield_index_percent
        )
        erp_fixed = calculate_erp_fixed(
            erp_method,
            calc_forward_per,
            calc_jgb10y_yield_percent,
            calc_dividend_yield_index_percent,
            erp_growth_percent,
        )
        growth_center_percent = calculate_growth_center_percent(
            erp_method,
            erp_growth_percent,
        )

        # 5. 計算実行
        data = calculate_bias(
            calc_price,
            calc_forward_per,
            dividend_yield_index_percent=calc_dividend_yield_index_percent,
            jgb10y_yield_percent=calc_jgb10y_yield_percent,
            erp_fixed=erp_fixed,
            growth_center_percent=growth_center_percent,
            growth_core_ratio=growth_core_ratio,
            growth_wide_ratio=growth_wide_ratio,
        )
        def format_price(value, decimals=0):
            if value is None:
                return ""
            try:
                return f"{value:,.{decimals}f}"
            except (TypeError, ValueError):
                return ""

        def format_percent(value):
            if value is None:
                return ""
            try:
                return f"{value:+.2f}%"
            except (TypeError, ValueError):
                return ""

        def valuation_class(label):
            if label in ("Over", "Over +"):
                return "value text-red"
            if label in ("Under", "Deep Under"):
                return "value text-green"
            if label == "Fair":
                return "value text-blue"
            return "value text-muted"

        def gap_class(value):
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return "value text-muted"
            if numeric > 0:
                return "value text-red"
            if numeric < 0:
                return "value text-green"
            return "value text-blue"

        def sentiment_class(key):
            if key == "bullish":
                return "value text-green"
            if key == "bearish":
                return "value text-red"
            return "value text-blue"

        data["price"] = round(price, 0)
        data["forward_per"] = calc_forward_per
        data["jgb10y_yield_percent"] = calc_jgb10y_yield_percent
        data["dividend_yield_index_percent"] = calc_dividend_yield_index_percent
        data["valuation_label"] = calculate_valuation_label(
            price,
            data.get("fair_price_core_low"),
            data.get("fair_price_core_high"),
            data.get("fair_price_wide_low"),
            data.get("fair_price_wide_high"),
        )
        fair_price_mid = data.get("fair_price_mid")
        if fair_price_mid:
            fair_price_gap = price - fair_price_mid
            data["fair_price_gap_pct"] = round(
                (fair_price_gap / fair_price_mid) * 100.0,
                2,
            )
        else:
            data["fair_price_gap_pct"] = None

        sentiment = calculate_futures_sentiment(
            price,
            data.get("fair_price_mid"),
            data.get("fair_price_core_low"),
            data.get("fair_price_core_high"),
            data.get("fair_price_wide_low"),
            data.get("fair_price_wide_high"),
            market_snapshot=futures_snapshot,
        )
        data.update(sentiment)

        data["price_display"] = format_price(data.get("price"), decimals=0)
        data["fair_price_gap_pct_display"] = format_percent(
            data.get("fair_price_gap_pct")
        )
        data["valuation_class"] = valuation_class(data.get("valuation_label"))
        data["fair_price_gap_class"] = gap_class(data.get("fair_price_gap_pct"))
        data["sentiment_class"] = sentiment_class(data.get("sentiment_key"))
        data["daily_change_pct_display"] = format_percent(data.get("daily_change_pct"))
        data["momentum_3d_pct_display"] = format_percent(data.get("momentum_3d_pct"))
        data["upper_target_display"] = format_price(data.get("upper_target"), decimals=0)
        data["lower_target_display"] = format_price(data.get("lower_target"), decimals=0)
        data["forward_eps_display"] = format_price(data.get("forward_eps"), decimals=2)
        data["fair_price_core_low_display"] = format_price(data.get("fair_price_core_low"), decimals=0)
        data["fair_price_core_high_display"] = format_price(data.get("fair_price_core_high"), decimals=0)
        data["fair_price_wide_low_display"] = format_price(data.get("fair_price_wide_low"), decimals=0)
        data["fair_price_wide_high_display"] = format_price(data.get("fair_price_wide_high"), decimals=0)
        if anchor_enabled:
            data["anchor_status_display"] = "ACTIVE"
            data["anchor_date_display"] = str(
                anchor_snapshot.get("anchor_date") or ""
            )
            data["anchor_price_display"] = format_price(
                anchor_snapshot.get("anchor_price"),
                decimals=0,
            )
            data["anchor_forward_per_display"] = format_price(
                anchor_snapshot.get("forward_per"),
                decimals=2,
            )
        else:
            data["anchor_status_display"] = "NOT SET"
            data["anchor_date_display"] = ""
            data["anchor_price_display"] = ""
            data["anchor_forward_per_display"] = ""

        price_param = format_price_param(price)
        
        context = {
            'data': data,
            'updated': force_update,
            'erp_method': erp_method,
            'erp_growth_input': erp_growth_input,
            'price_param': price_param,
            'growth_core_ratio_input': growth_core_ratio_input,
            'growth_wide_ratio_input': growth_wide_ratio_input,
            'can_update_basecalc_data': can_update_basecalc_data,
        }
    except Exception as e:
        context = {
            'error': str(e),
            'can_update_basecalc_data': can_update_basecalc_data,
        }
    return render(request, 'basecalc/index.html', context)
