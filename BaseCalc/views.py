from django.shortcuts import render
from django.core.cache import cache
from concurrent.futures import ThreadPoolExecutor
from .nikkei_bias import (
    calculate_bias,
    get_actual_per,
    get_nikkei_per_values,
    get_jgb10y_yield_percent,
)

def index(request):
    try:
        # キャッシュキー
        CACHE_KEY_FWD = 'nikkei_forward_per'
        CACHE_KEY_FWD_WEIGHTED = 'nikkei_forward_per_weighted'
        CACHE_KEY_ACT = 'nikkei_actual_per'
        CACHE_KEY_PRICE = 'nikkei_price'
        CACHE_KEY_JGB = 'nikkei_jgb10y_yield_percent'
        CACHE_KEY_DIVIDEND_INDEX = 'nikkei_dividend_yield_index'
        CACHE_KEY_DIVIDEND_WEIGHTED = 'nikkei_dividend_yield_weighted'
        CACHE_TTL_PRICE = 300
        CACHE_TTL_JGB = 3600
        
        # 1. パラメータ確認: update=true なら強制更新
        force_update = request.GET.get('update') == 'true'
        erp_fixed = 0.0
        erp_growth_input = None
        erp_growth_percent = None
        erp_method = request.GET.get('erp_method', 'method_a')
        if erp_method not in {'method_a', 'method_b', 'method_c'}:
            erp_method = 'method_a'

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

        allowed_growth_values = {1.7, 2.1, 2.7}
        def normalize_growth(value):
            if value is None:
                return None
            try:
                rounded = round(float(value), 1)
            except (TypeError, ValueError):
                return None
            return rounded if rounded in allowed_growth_values else None

        growth_param = request.GET.get('erp_growth')
        erp_growth_percent = normalize_growth(parse_float_param(growth_param))
        if erp_growth_percent is not None:
            erp_growth_input = f"{erp_growth_percent:.1f}"

        price_override = normalize_price(
            parse_float_param(request.GET.get('price'))
        )

        def format_price_param(value):
            if value is None:
                return None
            return f"{int(value)}"

        price_param = format_price_param(price_override)
        
        # 2. キャッシュから取得
        forward_per = cache.get(CACHE_KEY_FWD)
        forward_per_weighted = cache.get(CACHE_KEY_FWD_WEIGHTED)
        actual_per = cache.get(CACHE_KEY_ACT)
        price = (
            price_override
            if price_override is not None
            else normalize_price(cache.get(CACHE_KEY_PRICE))
        )
        jgb10y_yield_percent = cache.get(CACHE_KEY_JGB)
        dividend_yield_index_percent = cache.get(CACHE_KEY_DIVIDEND_INDEX)
        dividend_yield_weighted_percent = cache.get(CACHE_KEY_DIVIDEND_WEIGHTED)
        
        # 3. 更新リクエストまたは未取得時はデータ取得・更新 (All or Nothing)
        needs_update = force_update or any(
            value is None
            for value in (
                forward_per,
                forward_per_weighted,
                actual_per,
                price,
                jgb10y_yield_percent,
                dividend_yield_index_percent,
                dividend_yield_weighted_percent,
            )
        )
        if needs_update:
            with ThreadPoolExecutor() as executor:
                futures = {}
                futures['per_values'] = executor.submit(get_nikkei_per_values)
                futures['a_per'] = executor.submit(get_actual_per)
                futures['jgb'] = executor.submit(get_jgb10y_yield_percent)

                # 結果の回収と変数・キャッシュ更新
                if 'per_values' in futures:
                    per_vals = futures['per_values'].result()
                    if per_vals:
                        if per_vals.get('index_based'):
                            forward_per = per_vals['index_based']
                            cache.set(CACHE_KEY_FWD, forward_per, timeout=None)
                        if per_vals.get('weighted_average'):
                            forward_per_weighted = per_vals['weighted_average']
                            cache.set(CACHE_KEY_FWD_WEIGHTED, forward_per_weighted, timeout=None)
                        if per_vals.get('dividend_yield_index_based') is not None:
                            dividend_yield_index_percent = per_vals['dividend_yield_index_based']
                            cache.set(CACHE_KEY_DIVIDEND_INDEX, dividend_yield_index_percent, timeout=None)
                        if per_vals.get('dividend_yield_simple_average') is not None:
                            dividend_yield_weighted_percent = per_vals['dividend_yield_simple_average']
                            cache.set(CACHE_KEY_DIVIDEND_WEIGHTED, dividend_yield_weighted_percent, timeout=None)
                
                if 'a_per' in futures:
                    val = futures['a_per'].result()
                    if val:
                        actual_per = val
                        cache.set(CACHE_KEY_ACT, actual_per, timeout=None)

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
        if forward_per_weighted is None:
            forward_per_weighted = 0.0
        if actual_per is None:
            actual_per = 0.0
        if price is None:
            price = 0.0
        if jgb10y_yield_percent is None:
            jgb10y_yield_percent = 0.0
        if erp_growth_percent is None and erp_method in ('method_b', 'method_c'):
            erp_growth_percent = 2.1
            erp_growth_input = "2.1"

        growth_decimal = (erp_growth_percent or 0.0) / 100.0
        jgb_decimal = (jgb10y_yield_percent or 0.0) / 100.0
        if erp_method == 'method_a' and forward_per > 0:
            erp_fixed = (1.0 / forward_per) - jgb_decimal
        elif erp_method == 'method_b' and forward_per > 0:
            erp_fixed = (1.0 / forward_per) + growth_decimal - jgb_decimal
        elif erp_method == 'method_c':
            dividend_percent = (
                dividend_yield_index_percent
                if dividend_yield_index_percent is not None
                else 0.0
            )
            dividend_decimal = dividend_percent / 100.0
            erp_fixed = dividend_decimal + growth_decimal - jgb_decimal

        # 5. 計算実行
        data = calculate_bias(
            price,
            forward_per,
            actual_per,
            dividend_yield_index_percent=dividend_yield_index_percent,
            dividend_yield_weighted_percent=dividend_yield_weighted_percent,
            jgb10y_yield_percent=jgb10y_yield_percent,
            forward_per_weighted=forward_per_weighted,
            erp_fixed=erp_fixed,
        )
        def format_price(value, decimals=0):
            if value is None:
                return ""
            try:
                return f"{value:,.{decimals}f}"
            except (TypeError, ValueError):
                return ""

        data["price_display"] = format_price(data.get("price"), decimals=0)
        data["forward_eps_display"] = format_price(data.get("forward_eps"), decimals=2)
        data["forward_eps_weighted_display"] = format_price(data.get("forward_eps_weighted"), decimals=2)
        data["fair_price_core_low_display"] = format_price(data.get("fair_price_core_low"), decimals=0)
        data["fair_price_core_high_display"] = format_price(data.get("fair_price_core_high"), decimals=0)
        data["fair_price_wide_low_display"] = format_price(data.get("fair_price_wide_low"), decimals=0)
        data["fair_price_wide_high_display"] = format_price(data.get("fair_price_wide_high"), decimals=0)
        
        context = {
            'data': data,
            'updated': force_update,
            'erp_method': erp_method,
            'erp_growth_input': erp_growth_input,
            'price_param': price_param,
        }
    except Exception as e:
        context = {
            'error': str(e)
        }
    return render(request, 'BaseCalc/index.html', context)
