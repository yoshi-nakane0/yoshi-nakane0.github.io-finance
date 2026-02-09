from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache
from django.shortcuts import render

from .anchor_snapshot import (
    calculate_erp_fixed,
    calculate_growth_center_percent,
    calculate_valuation_label,
    load_anchor_snapshot,
    normalize_erp_method,
    normalize_growth_percent,
    normalize_ratio,
)
from .nikkei_bias import calculate_bias, get_jgb10y_yield_percent, get_nikkei_per_values

def index(request):
    try:
        # キャッシュキー
        CACHE_KEY_FWD = 'nikkei_forward_per'
        CACHE_KEY_PRICE = 'nikkei_price'
        CACHE_KEY_JGB = 'nikkei_jgb10y_yield_percent'
        CACHE_KEY_DIVIDEND_INDEX = 'nikkei_dividend_yield_index'
        CACHE_TTL_PRICE = 300
        CACHE_TTL_JGB = 3600
        
        # 1. パラメータ確認: update=true なら強制更新
        force_update = request.GET.get('update') == 'true'
        erp_fixed = 0.0
        erp_growth_input = None
        erp_growth_percent = None
        erp_method = normalize_erp_method(
            request.GET.get('erp_method', 'method_a')
        )

        def parse_float_param(value):
            if not value:
                return None
            cleaned = value.replace(',', '').replace('%', '').strip()
            try:
                return float(cleaned)
            except ValueError:
                return None

        def normalize_price(value):
            if value is None:
                return None
            try:
                normalized = int(float(value))
            except (TypeError, ValueError):
                return None
            return normalized if normalized > 0 else None

        growth_param = request.GET.get('erp_growth')
        erp_growth_percent = normalize_growth_percent(
            parse_float_param(growth_param),
            erp_method,
        )
        if erp_method == 'method_b' and erp_growth_percent is not None:
            erp_growth_input = f"{erp_growth_percent:.1f}"

        price_override = normalize_price(
            parse_float_param(request.GET.get('price'))
        )

        def format_price_param(value):
            if value is None:
                return None
            return f"{int(value)}"

        price_param = format_price_param(price_override)

        core_ratio_param = request.GET.get('growth_core_ratio')
        wide_ratio_param = request.GET.get('growth_wide_ratio')
        growth_core_ratio = normalize_ratio(
            parse_float_param(core_ratio_param),
            default_value=0.6,
        )
        growth_wide_ratio = normalize_ratio(
            parse_float_param(wide_ratio_param),
            default_value=0.7,
        )
        growth_core_ratio_input = f"{growth_core_ratio:.1f}"
        growth_wide_ratio_input = f"{growth_wide_ratio:.1f}"
        
        # 2. キャッシュから取得
        forward_per = cache.get(CACHE_KEY_FWD)
        price = (
            price_override
            if price_override is not None
            else normalize_price(cache.get(CACHE_KEY_PRICE))
        )
        jgb10y_yield_percent = cache.get(CACHE_KEY_JGB)
        dividend_yield_index_percent = cache.get(CACHE_KEY_DIVIDEND_INDEX)
        
        # 3. 更新リクエストまたは未取得時はデータ取得・更新 (All or Nothing)
        needs_update = force_update or any(
            value is None
            for value in (
                forward_per,
                price,
                jgb10y_yield_percent,
                dividend_yield_index_percent,
            )
        )
        if needs_update:
            with ThreadPoolExecutor() as executor:
                futures = {}
                futures['per_values'] = executor.submit(get_nikkei_per_values)
                futures['jgb'] = executor.submit(get_jgb10y_yield_percent)

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

        if price_override is not None:
            price = price_override
            cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)
        
        # 4. 欠落時は0.00を使用
        if forward_per is None:
            forward_per = 0.0
        if price is None:
            price = 0.0
        if jgb10y_yield_percent is None:
            jgb10y_yield_percent = 0.0

        anchor_snapshot = load_anchor_snapshot()
        anchor_enabled = anchor_snapshot is not None
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

        data["price"] = round(price, 0)
        data["forward_per"] = forward_per
        data["jgb10y_yield_percent"] = jgb10y_yield_percent
        data["dividend_yield_index_percent"] = dividend_yield_index_percent
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

        data["price_display"] = format_price(data.get("price"), decimals=0)
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
        
        context = {
            'data': data,
            'updated': force_update,
            'erp_method': erp_method,
            'erp_growth_input': erp_growth_input,
            'price_param': price_param,
            'growth_core_ratio_input': growth_core_ratio_input,
            'growth_wide_ratio_input': growth_wide_ratio_input,
        }
    except Exception as e:
        context = {
            'error': str(e)
        }
    return render(request, 'BaseCalc/index.html', context)
