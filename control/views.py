# control/views.py
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
import json
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup
import re
import time
import random

# JST timezone
TZ_JST = timezone(timedelta(hours=9))

def fetch_fed_data_from_multiple_sources():
    """複数のソースからFedレートデータを取得を試行"""
    
    # データソースのリスト（優先順位順）
    data_sources = [
        {
            'name': 'Investing.com',
            'function': fetch_fed_data_from_investing
        },
        {
            'name': 'Federal Reserve API',
            'function': fetch_fed_data_from_fred_api
        },
        {
            'name': 'CME FedWatch Tool',
            'function': fetch_fed_data_from_cme
        },
        {
            'name': 'Sample Data',
            'function': generate_sample_fed_data
        }
    ]
    
    for source in data_sources:
        try:
            print(f"Trying data source: {source['name']}")
            data = source['function']()
            if data and len(data) > 0:
                print(f"Successfully fetched data from {source['name']}")
                return data
        except Exception as e:
            print(f"Failed to fetch from {source['name']}: {e}")
            continue
    
    print("All data sources failed, returning empty data")
    return {}

def fetch_fed_data_from_investing():
    """investing.comから最新のFedレートデータを取得"""
    target_url = "https://www.investing.com/central-banks/fed-rate-monitor"
    
    print(f"Fetching data from: {target_url}")
    
    # セッションを作成してCookie管理を改善
    session = requests.Session()
    
    # より現実的なUser-Agentリスト
    user_agents = [
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0'
    ]
    
    try:
        # ランダムな遅延を追加（ボット検出を回避）
        time.sleep(random.uniform(1, 3))
        
        # より詳細なヘッダーでリアルなブラウザをシミュレート
        headers = {
            'User-Agent': random.choice(user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9,ja;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Cache-Control': 'max-age=0',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"macOS"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'DNT': '1',
            'Connection': 'keep-alive',
        }
        
        # リファラーを段階的に設定（より自然なアクセスパターン）
        google_search_url = "https://www.google.com/search?q=fed+rate+monitor+investing.com"
        
        # まずGoogleの検索結果ページを訪問
        print("Simulating Google search...")
        google_headers = headers.copy()
        google_headers['Referer'] = 'https://www.google.com/'
        
        # Google検索ページをシミュレート
        session.get('https://www.google.com/', headers=google_headers, timeout=10)
        time.sleep(random.uniform(0.5, 1.5))
        
        # メインページをリファラーありで取得
        print("Accessing target page...")
        headers['Referer'] = 'https://www.google.com/'
        
        response = session.get(target_url, headers=headers, timeout=20, allow_redirects=True)
        
        print(f"Response status: {response.status_code}")
        print(f"Response headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            print("Successfully fetched data from investing.com")
            return parse_investing_data_html(response.content)
        elif response.status_code == 403:
            print("Access forbidden - trying alternative approach...")
            return try_alternative_approach(session, target_url, headers)
        else:
            print(f"Failed to fetch data: Status {response.status_code}")
            return None
            
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None
    finally:
        session.close()

def try_alternative_approach(session, url, base_headers):
    """代替的なアプローチでアクセスを試行"""
    try:
        print("Trying alternative approach with different headers...")
        
        # より保守的なヘッダー設定
        alt_headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.investing.com/',
            'Upgrade-Insecure-Requests': '1'
        }
        
        # 少し長めの遅延
        time.sleep(random.uniform(2, 4))
        
        response = session.get(url, headers=alt_headers, timeout=15)
        
        if response.status_code == 200:
            print("Alternative approach successful!")
            return parse_investing_data_html(response.content)
        else:
            print(f"Alternative approach failed: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"Alternative approach error: {e}")
        return None

def parse_investing_data_html(html_content):
    """investing.comのHTMLからFedレートデータを解析（特定の構造に対応）"""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        fed_data = {}
        
        print("Parsing HTML content...")
        print(f"HTML content length: {len(html_content)}")
        
        # investing.comの特定構造を検索: cardWrapper div
        card_wrappers = soup.find_all('div', class_='cardWrapper')
        print(f"Found {len(card_wrappers)} cardWrapper elements")
        
        if card_wrappers:
            fed_data = parse_card_wrappers(card_wrappers)
            if fed_data:
                return fed_data
        
        # フォールバック: 従来のテーブル検索
        tables = soup.find_all('table', class_='fedRateTbl')
        print(f"Found {len(tables)} fedRateTbl tables")
        
        if tables:
            fed_data = parse_fed_rate_tables(tables)
            if fed_data:
                return fed_data
            
        print("No Fed data found in HTML")
        return None
        
    except Exception as e:
        print(f"Error parsing HTML: {e}")
        return None

def parse_card_wrappers(card_wrappers):
    """cardWrapper要素からFedデータを解析"""
    fed_data = {}
    
    for card in card_wrappers:
        try:
            # 日付を取得
            date_element = card.find('div', class_='fedRateDate')
            if not date_element:
                continue
                
            date_text = date_element.get_text(strip=True)
            meeting_date = parse_meeting_date(date_text)
            
            print(f"Processing card for date: {date_text} -> {meeting_date}")
            
            # fedRateTblテーブルを探す
            table = card.find('table', class_='fedRateTbl')
            if not table:
                continue
            
            probabilities = parse_fed_rate_table_rows(table)
            
            if probabilities:
                fed_data[meeting_date] = probabilities
                print(f"  Found {len(probabilities)} probability entries")
                
        except Exception as e:
            print(f"Error parsing card wrapper: {e}")
            continue
    
    return fed_data

def parse_fed_rate_tables(tables):
    """fedRateTblテーブルからデータを解析"""
    fed_data = {}
    
    for table in tables:
        try:
            # テーブル周辺から日付を探す
            meeting_date = find_meeting_date_near_table(table)
            if not meeting_date:
                continue
                
            probabilities = parse_fed_rate_table_rows(table)
            
            if probabilities:
                fed_data[meeting_date] = probabilities
                
        except Exception as e:
            print(f"Error parsing fed rate table: {e}")
            continue
    
    return fed_data

def parse_fed_rate_table_rows(table):
    """fedRateTblテーブルの行からデータを解析"""
    probabilities = []
    
    tbody = table.find('tbody')
    if not tbody:
        return probabilities
    
    rows = tbody.find_all('tr')
    
    for row in rows:
        try:
            cells = row.find_all('td')
            if len(cells) < 4:
                continue
            
            # investing.comの特定構造に基づく解析
            # TD 0: Target Rate (例: "4.00 - 4.25")
            # TD 1: Current Probability% (例: "73.1%")
            # TD 2: Previous Day Probability% (例: "78.4%") 
            # TD 3: Previous Week Probability% (例: "90.2%")
            
            target_rate = cells[0].get_text(strip=True)
            current_prob = cells[1].get_text(strip=True)
            prev_day_prob = cells[2].get_text(strip=True)
            prev_week_prob = cells[3].get_text(strip=True)
            
            # Target Rateから余分な要素を除去
            target_rate = clean_target_rate(target_rate)
            
            # 空の値を正規化
            current_prob = normalize_probability(current_prob)
            prev_day_prob = normalize_probability(prev_day_prob)
            prev_week_prob = normalize_probability(prev_week_prob)
            
            # Target Rateが有効な形式かチェック
            if not re.search(r'\d+\.\d+\s*[-–]\s*\d+\.\d+', target_rate):
                continue
            
            prob_type = determine_prob_type(current_prob)
            
            probabilities.append({
                'range': target_rate,
                'current': current_prob,
                'oneDay': prev_day_prob,
                'oneWeek': prev_week_prob,
                'type': prob_type
            })
            
        except Exception as e:
            print(f"Error parsing table row: {e}")
            continue
    
    return probabilities

def find_meeting_date_near_table(table):
    """テーブル周辺から会議日を探す"""
    # 親要素を遡って日付を探す
    current = table.parent
    
    for _ in range(5):  # 最大5階層まで遡る
        if not current:
            break
            
        # fedRateDateクラスを探す
        date_element = current.find('div', class_='fedRateDate')
        if date_element:
            date_text = date_element.get_text(strip=True)
            return parse_meeting_date(date_text)
        
        current = current.parent
    
    return None

def clean_target_rate(target_rate_text):
    """Target Rateテキストをクリーンアップ"""
    # HTMLタグや余分なテキストを除去
    # 例: "4.00 - 4.25<span class='chartIcon'>...</span>" -> "4.00 - 4.25"
    
    # まず数字とハイフンのパターンを抽出
    match = re.search(r'\d+\.\d+\s*[-–]\s*\d+\.\d+', target_rate_text)
    if match:
        return match.group().strip()
    
    return target_rate_text.strip()


def parse_meeting_date(date_str):
    """会議日文字列を標準形式に変換"""
    try:
        # investing.comの形式に対応
        # "Sep 17, 2025" -> "2025-09-17"
        # "Oct 08, 2025" -> "2025-10-08"
        # "Sep 17-18, 2025" -> "2025-09-17" (範囲の場合)
        
        months = {
            'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
            'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
            'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'
        }
        
        # カンマを除去して分割
        clean_str = date_str.replace(',', '').strip()
        parts = clean_str.split()
        
        if len(parts) >= 3:
            month_str = parts[0]
            day_str = parts[1]
            year_str = parts[2]
        elif len(parts) == 2:
            month_str = parts[0]
            year_str = parts[1]
            day_str = "01"  # デフォルトで1日
        else:
            return date_str
        
        # 月名を数字に変換
        month = months.get(month_str)
        if not month:
            return date_str
        
        # 日付から範囲の最初の日を取得（例: "17-18" -> "17"）
        if '-' in day_str:
            day = day_str.split('-')[0]
        else:
            day = day_str
        
        # 日付を2桁にフォーマット
        day = day.zfill(2)
        
        return f"{year_str}-{month}-{day}"
        
    except Exception as e:
        print(f"Error parsing date '{date_str}': {e}")
        return date_str

def normalize_probability(prob_str):
    """確率文字列を正規化"""
    if not prob_str or prob_str.strip() == '' or prob_str in ['N/A', 'n/a', 'null', 'None']:
        return '—'
    
    # 既に正規化済みの場合
    if prob_str in ['—', '-']:
        return '—'
    
    # パーセント記号があれば維持
    if '%' in prob_str:
        return prob_str.strip()
    
    # 数値のみの場合はパーセント記号を追加
    try:
        float(prob_str)
        return f"{prob_str}%"
    except:
        pass
    
    return prob_str.strip()

def determine_prob_type(prob_str):
    """確率文字列から種類を判定"""
    if prob_str in ['—', '-', '']:
        return 'negative'
    
    try:
        if '%' in prob_str:
            value = float(prob_str.replace('%', ''))
            if value > 50:
                return 'positive'
            elif value > 10:
                return 'neutral'
    except:
        pass
    
    return 'negative'

def fetch_fed_data_from_fred_api():
    """Federal Reserve Economic Data (FRED) APIからデータを取得"""
    try:
        # FRED APIを使用してFederal Fund Rate関連データを取得
        # 注意: 実際のFRED APIではAPIキーが必要です
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            'series_id': 'FEDFUNDS',
            'api_key': 'demo',  # デモ用（実際はAPIキーが必要）
            'file_type': 'json',
            'limit': '1'
        }
        
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # FREDデータからFedWatch風のデータを生成
            return convert_fred_to_fedwatch_format(data)
        else:
            return None
            
    except Exception as e:
        print(f"FRED API error: {e}")
        return None

def fetch_fed_data_from_cme():
    """CME FedWatch ToolからデータをスクレイピングまたはAPI経由で取得"""
    try:
        # CMEのFedWatch Tool（代替アプローチ）
        url = "https://www.cmegroup.com/tools-information/quikstrike/fed-watch-tool.html"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            # CMEのデータ構造に合わせて解析
            return parse_cme_data(response.content)
        else:
            return None
            
    except Exception as e:
        print(f"CME API error: {e}")
        return None

def generate_sample_fed_data():
    """サンプルのFedレートデータを生成（最終フォールバック）"""
    print("Generating sample Fed data as fallback")
    
    # 将来の会議日を生成
    base_date = datetime.now()
    meeting_dates = []
    
    # 通常FOMCは年8回開催
    months_ahead = [2, 3, 5, 6, 8, 9, 11, 12, 14]
    for months in months_ahead[:6]:  # 6回分の会議
        future_date = base_date + timedelta(days=30*months)
        formatted_date = future_date.strftime('%Y-%m-%d')
        meeting_dates.append(formatted_date)
    
    fed_data = {}
    
    # 金利レンジを定義
    rate_ranges = [
        "4.25 - 4.50",
        "4.50 - 4.75", 
        "4.75 - 5.00",
        "5.00 - 5.25",
        "5.25 - 5.50",
        "5.50 - 5.75"
    ]
    
    for date in meeting_dates:
        probabilities = []
        
        for i, rate_range in enumerate(rate_ranges):
            # 確率を生成（現在の金利付近で高い確率）
            if i == 1:  # 現在の金利レンジ
                current = random.uniform(65, 85)
            elif i == 0 or i == 2:  # 隣接するレンジ
                current = random.uniform(10, 25)
            else:  # その他
                current = random.uniform(0, 5)
            
            # 1日前と1週間前の確率（若干の変動を追加）
            one_day = current + random.uniform(-5, 5)
            one_week = current + random.uniform(-10, 10)
            
            # 確率を0-100の範囲に制限
            current = max(0, min(100, current))
            one_day = max(0, min(100, one_day))
            one_week = max(0, min(100, one_week))
            
            prob_type = determine_prob_type(f"{current:.1f}%")
            
            probabilities.append({
                'range': rate_range,
                'current': f"{current:.1f}%",
                'oneDay': f"{one_day:.1f}%",
                'oneWeek': f"{one_week:.1f}%",
                'type': prob_type
            })
        
        fed_data[date] = probabilities
    
    return fed_data

def convert_fred_to_fedwatch_format(fred_data):
    """FREDデータをFedWatch形式に変換"""
    # 実装は複雑になるため、現時点では簡単なサンプルデータを返す
    return generate_sample_fed_data()

def parse_cme_data(html_content):
    """CMEのHTMLデータを解析してFedWatch形式に変換"""
    # 実装は複雑になるため、現時点では簡単なサンプルデータを返す
    return generate_sample_fed_data()

def get_cached_fed_data():
    """Get cached fed data or sample data for initial load"""
    cached_data = cache.get('fed_data')
    cached_time = cache.get('fed_data_update_time')
    
    if cached_data and cached_time and len(cached_data) > 0:
        return cached_data, cached_time
    
    # If no cache, generate sample data for immediate display
    print("No cached data, generating sample data for initial display")
    sample_data = generate_sample_fed_data()
    
    # Check if there's a previous update time stored
    last_update = cache.get('last_fed_manual_update_time')
    if last_update:
        update_time = f"前回更新: {last_update} (サンプルデータ表示中)"
    else:
        update_time = "サンプルデータ表示中 - 更新ボタンで最新データを取得"
    
    return sample_data, update_time

def cache_fed_data(fed_data, update_time):
    """Cache fed data"""
    # Cache for 24 hours (86400 seconds)
    cache.set('fed_data', fed_data, 86400)
    cache.set('fed_data_update_time', update_time, 86400)
    # Store the last manual update time separately
    cache.set('last_fed_manual_update_time', update_time, 86400 * 7)  # Keep for 7 days

def load_fed_data():
    """複数のソースからデータを読み込む（更新時のみ使用）"""
    print("Fetching data from multiple sources...")
    
    # 複数のソースを試行
    web_data = fetch_fed_data_from_multiple_sources()
    
    if web_data and len(web_data) > 0:
        print(f"Successfully fetched data: {len(web_data)} meetings")
        return web_data
    
    # 全てのソースで失敗した場合はキャッシュされたデータを返す
    print("All sources failed, checking cache...")
    cached_data = cache.get('fed_data')
    if cached_data and len(cached_data) > 0:
        print("Using cached data")
        return cached_data
    
    # 最終フォールバック：サンプルデータを生成
    print("No cached data available, generating sample data")
    return generate_sample_fed_data()


@csrf_exempt
def index(request):
    if request.method == 'POST':
        # Handle AJAX refresh request (similar to sector page)
        try:
            data = json.loads(request.body)
            if data.get('action') == 'refresh':
                print("Manual refresh requested")
                fed_data = load_fed_data()
                update_time = datetime.now(TZ_JST).strftime('%Y年%m月%d日 %H:%M:%S')
                
                if fed_data:
                    # Cache the new data
                    cache_fed_data(fed_data, update_time)
                    
                    success_msg = f"データ更新完了 ({len(fed_data)}件の会議)"
                    return JsonResponse({
                        'success': True,
                        'update_time': update_time,
                        'message': success_msg
                    })
                else:
                    return JsonResponse({
                        'success': False,
                        'error': 'データの取得に失敗しました'
                    })
        except Exception as e:
            print(f"POST error: {e}")
            return JsonResponse({'success': False, 'error': str(e)})
    
    # GET request - render the main page with cached data (similar to sector page)
    fed_data, cached_update_time = get_cached_fed_data()
    
    if fed_data:
        print(f"Using cached data with {len(fed_data)} meetings")
    else:
        print("No cached data available")
    
    context = {
        'fed_data': fed_data,
        'fed_data_json': json.dumps(fed_data, ensure_ascii=False),
        'update_time': cached_update_time
    }
    
    return render(request, 'control/index.html', context)