from django.shortcuts import render
from django.core.cache import cache
from .nikkei_bias import (
    calculate_bias,
    get_actual_per,
    get_forward_per,
    get_jgb10y_yield_percent,
    get_nikkei_price,
    get_nominal_gdp_growth_median,
)

def index(request):
    try:
        # キャッシュキー
        CACHE_KEY_FWD = 'nikkei_forward_per'
        CACHE_KEY_ACT = 'nikkei_actual_per'
        CACHE_KEY_PRICE = 'nikkei_price'
        CACHE_KEY_GDP = 'nikkei_gdp_growth_median'
        CACHE_KEY_JGB = 'nikkei_jgb10y_yield_percent'
        CACHE_TTL_PRICE = 300
        CACHE_TTL_GDP = 86400
        CACHE_TTL_JGB = 3600
        
        # 1. パラメータ確認: update=true なら強制更新
        force_update = request.GET.get('update') == 'true'
        
        # 2. キャッシュから取得
        forward_per = cache.get(CACHE_KEY_FWD)
        actual_per = cache.get(CACHE_KEY_ACT)
        price = cache.get(CACHE_KEY_PRICE)
        gdp_growth_median = cache.get(CACHE_KEY_GDP)
        jgb10y_yield_percent = cache.get(CACHE_KEY_JGB)
        
        if force_update:
            # 更新リクエスト時はスクレイピング実行
            
            # Forward PER
            scraped_f_per = get_forward_per()
            if scraped_f_per:
                forward_per = scraped_f_per
                cache.set(CACHE_KEY_FWD, forward_per, timeout=None)
                
            # Actual PER
            scraped_a_per = get_actual_per()
            if scraped_a_per:
                actual_per = scraped_a_per
                cache.set(CACHE_KEY_ACT, actual_per, timeout=None)

            # Nikkei Price
            scraped_price = get_nikkei_price()
            if scraped_price is not None:
                price = scraped_price
                cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)

            # GDP Median Growth
            scraped_gdp_growth = get_nominal_gdp_growth_median()
            if scraped_gdp_growth is not None:
                gdp_growth_median = scraped_gdp_growth
                cache.set(CACHE_KEY_GDP, gdp_growth_median, timeout=CACHE_TTL_GDP)

            # JGB 10Y Yield
            scraped_jgb_yield = get_jgb10y_yield_percent()
            if scraped_jgb_yield is not None:
                jgb10y_yield_percent = scraped_jgb_yield
                cache.set(CACHE_KEY_JGB, jgb10y_yield_percent, timeout=CACHE_TTL_JGB)

        if price is None:
            scraped_price = get_nikkei_price()
            if scraped_price is not None:
                price = scraped_price
                cache.set(CACHE_KEY_PRICE, price, timeout=CACHE_TTL_PRICE)

        if gdp_growth_median is None:
            scraped_gdp_growth = get_nominal_gdp_growth_median()
            if scraped_gdp_growth is not None:
                gdp_growth_median = scraped_gdp_growth
                cache.set(CACHE_KEY_GDP, gdp_growth_median, timeout=CACHE_TTL_GDP)

        if jgb10y_yield_percent is None:
            scraped_jgb_yield = get_jgb10y_yield_percent()
            if scraped_jgb_yield is not None:
                jgb10y_yield_percent = scraped_jgb_yield
                cache.set(CACHE_KEY_JGB, jgb10y_yield_percent, timeout=CACHE_TTL_JGB)
        
        # キャッシュがない場合（初回など）はデフォルト値を使用
        if forward_per is None:
            forward_per = 23.84 
            
        if actual_per is None:
            actual_per = 21.77 # ユーザー指定の例示値に近い値をデフォルトに
        
        if price is None:
            price = 54000

        # 3. 計算実行
        data = calculate_bias(
            price,
            forward_per,
            actual_per,
            gdp_growth_median=gdp_growth_median,
            jgb10y_yield_percent=jgb10y_yield_percent,
        )
        
        context = {
            'data': data,
            'updated': force_update
        }
    except Exception as e:
        context = {
            'error': str(e)
        }
    return render(request, 'BaseCalc/index.html', context)
