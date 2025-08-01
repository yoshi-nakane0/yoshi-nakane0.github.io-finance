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
        "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-26", "2026-03-18", "2026-04-29", "2026-06-17"
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

def get_fomc_data():
    """FOMC会合日程データ"""
    
    # 生データを定義
    raw_data = {
        '2025-07-30': [
            {'range': '3.25-3.50%', 'current': '12.8%', 'oneWeek': '14.2%', 'oneMonth': '16.7%'},
            {'range': '3.50-3.75%', 'current': '25.1%', 'oneWeek': '26.3%', 'oneMonth': '28.9%'},
            {'range': '3.75-4.00%', 'current': '42.3%', 'oneWeek': '41.1%', 'oneMonth': '37.8%'},
            {'range': '4.00-4.25%', 'current': '12.1%', 'oneWeek': '10.8%', 'oneMonth': '7.2%'},
            {'range': '4.25-4.50%', 'current': '2.5%', 'oneWeek': '1.5%', 'oneMonth': '1.1%'}
        ],
        '2025-09-17': [
            {'range': '3.25-3.50%', 'current': '18.4%', 'oneWeek': '20.2%', 'oneMonth': '23.5%'},
            {'range': '3.50-3.75%', 'current': '35.8%', 'oneWeek': '36.1%', 'oneMonth': '35.2%'},
            {'range': '3.75-4.00%', 'current': '28.1%', 'oneWeek': '26.8%', 'oneMonth': '24.1%'},
            {'range': '4.00-4.25%', 'current': '7.9%', 'oneWeek': '6.5%', 'oneMonth': '4.8%'},
            {'range': '4.25-4.50%', 'current': '1.1%', 'oneWeek': '0.6%', 'oneMonth': '0.3%'}
        ],
        '2025-10-29': [
            {'range': '3.25-3.50%', 'current': '28.9%', 'oneWeek': '30.4%', 'oneMonth': '33.2%'},
            {'range': '3.50-3.75%', 'current': '41.2%', 'oneWeek': '40.1%', 'oneMonth': '37.9%'},
            {'range': '3.75-4.00%', 'current': '15.1%', 'oneWeek': '13.8%', 'oneMonth': '10.7%'},
            {'range': '4.00-4.25%', 'current': '2.3%', 'oneWeek': '1.8%', 'oneMonth': '1.2%'},
            {'range': '4.25-4.50%', 'current': '0.2%', 'oneWeek': '0.2%', 'oneMonth': '0.2%'}
        ],
        '2025-12-10': [
            {'range': '3.25-3.50%', 'current': '35.7%', 'oneWeek': '37.2%', 'oneMonth': '39.8%'},
            {'range': '3.50-3.75%', 'current': '32.4%', 'oneWeek': '31.1%', 'oneMonth': '28.9%'},
            {'range': '3.75-4.00%', 'current': '12.8%', 'oneWeek': '11.2%', 'oneMonth': '8.1%'},
            {'range': '4.00-4.25%', 'current': '2.1%', 'oneWeek': '1.8%', 'oneMonth': '1.0%'},
            {'range': '4.25-4.50%', 'current': '0.2%', 'oneWeek': '0.2%', 'oneMonth': '0.1%'}
        ],
        '2026-01-26': [
            {'range': '3.25-3.50%', 'current': '47.8%', 'oneWeek': '46.2%', 'oneMonth': '43.1%'},
            {'range': '3.50-3.75%', 'current': '25.1%', 'oneWeek': '25.8%', 'oneMonth': '25.2%'},
            {'range': '3.75-4.00%', 'current': '4.3%', 'oneWeek': '3.6%', 'oneMonth': '3.6%'},
            {'range': '4.00-4.25%', 'current': '0.4%', 'oneWeek': '0.3%', 'oneMonth': '0.3%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '2026-03-18': [
            {'range': '3.25-3.50%', 'current': '42.1%', 'oneWeek': '40.8%', 'oneMonth': '38.9%'},
            {'range': '3.50-3.75%', 'current': '17.3%', 'oneWeek': '17.9%', 'oneMonth': '18.1%'},
            {'range': '3.75-4.00%', 'current': '1.8%', 'oneWeek': '1.8%', 'oneMonth': '1.7%'},
            {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '2026-04-29': [
            {'range': '3.25-3.50%', 'current': '35.4%', 'oneWeek': '36.1%', 'oneMonth': '37.8%'},
            {'range': '3.50-3.75%', 'current': '12.1%', 'oneWeek': '11.8%', 'oneMonth': '11.9%'},
            {'range': '3.75-4.00%', 'current': '1.2%', 'oneWeek': '1.2%', 'oneMonth': '1.1%'},
            {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '2026-06-17': [
            {'range': '3.25-3.50%', 'current': '28.7%', 'oneWeek': '29.8%', 'oneMonth': '32.1%'},
            {'range': '3.50-3.75%', 'current': '6.9%', 'oneWeek': '6.7%', 'oneMonth': '6.9%'},
            {'range': '3.75-4.00%', 'current': '0.5%', 'oneWeek': '0.5%', 'oneMonth': '0.5%'},
            {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '0000-00-00': [
            {'range': '3.25-3.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.50-3.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.75-4.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.00-4.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ]
    }
    
    # 各会合日程のデータを処理
    processed_data = {}
    for date, probs in raw_data.items():
        processed_probs = []
        for prob in probs:
            current_val = float(prob['current'].replace('%', ''))
            week_val = float(prob['oneWeek'].replace('%', ''))
            
            processed_probs.append({
                'range': prob['range'],
                'current': prob['current'],
                'oneWeek': prob['oneWeek'],
                'oneMonth': prob['oneMonth'],
                'type': 'positive' if current_val > week_val else 'negative'
            })
        
        processed_data[date] = {'probabilities': processed_probs}
    
    return processed_data

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
                    'fomc_data': get_fomc_data(),
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
    if fed_monitor_data and '2025-07-30' in fed_monitor_data:
        print(f"Sample data for 2025-07-30: {fed_monitor_data['2025-07-30']}")
    
    context = {
        'fed_monitor_data': json.dumps(fed_monitor_data),
        'fomc_data': json.dumps(get_fomc_data()),
        'update_time': update_time,
    }
    return render(request, 'control/index.html', context)