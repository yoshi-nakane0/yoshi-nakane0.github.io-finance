from django.shortcuts import render
from django.conf import settings
from django.core.cache import cache
import os
import csv
from datetime import datetime, timezone, timedelta, date

def fetch_earnings_from_csv():
    """
    static/earning/data/data.csvから決算データを読み込む
    """
    try:
        # CSVファイルのパスを構築
        csv_path = os.path.join(settings.BASE_DIR, 'static', 'earning', 'data', 'data.csv')
        
        if not os.path.exists(csv_path):
            print(f"CSV file not found at {csv_path}")
            return []
        
        earnings_data = []
        
        with open(csv_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            
            for row in reader:
                try:
                    # CSVの各行をパース
                    date_str = row.get('date', '').strip()
                    market = row.get('market', '').strip()
                    symbol = row.get('symbol', '').strip()
                    company = row.get('company', '').strip()
                    industry = row.get('industry', '').strip()
                    
                    # 必須フィールドのチェック
                    if not all([date_str, symbol, company]):
                        # print(f"Skipping incomplete row: {row}")
                        continue
                    
                    # 日付の形式をチェック
                    earnings_date = '決算日未定'
                    if date_str and date_str != '決算日未定':
                        try:
                            # 日付フォーマットの検証
                            # parsed_date = datetime.strptime(date_str, '%Y-%m-%d') # unused variable
                            datetime.strptime(date_str, '%Y-%m-%d')
                            earnings_date = date_str
                        except ValueError:
                            print(f"Invalid date format for {symbol}: {date_str}")
                            earnings_date = '決算日未定'
                    
                    earnings_data.append({
                        'date': earnings_date,
                        'company': company,
                        'industry': industry,
                        'market': market,
                        'symbol': symbol
                    })
                    
                except Exception as e:
                    print(f"Error parsing CSV row {row}: {e}")
                    continue
        
        print(f"CSV: Successfully loaded {len(earnings_data)} earnings announcements")
        return earnings_data
        
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        return []

def index(request):
    """
    決算カレンダーページの表示
    """
    # キャッシュキー
    cache_key = 'earnings_data_grouped'
    # キャッシュからデータを取得
    grouped_earnings = cache.get(cache_key)

    if grouped_earnings is None:
        # キャッシュがない場合はデータを生成
        # CSVファイルから決算データを読み込み
        earnings_data = fetch_earnings_from_csv()
        
        # 本日の日付を取得
        today = date.today()
        
        # 今日以降の決算イベントのみをフィルタリング
        future_earnings = []
        for item in earnings_data:
            if item['date'] == '決算日未定':
                future_earnings.append(item)
            else:
                try:
                    earnings_date = datetime.strptime(item['date'], '%Y-%m-%d').date()
                    if earnings_date >= today:
                        future_earnings.append(item)
                except ValueError:
                    # 日付形式が正しくない場合はスキップ
                    continue
        
        # 日付順にソート（決算日未定は最後に配置）
        try:
            def sort_key(x):
                if x['date'] == '決算日未定':
                    return datetime.max
                try:
                    return datetime.strptime(x['date'], '%Y-%m-%d')
                except:
                    return datetime.max
            future_earnings.sort(key=sort_key)
        except Exception as e:
            print(f"Error sorting data: {e}")
        
        # 日付でグループ化
        grouped_earnings = []
        if future_earnings:
            try:
                current_date = None
                current_companies = []
                
                for item in future_earnings:
                    if current_date != item['date']:
                        if current_date is not None:
                            grouped_earnings.append({
                                'date': current_date,
                                'companies': current_companies
                            })
                        current_date = item['date']
                        current_companies = []
                    current_companies.append(item)
                
                # 最後のグループを追加
                if current_date is not None:
                    grouped_earnings.append({
                        'date': current_date,
                        'companies': current_companies
                    })
            except Exception as e:
                print(f"Error grouping data: {e}")
                # グループ化に失敗した場合は、各項目を個別の日付グループとして扱う
                for item in future_earnings:
                    grouped_earnings.append({
                        'date': item['date'],
                        'companies': [item]
                    })
        
        # 結果をキャッシュに保存（有効期限: 1日 = 86400秒）
        cache.set(cache_key, grouped_earnings, 86400)
    
    context = {
        'earnings_data': grouped_earnings,
    }
    
    return render(request, 'earning/index.html', context)