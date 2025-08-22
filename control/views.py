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

# JST timezone & target URL
TZ_JST = timezone(timedelta(hours=9))
INVESTING_FED_URL = "https://www.investing.com/central-banks/fed-rate-monitor"


def fetch_fed_data_from_web():
    """Investing.com から複数経路（プロキシ/直）でFedレートデータを取得"""
    data_sources = [
        ('Proxy Chain + Investing.com', fetch_via_proxy_chain),
        ('Direct with Rate Limiting', fetch_with_rate_limiting),
    ]

    for source_name, fetch_function in data_sources:
        try:
            print(f"Trying {source_name}...")
            # 各ソース間で少し待機（レート制限対策）
            time.sleep(random.uniform(1, 2))
            data = fetch_function()
            if data and len(data) > 0:
                print(f"Successfully fetched from {source_name}")
                return data
        except Exception as e:
            print(f"Failed to fetch from {source_name}: {e}")
            continue

    print("All data sources failed")
    return None


def parse_investing_data_html(html_content):
    """investing.comのHTMLからFedレートデータを解析（特定の構造に対応）"""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        fed_data = {}

        print("Parsing HTML content...")
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
                'type': prob_type,
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
    # まず数字とハイフンのパターンを抽出
    match = re.search(r'\d+\.\d+\s*[-–]\s*\d+\.\d+', target_rate_text)
    if match:
        return match.group().strip()

    return target_rate_text.strip()


def parse_meeting_date(date_str):
    """会議日文字列を標準形式に変換"""
    try:
        # 例:
        # "Sep 17, 2025" -> "2025-09-17"
        # "Sep 17-18, 2025" -> "2025-09-17" (範囲の場合は開始日)
        months = {
            'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
            'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
            'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
        }

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

        month = months.get(month_str)
        if not month:
            return date_str

        # 日付から範囲の最初の日を取得（例: "17-18" -> "17"）
        day = day_str.split('-')[0] if '-' in day_str else day_str
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
    except Exception:
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
    except Exception:
        pass

    return 'negative'


def fetch_via_proxy_chain():
    """複数のプロキシサーバーチェーンを経由してInvesting.comにアクセス"""
    proxy_services = [
        "https://api.allorigins.win/get?url=",
        "https://cors-anywhere.herokuapp.com/",
        "https://thingproxy.freeboard.io/fetch/",
        "https://api.codetabs.com/v1/proxy/?quest=",
    ]

    target_url = INVESTING_FED_URL

    for proxy_url in proxy_services:
        try:
            print(f"Trying proxy: {proxy_url[:30]}...")

            if "allorigins" in proxy_url:
                # AllOrigins API形式
                full_url = f"{proxy_url}{requests.utils.quote(target_url, safe='')}"
                response = requests.get(full_url, timeout=20)
                if response.status_code == 200:
                    json_data = response.json()
                    if json_data.get('contents'):
                        # contents は str なのでそのまま渡す
                        return parse_investing_data_html(json_data['contents'])
            else:
                # 標準プロキシ形式
                full_url = proxy_url + target_url
                headers = get_rotating_headers()
                response = requests.get(full_url, headers=headers, timeout=20)
                if response.status_code == 200:
                    return parse_investing_data_html(response.content)

        except Exception as e:
            print(f"Proxy failed: {e}")
            continue

    return None


def fetch_with_rate_limiting():
    """レート制限とローテーション機能付きの直接アクセス"""
    try:
        target_url = INVESTING_FED_URL
        with requests.Session() as session:
            headers = get_rotating_headers()

            # より自然なアクセスパターン
            # 1. まずInvesting.comのトップページを訪問
            print("Visiting investing.com homepage...")
            session.get("https://www.investing.com/", headers=headers, timeout=10)
            time.sleep(random.uniform(2, 5))

            # 2. 検索ページを経由
            search_url = "https://www.investing.com/search/"
            session.get(search_url, headers=headers, timeout=10)
            time.sleep(random.uniform(1, 3))

            # 3. 最終的にターゲットページへ
            headers['Referer'] = search_url
            response = session.get(target_url, headers=headers, timeout=20)

            if response.status_code == 200:
                return parse_investing_data_html(response.content)
            else:
                print(f"Rate limited access failed: {response.status_code}")

    except Exception as e:
        print(f"Rate limited access error: {e}")

    return None


def get_rotating_headers():
    """ローテーション用のHTTPヘッダーを生成"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0',
    ]

    accept_languages = [
        'ja-JP,ja;q=0.9,en;q=0.8',
        'ja;q=0.9,en-US;q=0.8,en;q=0.7',
        'ja-JP,ja;q=0.8,en-US;q=0.5,en;q=0.3',
    ]

    return {
        'User-Agent': random.choice(user_agents),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': random.choice(accept_languages),
        'Connection': 'keep-alive',
    }


def get_cached_fed_data():
    """初期表示用にキャッシュから取得"""
    cached_data = cache.get('fed_data')
    cached_time = cache.get('fed_data_update_time')

    if cached_data and cached_time:
        return cached_data, cached_time

    # If no cache, return empty data
    last_update = cache.get('last_fed_manual_update_time')
    if last_update:
        update_time = f"前回更新: {last_update}"
    else:
        update_time = "データを取得するには更新ボタンを押してください"

    return {}, update_time


def cache_fed_data(fed_data, update_time):
    """Fedデータをキャッシュ"""
    # Cache for 24 hours (86400 seconds)
    cache.set('fed_data', fed_data, 86400)
    cache.set('fed_data_update_time', update_time, 86400)
    # Store the last manual update time separately
    cache.set('last_fed_manual_update_time', update_time, 86400 * 7)  # Keep for 7 days


def load_fed_data():
    """Webからデータを読み込む（更新時のみ使用）"""
    print("Fetching data from web sources...")
    web_data = fetch_fed_data_from_web()

    if web_data and len(web_data) > 0:
        print(f"Successfully fetched data from web: {len(web_data)} meetings")
        return web_data

    # Webから取得失敗した場合はキャッシュされたデータを返す
    print("Web fetch failed, checking cache...")
    cached_data = cache.get('fed_data')
    if cached_data and len(cached_data) > 0:
        print("Using cached data")
        return cached_data

    print("No data available")
    return None


@csrf_exempt
def index(request):
    if request.method == 'POST':
        # Handle AJAX refresh request
        try:
            data = json.loads(request.body)
            if data.get('action') == 'refresh':
                print("Manual refresh requested")
                fed_data = load_fed_data()
                update_time = datetime.now(TZ_JST).strftime('%Y-%m-%d %H:%M:%S')

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

    # GET request - render the main page with cached data
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
