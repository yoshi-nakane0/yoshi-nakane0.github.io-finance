# sector/views.py
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.core.cache import cache
from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from datetime import datetime, timezone, timedelta
from .models import SectorSnapshot
import json
import requests

logger = logging.getLogger(__name__)

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
    except Exception:
        logger.exception("Failed to fetch JPX data")
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
    except Exception:
        logger.exception("Failed to fetch Yahoo Finance data for %s", ticker)
        return None, None, None

def get_sector_data_real(fallback_sectors=None):
    """Get real sector data for refresh button"""
    sectors = []
    fallback_sectors = fallback_sectors or []
    
    # US SPDR sectors - fetch real data from Yahoo Finance
    for sector, data in SPDR_TICKERS.items():
        ticker = data["ticker"]
        price, change, pct = fetch_yahoo_finance_data(ticker)
        
        # Fallback: try to get persisted data first
        if price is None or change is None or pct is None:
            cached_sector = next((s for s in fallback_sectors if s.get('sector') == f"{sector} - {ticker}"), None)
            if cached_sector:
                price = cached_sector['current']
                change = cached_sector['change']
                pct = cached_sector['change_pct']
            else:
                continue
        
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
        current_price = None
        change_abs = None
        change_pct = None
        if jpx_key in jpx_data:
            jpx_sector = jpx_data[jpx_key]
            try:
                current_price = float(jpx_sector["currentPrice"])
                change_abs = float(jpx_sector["previousDayComparison"])
                change_pct = float(jpx_sector["previousDayRatio"])
            except (ValueError, KeyError):
                pass

        if current_price is None or change_abs is None or change_pct is None:
            # Fallback: try to get persisted data first
            cached_sector = next((s for s in fallback_sectors if s.get('sector') == sector and s.get('group') == 'JP'), None)
            if cached_sector:
                current_price = cached_sector['current']
                change_abs = cached_sector['change']
                change_pct = cached_sector['change_pct']
            else:
                continue
        
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

def get_benchmark_data_real(fallback_benchmarks=None):
    """Get real benchmark data for refresh button"""
    benchmarks = []
    fallback_benchmarks = fallback_benchmarks or []
    for name, data in BENCHMARKS.items():
        ticker = data["ticker"]
        price, change, pct = fetch_yahoo_finance_data(ticker)
        
        # Fallback: try to get persisted data first
        if price is None or change is None or pct is None:
            cached_benchmark = next((b for b in fallback_benchmarks if b.get('sector') == name), None)
            if cached_benchmark:
                price = cached_benchmark['current']
                change = cached_benchmark['change']
                pct = cached_benchmark['change_pct']
            else:
                continue
        
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

def get_persisted_snapshot():
    """Get persisted sector and benchmark data"""
    try:
        snapshot = SectorSnapshot.objects.filter(pk=1).first()
    except (OperationalError, ProgrammingError, DatabaseError):
        logger.exception("[sector] DB unavailable, skip snapshot")
        return None, None, None
    if snapshot:
        return snapshot.sectors, snapshot.benchmarks, snapshot.update_time
    return None, None, None

def get_fallback_data():
    """Get cached or persisted data for fallback"""
    cached_sectors = cache.get('sectors_data')
    cached_benchmarks = cache.get('benchmarks_data')
    
    if cached_sectors and cached_benchmarks:
        return cached_sectors, cached_benchmarks
    
    persisted_sectors, persisted_benchmarks, _ = get_persisted_snapshot()
    if persisted_sectors is not None and persisted_benchmarks is not None:
        return persisted_sectors, persisted_benchmarks
    
    return [], []

def get_cached_data():
    """Get cached or persisted sector and benchmark data for initial load"""
    cached_sectors = cache.get('sectors_data')
    cached_benchmarks = cache.get('benchmarks_data')
    cached_time = cache.get('data_update_time')
    
    if cached_sectors and cached_benchmarks and cached_time:
        return cached_sectors, cached_benchmarks, cached_time

    persisted_sectors, persisted_benchmarks, persisted_time = get_persisted_snapshot()
    if persisted_sectors is not None and persisted_benchmarks is not None and persisted_time:
        cache_data(persisted_sectors, persisted_benchmarks, persisted_time)
        return persisted_sectors, persisted_benchmarks, persisted_time
    
    sectors = []
    benchmarks = []
    
    update_time = "データを取得するには更新ボタンを押してください"
    
    return sectors, benchmarks, update_time

def cache_data(sectors, benchmarks, update_time):
    """Cache sector and benchmark data"""
    # Cache for 24 hours (86400 seconds)
    cache.set('sectors_data', sectors, 86400)
    cache.set('benchmarks_data', benchmarks, 86400)
    cache.set('data_update_time', update_time, 86400)

def persist_snapshot(sectors, benchmarks, update_time):
    """Persist sector and benchmark data"""
    try:
        SectorSnapshot.objects.update_or_create(
            pk=1,
            defaults={
                "sectors": sectors,
                "benchmarks": benchmarks,
                "update_time": update_time,
            },
        )
    except (OperationalError, ProgrammingError, DatabaseError):
        logger.exception("[sector] DB write failed, skip snapshot")

@ensure_csrf_cookie
def index(request):
    if request.method == 'POST':
        if not request.user.is_authenticated or not request.user.is_staff:
            return JsonResponse({'success': False, 'error': '権限がありません。'}, status=403)

        try:
            data = json.loads(request.body)
            if data.get('action') == 'refresh':
                fallback_sectors, fallback_benchmarks = get_fallback_data()
                sectors = get_sector_data_real(fallback_sectors=fallback_sectors)
                benchmarks = get_benchmark_data_real(fallback_benchmarks=fallback_benchmarks)
                update_time = datetime.now(TZ_JST).strftime("%Y年%m月%d日 %H:%M:%S")
                
                # Cache the new data
                cache_data(sectors, benchmarks, update_time)
                persist_snapshot(sectors, benchmarks, update_time)
                
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
            return JsonResponse({'success': False, 'error': '不正な操作です。'}, status=400)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'JSON が不正です。'}, status=400)
        except Exception:
            logger.exception("[sector] refresh failed")
            return JsonResponse({'success': False, 'error': '更新に失敗しました。'}, status=500)
    
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
        'can_refresh_sector_data': request.user.is_authenticated and request.user.is_staff,
    }
    
    return render(request, 'sector/index.html', context)
