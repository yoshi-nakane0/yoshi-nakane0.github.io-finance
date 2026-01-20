from django.shortcuts import render
from django.core.cache import cache
from concurrent.futures import ThreadPoolExecutor
from .nikkei_bias import (
    calculate_bias,
    get_actual_per,
    get_nikkei_per_values,
    get_jgb10y_yield_percent,
    get_nikkei_price,
    get_nominal_gdp_growth_median,
)

def index(request):
    try:
        # キャッシュキー
        CACHE_KEY_FWD = 'nikkei_forward_per'
        CACHE_KEY_FWD_WEIGHTED = 'nikkei_forward_per_weighted'
        CACHE_KEY_ACT = 'nikkei_actual_per'
        CACHE_KEY_PRICE = 'nikkei_price'
        CACHE_KEY_GDP = 'nikkei_gdp_growth_median'
        CACHE_KEY_JGB = 'nikkei_jgb10y_yield_percent'
        CACHE_TTL_PRICE = 300
        CACHE_TTL_GDP = 86400
        CACHE_TTL_JGB = 3600
        
        # 1. パラメータ確認: update=true なら強制更新
        force_update = request.GET.get('update') == 'true'
        erp_fixed = None
        erp_input = None
        erp_query = None
        erp_param = request.GET.get('erp')
        if erp_param:
            cleaned = erp_param.replace(',', '').replace('%', '').strip()
            try:
                erp_value = float(cleaned)
            except ValueError:
                erp_value = None
            if erp_value is not None:
                erp_fixed = erp_value / 100.0
                erp_input = f"{erp_value:.2f}"
                erp_query = erp_input
        
        # 2. キャッシュから取得
        forward_per = cache.get(CACHE_KEY_FWD)
        forward_per_weighted = cache.get(CACHE_KEY_FWD_WEIGHTED)
        actual_per = cache.get(CACHE_KEY_ACT)
        price = cache.get(CACHE_KEY_PRICE)
        gdp_growth_median = cache.get(CACHE_KEY_GDP)
        jgb10y_yield_percent = cache.get(CACHE_KEY_JGB)
        
        # 3. 更新リクエストまたは未取得時はデータ取得・更新 (All or Nothing)
        needs_update = force_update or any(
            value is None
            for value in (
                forward_per,
                forward_per_weighted,
                actual_per,
                price,
                gdp_growth_median,
                jgb10y_yield_percent,
            )
        )
        if needs_update:
            with ThreadPoolExecutor() as executor:
                futures = {}
                futures['per_values'] = executor.submit(get_nikkei_per_values)
                futures['a_per'] = executor.submit(get_actual_per)
                futures['price'] = executor.submit(get_nikkei_price)
                futures['gdp'] = executor.submit(get_nominal_gdp_growth_median)
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
                
                if 'a_per' in futures:
                    val = futures['a_per'].result()
                    if val:
                        actual_per = val
                        cache.set(CACHE_KEY_ACT, actual_per, timeout=None)

                if 'price' in futures:
                    val = futures['price'].result()
                    if val is not None:
                        price = val
                        cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)

                if 'gdp' in futures:
                    val = futures['gdp'].result()
                    if val is not None:
                        gdp_growth_median = val
                        cache.set(CACHE_KEY_GDP, gdp_growth_median, timeout=CACHE_TTL_GDP)

                if 'jgb' in futures:
                    val = futures['jgb'].result()
                    if val is not None:
                        jgb10y_yield_percent = val
                        cache.set(CACHE_KEY_JGB, jgb10y_yield_percent, timeout=CACHE_TTL_JGB)
        
        # 4. 欠落時は0.00を使用
        if forward_per is None:
            forward_per = 0.0
        if forward_per_weighted is None:
            forward_per_weighted = 0.0
        if actual_per is None:
            actual_per = 0.0
        if price is None:
            price = 0.0
        if gdp_growth_median is None:
            gdp_growth_median = 0.0
        if jgb10y_yield_percent is None:
            jgb10y_yield_percent = 0.0
        if erp_fixed is None:
            erp_fixed = 0.0

        # 5. 計算実行
        data = calculate_bias(
            price,
            forward_per,
            actual_per,
            gdp_growth_median=gdp_growth_median,
            jgb10y_yield_percent=jgb10y_yield_percent,
            forward_per_weighted=forward_per_weighted,
            erp_fixed=erp_fixed,
        )
        if erp_query is None and data.get('erp_percent') is not None:
            erp_query = f"{data['erp_percent']:.2f}"
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
            'erp_input': erp_input,
            'erp_query': erp_query,
        }
    except Exception as e:
        context = {
            'error': str(e)
        }
    return render(request, 'BaseCalc/index.html', context)
