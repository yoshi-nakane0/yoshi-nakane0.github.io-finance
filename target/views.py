# target/views.py
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from datetime import datetime, timezone, timedelta
import json
import random
import requests

# JST timezone
TZ_JST = timezone(timedelta(hours=9))

# Sector data constants
SPDR_TICKERS = {
    "通信サービス": {"ticker": "XLC", "icon": "📡", "color": "#FF6B6B"},
    "一般消費財": {"ticker": "XLY", "icon": "🛍️", "color": "#4ECDC4"},
    "生活必需品": {"ticker": "XLP", "icon": "🥖", "color": "#45B7D1"},
    "エネルギー": {"ticker": "XLE", "icon": "⚡", "color": "#96CEB4"},
    "金融": {"ticker": "XLF", "icon": "🏦", "color": "#FFEAA7"},
    "ヘルスケア": {"ticker": "XLV", "icon": "🏥", "color": "#DDA0DD"},
    "資本財": {"ticker": "XLI", "icon": "🏭", "color": "#98D8C8"},
    "素材": {"ticker": "XLB", "icon": "🏗️", "color": "#F7DC6F"},
    "不動産": {"ticker": "XLRE", "icon": "🏠", "color": "#BB8FCE"},
    "テクノロジー": {"ticker": "XLK", "icon": "💻", "color": "#85C1E9"},
    "公益事業": {"ticker": "XLU", "icon": "💡", "color": "#82E0AA"},
}

BENCHMARKS = {
    "Nikkei 225": {"ticker": "^N225", "icon": "🗾", "color": "#FF6B6B"},
    "Dow Jones": {"ticker": "^DJI", "icon": "🇺🇸", "color": "#4ECDC4"},
    "S&P 500": {"ticker": "^GSPC", "icon": "📊", "color": "#45B7D1"},
    "NASDAQ": {"ticker": "^IXIC", "icon": "🚀", "color": "#96CEB4"},
}

TOPIX17_SECTORS = {
    "食品": {"icon": "🍽️", "color": "#FF6B6B", "jpx_key": "Topix17Food"},
    "エネルギー資源": {"icon": "⛽", "color": "#4ECDC4", "jpx_key": "Topix17Energy"},
    "建設・資材": {"icon": "🏗️", "color": "#45B7D1", "jpx_key": "Topix17BuildingMaterial"},
    "原材料・化学": {"icon": "🧪", "color": "#96CEB4", "jpx_key": "Topix17MaterialChemistry"},
    "医薬品": {"icon": "💊", "color": "#FFEAA7", "jpx_key": "Topix17nostrum"},
    "自動車・輸送機": {"icon": "🚗", "color": "#DDA0DD", "jpx_key": "Topix17CarTransport"},
    "鉄鋼・非鉄": {"icon": "🔩", "color": "#98D8C8", "jpx_key": "Topix17SteelNonferrous"},
    "機械": {"icon": "⚙️", "color": "#F7DC6F", "jpx_key": "Topix17Machine"},
    "電機・精密": {"icon": "📱", "color": "#BB8FCE", "jpx_key": "Topix17ElectricalPrecision"},
    "情報通信・サービスその他": {"icon": "💻", "color": "#85C1E9", "jpx_key": "Topix17InformationService"},
    "電力・ガス": {"icon": "⚡", "color": "#82E0AA", "jpx_key": "Topix17ElectricPowerGas"},
    "運輸・物流": {"icon": "🚛", "color": "#F8C471", "jpx_key": "Topix17TransportationLogistics"},
    "商社・卸売": {"icon": "🏪", "color": "#F1948A", "jpx_key": "Topix17TradingWholesale"},
    "小売": {"icon": "🛒", "color": "#85C1E9", "jpx_key": "Topix17Retail"},
    "銀行": {"icon": "🏦", "color": "#BB8FCE", "jpx_key": "Topix17Bank"},
    "証券・商品先物": {"icon": "📈", "color": "#98D8C8", "jpx_key": "Topix17Finance"},
    "不動産": {"icon": "🏠", "color": "#82E0AA", "jpx_key": "Topix17RealEstate"},
}

def generate_sample_data(base_price: float, volatility: float = 0.05) -> tuple:
    """Generate sample financial data"""
    change_pct = random.uniform(-volatility * 100, volatility * 100)
    change_abs = base_price * (change_pct / 100)
    current_price = base_price + change_abs
    return current_price, change_abs, change_pct

def fetch_jpx_data():
    """Fetch real TOPIX-17 data from JPX"""
    try:
        url = "https://www.jpx.co.jp/market/indices/e_indices_stock_price3.txt"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Extract IndustryTypeStockIndex section
        if "IndustryTypeStockIndex" in data:
            return data["IndustryTypeStockIndex"]
        return {}
    except Exception as e:
        print(f"Failed to fetch JPX data: {e}")
        return {}

def fetch_yahoo_finance_data(ticker: str):
    """Fetch real data from Yahoo Finance using their API"""
    try:
        # Yahoo Finance API endpoint
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if 'chart' in data and 'result' in data['chart'] and data['chart']['result']:
            result = data['chart']['result'][0]
            meta = result['meta']
            
            current_price = meta.get('regularMarketPrice', 0)
            previous_close = meta.get('previousClose', current_price)
            
            change_abs = current_price - previous_close
            change_pct = (change_abs / previous_close * 100) if previous_close != 0 else 0
            
            return current_price, change_abs, change_pct
        
        return None, None, None
    except Exception as e:
        print(f"Failed to fetch Yahoo Finance data for {ticker}: {e}")
        return None, None, None

def fetch_price_change(ticker: str) -> tuple:
    """Fetch price change data (using sample data for now)"""
    if ticker.startswith("^"):
        base_prices = {"^N225": 32000, "^DJI": 35000, "^GSPC": 4500, "^IXIC": 14000}
        base_price = base_prices.get(ticker, 1000)
    else:
        base_price = random.uniform(80, 200)
    
    return generate_sample_data(base_price)

def get_sector_data_sample():
    """Get sample sector data for initial page load"""
    sectors = []
    
    # US SPDR sectors - use sample data only
    for sector, data in SPDR_TICKERS.items():
        ticker = data["ticker"]
        price, change, pct = fetch_price_change(ticker)
        
        sectors.append({
            "group": "US",
            "sector": f"{sector} - {ticker}",
            "current": price,
            "change": change,
            "change_pct": pct,
            "icon": data["icon"],
            "color": data["color"],
        })
    
    # JP TOPIX-17 sectors - use sample data only
    for sector, data in TOPIX17_SECTORS.items():
        base_price = random.uniform(1000, 3000)
        current_price, change_abs, change_pct = generate_sample_data(base_price)
        
        sectors.append({
            "group": "JP",
            "sector": sector,
            "current": current_price,
            "change": change_abs,
            "change_pct": change_pct,
            "icon": data["icon"],
            "color": data["color"],
        })
    
    return sectors

def get_sector_data_real():
    """Get real sector data for refresh button"""
    sectors = []
    
    # US SPDR sectors - fetch real data from Yahoo Finance
    for sector, data in SPDR_TICKERS.items():
        ticker = data["ticker"]
        price, change, pct = fetch_yahoo_finance_data(ticker)
        
        # Fallback to sample data if real data is unavailable
        if price is None or change is None or pct is None:
            price, change, pct = fetch_price_change(ticker)
        
        sectors.append({
            "group": "US",
            "sector": f"{sector} - {ticker}",
            "current": price,
            "change": change,
            "change_pct": pct,
            "icon": data["icon"],
            "color": data["color"],
        })
    
    # JP TOPIX-17 sectors - fetch real data from JPX
    jpx_data = fetch_jpx_data()
    for sector, data in TOPIX17_SECTORS.items():
        jpx_key = data["jpx_key"]
        
        if jpx_key in jpx_data:
            jpx_sector = jpx_data[jpx_key]
            try:
                current_price = float(jpx_sector["currentPrice"])
                change_abs = float(jpx_sector["previousDayComparison"])
                change_pct = float(jpx_sector["previousDayRatio"])
            except (ValueError, KeyError):
                # Fallback to sample data if real data is unavailable
                base_price = random.uniform(1000, 3000)
                current_price, change_abs, change_pct = generate_sample_data(base_price)
        else:
            # Fallback to sample data if sector not found
            base_price = random.uniform(1000, 3000)
            current_price, change_abs, change_pct = generate_sample_data(base_price)
        
        sectors.append({
            "group": "JP",
            "sector": sector,
            "current": current_price,
            "change": change_abs,
            "change_pct": change_pct,
            "icon": data["icon"],
            "color": data["color"],
        })
    
    return sectors

def get_benchmark_data_sample():
    """Get sample benchmark data for initial page load"""
    benchmarks = []
    for name, data in BENCHMARKS.items():
        price, change, pct = fetch_price_change(data["ticker"])
        benchmarks.append({
            "group": "Benchmark",
            "sector": name,
            "current": price,
            "change": change,
            "change_pct": pct,
            "icon": data["icon"],
            "color": data["color"],
        })
    return benchmarks

def get_benchmark_data_real():
    """Get real benchmark data for refresh button"""
    benchmarks = []
    for name, data in BENCHMARKS.items():
        ticker = data["ticker"]
        price, change, pct = fetch_yahoo_finance_data(ticker)
        
        # Fallback to sample data if real data is unavailable
        if price is None or change is None or pct is None:
            price, change, pct = fetch_price_change(ticker)
        
        benchmarks.append({
            "group": "Benchmark",
            "sector": name,
            "current": price,
            "change": change,
            "change_pct": pct,
            "icon": data["icon"],
            "color": data["color"],
        })
    return benchmarks

def calculate_summary(sectors):
    """Calculate summary metrics"""
    positive_count = len([s for s in sectors if s["change"] > 0])
    negative_count = len([s for s in sectors if s["change"] < 0])
    total_count = len(sectors)
    avg_change = sum(s["change_pct"] for s in sectors) / total_count if sectors else 0
    
    return {
        "positive_count": positive_count,
        "negative_count": negative_count,
        "total_count": total_count,
        "avg_change": avg_change,
    }

def get_cached_data():
    """Get cached sector and benchmark data"""
    cached_sectors = cache.get('sectors_data')
    cached_benchmarks = cache.get('benchmarks_data')
    cached_time = cache.get('data_update_time')
    
    if cached_sectors and cached_benchmarks and cached_time:
        return cached_sectors, cached_benchmarks, cached_time
    
    # If no cache, return sample data
    sectors = get_sector_data_sample()
    benchmarks = get_benchmark_data_sample()
    update_time = datetime.now(TZ_JST).strftime("%Y年%m月%d日 %H:%M:%S")
    
    return sectors, benchmarks, update_time

def cache_data(sectors, benchmarks, update_time):
    """Cache sector and benchmark data"""
    # Cache for 24 hours (86400 seconds)
    cache.set('sectors_data', sectors, 86400)
    cache.set('benchmarks_data', benchmarks, 86400)
    cache.set('data_update_time', update_time, 86400)

@csrf_exempt
def index(request):
    if request.method == 'POST':
        # Handle AJAX refresh request
        try:
            data = json.loads(request.body)
            if data.get('action') == 'refresh':
                sectors = get_sector_data_real()
                benchmarks = get_benchmark_data_real()
                update_time = datetime.now(TZ_JST).strftime("%Y年%m月%d日 %H:%M:%S")
                
                # Cache the new data
                cache_data(sectors, benchmarks, update_time)
                
                # Separate US and JP sectors
                us_sectors = [s for s in sectors if s["group"] == "US"]
                jp_sectors = [s for s in sectors if s["group"] == "JP"]
                
                # Sort by change percentage (descending)
                us_sectors.sort(key=lambda x: x["change_pct"], reverse=True)
                jp_sectors.sort(key=lambda x: x["change_pct"], reverse=True)
                benchmarks.sort(key=lambda x: x["change_pct"], reverse=True)
                
                # Calculate separate summaries
                us_summary = calculate_summary(us_sectors)
                jp_summary = calculate_summary(jp_sectors)
                
                return JsonResponse({
                    'success': True,
                    'update_time': update_time,
                    'us_summary': us_summary,
                    'jp_summary': jp_summary,
                    'sectors': sectors,
                    'benchmarks': benchmarks
                })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    # GET request - render the main page with cached data
    sectors, benchmarks, cached_update_time = get_cached_data()
    
    # Separate US and JP sectors
    us_sectors = [s for s in sectors if s["group"] == "US"]
    jp_sectors = [s for s in sectors if s["group"] == "JP"]
    
    # Calculate separate summaries for US and JP
    us_summary = calculate_summary(us_sectors)
    jp_summary = calculate_summary(jp_sectors)
    
    # Sort by change percentage (descending)
    us_sectors.sort(key=lambda x: x["change_pct"], reverse=True)
    jp_sectors.sort(key=lambda x: x["change_pct"], reverse=True)
    benchmarks.sort(key=lambda x: x["change_pct"], reverse=True)
    
    context = {
        'update_time': cached_update_time,
        'us_summary': us_summary,
        'jp_summary': jp_summary,
        'benchmarks': benchmarks,
        'us_sectors': us_sectors,
        'jp_sectors': jp_sectors,
    }
    
    return render(request, 'target/index.html', context)