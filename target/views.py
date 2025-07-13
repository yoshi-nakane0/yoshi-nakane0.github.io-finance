# target/views.py
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timezone, timedelta
import json
import random

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
    "食品": {"icon": "🍽️", "color": "#FF6B6B"},
    "エネルギー資源": {"icon": "⛽", "color": "#4ECDC4"},
    "建設・資材": {"icon": "🏗️", "color": "#45B7D1"},
    "原材料・化学": {"icon": "🧪", "color": "#96CEB4"},
    "医薬品": {"icon": "💊", "color": "#FFEAA7"},
    "自動車・輸送機": {"icon": "🚗", "color": "#DDA0DD"},
    "鉄鋼・非鉄": {"icon": "🔩", "color": "#98D8C8"},
    "機械": {"icon": "⚙️", "color": "#F7DC6F"},
    "電機・精密": {"icon": "📱", "color": "#BB8FCE"},
    "情報通信・サービスその他": {"icon": "💻", "color": "#85C1E9"},
    "電力・ガス": {"icon": "⚡", "color": "#82E0AA"},
    "運輸・物流": {"icon": "🚛", "color": "#F8C471"},
    "商社・卸売": {"icon": "🏪", "color": "#F1948A"},
    "小売": {"icon": "🛒", "color": "#85C1E9"},
    "銀行": {"icon": "🏦", "color": "#BB8FCE"},
    "証券・商品先物": {"icon": "📈", "color": "#98D8C8"},
    "保険": {"icon": "🛡️", "color": "#F7DC6F"},
    "不動産": {"icon": "🏠", "color": "#82E0AA"},
    "サービス": {"icon": "🤝", "color": "#DDA0DD"},
}

def generate_sample_data(base_price: float, volatility: float = 0.05) -> tuple:
    """Generate sample financial data"""
    change_pct = random.uniform(-volatility * 100, volatility * 100)
    change_abs = base_price * (change_pct / 100)
    current_price = base_price + change_abs
    return current_price, change_abs, change_pct

def fetch_price_change(ticker: str) -> tuple:
    """Fetch price change data (using sample data for now)"""
    if ticker.startswith("^"):
        base_prices = {"^N225": 32000, "^DJI": 35000, "^GSPC": 4500, "^IXIC": 14000}
        base_price = base_prices.get(ticker, 1000)
    else:
        base_price = random.uniform(80, 200)
    
    return generate_sample_data(base_price)

def get_sector_data():
    """Get all sector data"""
    sectors = []
    
    # US SPDR sectors
    for sector, data in SPDR_TICKERS.items():
        price, change, pct = fetch_price_change(data["ticker"])
        sectors.append({
            "group": "US",
            "sector": sector,
            "current": price,
            "change": change,
            "change_pct": pct,
            "icon": data["icon"],
            "color": data["color"],
        })
    
    # JP TOPIX-17 sectors
    for sector, data in TOPIX17_SECTORS.items():
        base_price = random.uniform(1000, 3000)
        price, change, pct = generate_sample_data(base_price)
        sectors.append({
            "group": "JP",
            "sector": sector,
            "current": price,
            "change": change,
            "change_pct": pct,
            "icon": data["icon"],
            "color": data["color"],
        })
    
    return sectors

def get_benchmark_data():
    """Get benchmark data"""
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

@csrf_exempt
def index(request):
    if request.method == 'POST':
        # Handle AJAX refresh request
        try:
            data = json.loads(request.body)
            if data.get('action') == 'refresh':
                sectors = get_sector_data()
                benchmarks = get_benchmark_data()
                
                # Separate US and JP sectors
                us_sectors = [s for s in sectors if s["group"] == "US"]
                jp_sectors = [s for s in sectors if s["group"] == "JP"]
                
                # Calculate separate summaries
                us_summary = calculate_summary(us_sectors)
                jp_summary = calculate_summary(jp_sectors)
                
                return JsonResponse({
                    'success': True,
                    'update_time': datetime.now(TZ_JST).strftime("%Y年%m月%d日 %H:%M:%S"),
                    'us_summary': us_summary,
                    'jp_summary': jp_summary,
                    'sectors': sectors,
                    'benchmarks': benchmarks
                })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    # GET request - render the main page
    sectors = get_sector_data()
    benchmarks = get_benchmark_data()
    
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
        'update_time': datetime.now(TZ_JST).strftime("%Y年%m月%d日 %H:%M:%S"),
        'us_summary': us_summary,
        'jp_summary': jp_summary,
        'benchmarks': benchmarks,
        'us_sectors': us_sectors,
        'jp_sectors': jp_sectors,
    }
    
    return render(request, 'target/index.html', context)