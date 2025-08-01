# control/views.py
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from datetime import datetime, timedelta
import json




def fetch_free_fed_monitor_data():
    """Fed Rate Monitor Tool データを取得"""
    try:
        print("Fetching Fed Monitor data...")
        
        # 標準の0%データを使用
        print("Using default data...")
        
        # すべてのFOMC会合日程のデータを取得
        all_fed_data = get_fed_monitor_data()
        
        print(f"Successfully generated Fed Monitor data for {len(all_fed_data)} meetings")
        return all_fed_data
        
    except Exception as e:
        print(f"Failed to fetch Fed Monitor data: {e}")
        # 例外時も標準の0%データを使用
        return get_fed_monitor_data()




def get_fed_monitor_fallback_data(meeting_date):
    """Fed Rate Monitor Tool用のフォールバックデータ"""
    fed_data = get_fed_monitor_data()
    return fed_data.get(meeting_date, {
        'probabilities': [
            {'range': '4.00-4.25%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
            {'range': '4.50-4.75%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
        ]
    })

def get_cached_fed_monitor_data():
    """キャッシュされたFed Monitor データを取得"""
    cached_data = cache.get('fed_monitor_data')
    cached_time = cache.get('fed_monitor_update_time')
    
    if cached_data and cached_time:
        return cached_data, cached_time
    
    # キャッシュがない場合は実データを取得
    fed_monitor_data = fetch_free_fed_monitor_data()
    update_time = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    
    # 1時間キャッシュ
    cache.set('fed_monitor_data', fed_monitor_data, 3600)
    cache.set('fed_monitor_update_time', update_time, 3600)
    
    return fed_monitor_data, update_time

def get_fed_monitor_data_static():
    """静的なFed Rate Monitor Tool データ（元のデータ）"""
    return get_fed_monitor_data()

def get_fed_monitor_data():
    """Fed Rate Monitor Tool データ（デフォルト0.0%データ）"""
    meeting_dates = [
        "2025-09-17", "2025-10-29", "2025-12-10", "2026-01-28",
        "2026-03-18", "2026-04-29", "2026-06-17"
    ]
    
    # 標準的な金利レンジを定義（0.25%刻み）
    standard_ranges = [
        "3.25-3.50%", "3.50-3.75%", "3.75-4.00%",
        "4.00-4.25%", "4.25-4.50%"
    ]
    
    data = {}
    for meeting_date in meeting_dates:
        probabilities = []
        for range_str in standard_ranges:
            probabilities.append({
                'range': range_str,
                'current': '0.0%',
                'oneDay': '0.0%',
                'oneWeek': '0.0%',
                'type': 'negative'
            })
        
        data[meeting_date] = {
            'probabilities': probabilities
        }
    
    return data


@csrf_exempt
def index(request):
    if request.method == 'POST':
        # AJAX更新リクエストの処理
        try:
            print("POST request received")
            data = json.loads(request.body)
            print(f"Request data: {data}")
            
            if data.get('action') == 'refresh':
                print("Processing refresh action")
                # キャッシュをクリアして新しいデータを取得
                cache.delete('fed_monitor_data')
                cache.delete('fed_monitor_update_time')
                print("Cache cleared")
                
                fed_monitor_data, update_time = get_cached_fed_monitor_data()
                print(f"Got fed_monitor_data: {len(fed_monitor_data) if fed_monitor_data else 0} items")
                
                return JsonResponse({
                    'success': True,
                    'fed_monitor_data': fed_monitor_data,
                    'update_time': update_time
                })
        except json.JSONDecodeError as e:
            print(f"JSON decode error: {e}")
            return JsonResponse({'success': False, 'error': f'JSON decode error: {str(e)}'})
        except Exception as e:
            print(f"General error in POST handler: {e}")
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)})
    
    # GET リクエスト - メインページの表示
    fed_monitor_data, update_time = get_cached_fed_monitor_data()
    
    # デバッグ用出力
    print(f"Fed Monitor Data keys: {list(fed_monitor_data.keys()) if fed_monitor_data else 'None'}")
    
    context = {
        'fed_monitor_data': json.dumps(fed_monitor_data),
        'update_time': update_time,
    }
    return render(request, 'control/index.html', context)