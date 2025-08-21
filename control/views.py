# control/views.py
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
import json
import csv
import os
from datetime import datetime
import pytz

def load_fed_data():
    """fed.csvからデータを読み込む（シンプル版）"""
    csv_path = os.path.join(settings.BASE_DIR, 'control', 'static', 'control', 'data', 'fed.csv')
    
    print(f"Looking for CSV at: {csv_path}")
    print(f"File exists: {os.path.exists(csv_path)}")
    
    if not os.path.exists(csv_path):
        print("CSV file not found, using fallback data")
        return get_fallback_data()
    
    fed_data = {}
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            row_count = 0
            
            for row in reader:
                row_count += 1
                meeting_date = row.get('meeting_date', '').strip()
                target_rate = row.get('target_rate', '').strip()
                current_prob = row.get('current_probability_pct', '—').strip()
                prev_day_prob = row.get('prev_day_probability_pct', '—').strip()
                prev_week_prob = row.get('prev_week_probability_pct', '—').strip()
                
                if not meeting_date or not target_rate:
                    continue
                
                if meeting_date not in fed_data:
                    fed_data[meeting_date] = []
                
                # 確率の種類を判定
                prob_type = 'negative'
                if current_prob != '—' and '%' in current_prob:
                    try:
                        current_val = float(current_prob.replace('%', ''))
                        if current_val > 50:
                            prob_type = 'positive'
                        elif current_val > 10:
                            prob_type = 'neutral'
                    except:
                        pass
                
                fed_data[meeting_date].append({
                    'range': target_rate,
                    'current': current_prob,
                    'oneDay': prev_day_prob,
                    'oneWeek': prev_week_prob,
                    'type': prob_type
                })
            
            print(f"Loaded {row_count} rows from CSV")
            print(f"Processed {len(fed_data)} meeting dates")
            
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return get_fallback_data()
    
    return fed_data

@csrf_exempt
def index(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            if data.get('action') == 'refresh':
                fed_data = load_fed_data()
                return JsonResponse({
                    'success': True,
                    'fed_data': fed_data,
                    'update_time': '更新完了'
                })
        except Exception as e:
            print(f"POST error: {e}")
            return JsonResponse({'success': False, 'error': str(e)})
    
    # GET request - ページ表示
    fed_data = load_fed_data()
    
    print(f"Sending to template: {len(fed_data)} meetings")
    for date, probs in fed_data.items():
        print(f"  {date}: {len(probs)} probabilities")
    
    # 現在の日時を取得（日本時間）
    japan_tz = pytz.timezone('Asia/Tokyo')
    now = datetime.now(japan_tz)
    update_time = now.strftime('%Y-%m-%d %H:%M:%S')
    
    context = {
        'fed_data': fed_data,
        'fed_data_json': json.dumps(fed_data, ensure_ascii=False),
        'update_time': update_time
    }
    
    return render(request, 'control/index.html', context)