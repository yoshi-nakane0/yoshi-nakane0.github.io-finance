# control/views.py
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from datetime import datetime, timedelta
import json
import requests
import re

def scrape_investing_fed_data():
    """Investing.comからFed Rate Monitorデータをスクレイピング"""
    try:
        print("Starting scraping process...")
        # より現実的なブラウザヘッダーを使用
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"macOS"'
        }
        
        # 指定されたURLを使用
        url = "https://jp.investing.com/central-banks/fed-rate-monitor"
        print(f"Fetching URL: {url}")
        
        # セッションを使用してリトライ機能を追加
        session = requests.Session()
        session.headers.update(headers)
        
        # 本番環境でのプロキシ設定（必要に応じて）
        import os
        if os.environ.get('DJANGO_SETTINGS_MODULE') == 'myproject.settings.production':
            # 本番環境の場合の特別な設定
            print("Running in production environment")
            # プロキシが必要な場合は設定
            # session.proxies = {'http': 'proxy_url', 'https': 'proxy_url'}
        
        # リトライ機能（最大3回）
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"Attempt {attempt + 1}/{max_retries}")
                # 本番環境では少し待機
                import time
                if attempt > 0:
                    wait_time = attempt * 2
                    print(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                
                response = session.get(url, timeout=30, verify=True, allow_redirects=True)
                print(f"HTTP Response: {response.status_code}")
                
                if response.status_code == 200:
                    break
                elif response.status_code == 429:  # Too Many Requests
                    print("Rate limited, waiting longer...")
                    time.sleep(10)
                    continue
                else:
                    print(f"HTTP Error {response.status_code}, retrying...")
                    continue
                    
            except requests.exceptions.Timeout:
                print(f"Timeout on attempt {attempt + 1}")
                if attempt == max_retries - 1:
                    raise
                continue
            except requests.exceptions.RequestException as e:
                print(f"Request error on attempt {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise
                continue
        
        if response.status_code == 200:
            print(f"Response content length: {len(response.text)}")
            scraped_data = parse_cardwrapper_data(response.text)
            
            if scraped_data:
                print(f"Successfully scraped data for {len(scraped_data)} meetings")
                print(f"Scraped dates: {list(scraped_data.keys())}")
                return scraped_data
            else:
                print("No data found in scraped content")
                print("First 1000 chars of response:")
                print(response.text[:1000])
                return {}
        else:
            print(f"Failed to fetch data: HTTP {response.status_code}")
            print(f"Response text: {response.text[:500]}")
            return {}
            
    except requests.exceptions.Timeout as e:
        print(f"Timeout error: {e}")
        return {}
    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        return {}
    except Exception as e:
        print(f"Error scraping Investing.com data: {e}")
        import traceback
        traceback.print_exc()
        return {}


def get_static_meeting_data(date):
    """特定の会合日程用の静的データを取得"""
    static_data = {
        "2025-07-30": {
            'probabilities': [
                {'range': '3.25-3.50%', 'current': '8.5%', 'oneDay': '8.3%', 'oneWeek': '8.8%', 'type': 'positive'},
                {'range': '3.50-3.75%', 'current': '18.2%', 'oneDay': '18.0%', 'oneWeek': '17.5%', 'type': 'positive'},
                {'range': '3.75-4.00%', 'current': '31.4%', 'oneDay': '31.8%', 'oneWeek': '32.1%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '25.6%', 'oneDay': '25.4%', 'oneWeek': '24.9%', 'type': 'positive'},
                {'range': '4.25-4.50%', 'current': '10.8%', 'oneDay': '11.0%', 'oneWeek': '11.5%', 'type': 'negative'},
            ]
        },
        "2025-09-17": {
            'probabilities': [
                {'range': '3.25-3.50%', 'current': '14.7%', 'oneDay': '14.5%', 'oneWeek': '15.2%', 'type': 'positive'},
                {'range': '3.50-3.75%', 'current': '28.9%', 'oneDay': '29.1%', 'oneWeek': '28.5%', 'type': 'positive'},
                {'range': '3.75-4.00%', 'current': '32.8%', 'oneDay': '32.6%', 'oneWeek': '31.9%', 'type': 'positive'},
                {'range': '4.00-4.25%', 'current': '15.2%', 'oneDay': '15.4%', 'oneWeek': '16.1%', 'type': 'negative'},
                {'range': '4.25-4.50%', 'current': '3.0%', 'oneDay': '3.1%', 'oneWeek': '2.7%', 'type': 'positive'},
            ]
        },
        "2025-10-29": {
            'probabilities': [
                {'range': '3.25-3.50%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
                {'range': '3.50-3.75%', 'current': '1.3%', 'oneDay': '1.3%', 'oneWeek': '1.2%', 'type': 'positive'},
                {'range': '3.75-4.00%', 'current': '30.9%', 'oneDay': '30.9%', 'oneWeek': '29.0%', 'type': 'positive'},
                {'range': '4.00-4.25%', 'current': '48.6%', 'oneDay': '48.6%', 'oneWeek': '48.6%', 'type': 'positive'},
                {'range': '4.25-4.50%', 'current': '19.2%', 'oneDay': '19.2%', 'oneWeek': '21.1%', 'type': 'negative'},
            ]
        },
        "2025-12-10": {
            'probabilities': [
                {'range': '3.25-3.50%', 'current': '0.8%', 'oneDay': '0.8%', 'oneWeek': '0.8%', 'type': 'positive'},
                {'range': '3.50-3.75%', 'current': '18.9%', 'oneDay': '18.9%', 'oneWeek': '19.1%', 'type': 'negative'},
                {'range': '3.75-4.00%', 'current': '41.4%', 'oneDay': '41.4%', 'oneWeek': '41.7%', 'type': 'negative'},
                {'range': '4.00-4.25%', 'current': '31.1%', 'oneDay': '31.1%', 'oneWeek': '30.9%', 'type': 'positive'},
                {'range': '4.25-4.50%', 'current': '7.8%', 'oneDay': '7.8%', 'oneWeek': '7.5%', 'type': 'positive'},
            ]
        }
    }
    
    # データを取得して最高確率フラグを追加
    data = static_data.get(date, {
        'probabilities': [
            {'range': '3.25-3.50%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
            {'range': '3.50-3.75%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
            {'range': '3.75-4.00%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
            {'range': '4.00-4.25%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneDay': '0.0%', 'oneWeek': '0.0%', 'type': 'negative'},
        ]
    })
    
    return data

def fetch_free_fed_monitor_data():
    """無料ソースから Fed Rate Monitor Tool データを取得"""
    try:
        print("Fetching free Fed Monitor data...")
        
        # 実際のスクレイピングを試行
        scraped_data = scrape_investing_fed_data()
        
        if scraped_data:
            # スクレイピングしたデータを直接使用（既に正しい形式）
            fed_monitor_data = {}
            
            for date, meeting_data in scraped_data.items():
                probabilities = meeting_data.get('probabilities', [])
                
                fed_monitor_data[date] = {
                    'probabilities': probabilities
                }
            
            # スクレイピングできなかった対象日程のみ静的データで補完
            target_dates = ["2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10"]
            for date in target_dates:
                if date not in fed_monitor_data:
                    print(f"Using static data for missing date: {date}")
                    fed_monitor_data[date] = get_static_meeting_data(date)
                else:
                    print(f"Using scraped data for date: {date}")
            
            # 2026年のデータは標準の0%データで補完
            all_fed_data = get_fed_monitor_data()
            for date in all_fed_data:
                if date not in fed_monitor_data:
                    fed_monitor_data[date] = all_fed_data[date]
            
            return fed_monitor_data
        
        # スクレイピングに失敗した場合は最新の静的データを使用
        print("Scraping failed, using static data as fallback...")
        
        fed_monitor_data = {}
        target_dates = ["2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10"]
        for date in target_dates:
            fed_monitor_data[date] = get_static_meeting_data(date)
        
        # 2026年のデータは標準の0%データを使用
        all_fed_data = get_fed_monitor_data()
        for date in all_fed_data:
            if date not in fed_monitor_data:
                fed_monitor_data[date] = all_fed_data[date]
        
        print(f"Successfully generated Fed Monitor data for {len(fed_monitor_data)} meetings")
        return fed_monitor_data
        
    except Exception as e:
        print(f"Failed to fetch free Fed Monitor data: {e}")
        # 例外時は静的データをフォールバックとして使用
        static_dates = ["2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10"]
        fed_monitor_data = {}
        for date in static_dates:
            fed_monitor_data[date] = get_static_meeting_data(date)
        
        # 2026年のデータは標準の0%データを使用
        all_fed_data = get_fed_monitor_data()
        for date in all_fed_data:
            if date not in fed_monitor_data:
                fed_monitor_data[date] = all_fed_data[date]
        
        return fed_monitor_data

def scrape_investing_html_data(meeting_date, headers):
    """HTMLからInvesting.com Fed Rate Monitor データをスクレイピング"""
    try:
        url = "https://www.investing.com/central-banks/fed-rate-monitor"
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            html_content = response.text
            
            # HTMLからテーブルデータを抽出
            probabilities = parse_investing_html_table(html_content)
            if probabilities:
                return probabilities
                
        return []
        
    except Exception as e:
        print(f"Error scraping Investing.com HTML for {meeting_date}: {e}")
        return []

def parse_cardwrapper_data(html_content):
    """cardWrapperからスクレイピングしたデータを解析"""
    try:
        import re
        from bs4 import BeautifulSoup
        
        soup = BeautifulSoup(html_content, 'html.parser')
        card_wrappers = soup.find_all('div', class_='cardWrapper')
        
        meetings_data = {}
        # 実際のHTML上での日付と対応する対象日付のマッピング
        date_mapping = {
            "2025-07-31": "2025-07-30", # 7月31日 -> 7月30日会合
            "2025-09-18": "2025-09-17", # 9月18日 -> 9月17日会合  
            "2025-10-30": "2025-10-29", # 10月30日 -> 10月29日会合
            "2025-12-11": "2025-12-10"  # 12月11日 -> 12月10日会合
        }
        target_dates = ["2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10"]
        
        for card in card_wrappers:
            # 日付を取得
            date_element = card.find('div', class_='fedRateDate')
            if not date_element:
                continue
                
            date_text = date_element.get_text().strip()
            # 日本語の日付を変換 (例: 2025年07月31日 -> 2025-07-31)
            date_match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_text)
            if not date_match:
                continue
                
            year, month, day = date_match.groups()
            formatted_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
            
            # 日付マッピングをチェックして対象日付を決定
            target_date = date_mapping.get(formatted_date)
            if not target_date:
                continue
            
            # 会合時間を取得
            meeting_time = ""
            info_fed = card.find('div', class_='infoFed')
            if info_fed:
                time_element = info_fed.find('i')
                if time_element:
                    meeting_time = time_element.get_text().strip()
            
            # 先物価格を取得
            futures_price = ""
            if info_fed:
                span_elements = info_fed.find_all('span')
                for i, span in enumerate(span_elements):
                    if '先物価格' in span.get_text():
                        next_i = span.find_next_sibling('i')
                        if next_i:
                            futures_price = next_i.get_text().strip()
                            break
            
            # 確率データを取得
            probabilities = []
            perc_items = card.find_all('div', class_='percfedRateItem')
            
            # 5つの指定レンジのみ処理
            standard_ranges = ["3.25-3.50%", "3.50-3.75%", "3.75-4.00%", "4.00-4.25%", "4.25-4.50%"]
            
            # percfedRateItemからは現在値のみ取得（テーブルデータを優先）
            perc_data = {}
            for item in perc_items:
                spans = item.find_all('span')
                if len(spans) >= 2:
                    range_text = spans[0].get_text().strip()  # "4.00 - 4.25" 形式
                    prob_text = spans[-1].get_text().strip()  # "4.3%" 形式
                    
                    # レンジ形式を標準形式に変換 "4.00 - 4.25" -> "4.00-4.25%"
                    normalized_range = range_text.replace(' ', '') + '%'
                    
                    # 指定レンジのみ保存
                    if normalized_range in standard_ranges:
                        perc_data[normalized_range] = prob_text
            
            # テーブルから詳細データを取得（現在/前日/前週）
            table = card.find('table', class_='genTbl')
            if table:
                print(f"Found table for date: {target_date}")
                rows = table.find('tbody').find_all('tr') if table.find('tbody') else []
                print(f"Found {len(rows)} table rows")
                
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 4:
                        # 最初のセルからレンジテキストを取得
                        range_cell = cells[0]
                        # チャートアイコンのspanを除去
                        chart_span = range_cell.find('span', class_='chartIcon')
                        if chart_span:
                            chart_span.decompose()
                        range_text = range_cell.get_text().strip()  # "3.50 - 3.75" 形式
                        
                        # レンジ形式を標準形式に変換 "3.50 - 3.75" -> "3.50-3.75%"
                        normalized_range = range_text.replace(' ', '') + '%'
                        
                        current = cells[1].get_text().strip()  # "1.2%" 形式 (現在)
                        one_day = cells[2].get_text().strip()  # "1.3%" 形式 (前日)
                        one_week = cells[3].get_text().strip()  # "1.2%" 形式 (前週)
                        
                        print(f"Raw table data - Range: '{range_text}' -> '{normalized_range}', Current: {current}, OneDay: {one_day}, OneWeek: {one_week}")
                        
                        if normalized_range in standard_ranges:
                            prob_data = {
                                'range': normalized_range,
                                'current': current,
                                'oneDay': one_day,
                                'oneWeek': one_week,  # テーブルの前週列から取得
                                'type': 'positive' if float(current.replace('%', '')) > float(one_week.replace('%', '')) else 'negative'
                            }
                            
                            print(f"Added table data for {normalized_range}: current={current}, oneWeek={one_week}")
                            probabilities.append(prob_data)
                        else:
                            print(f"Normalized range '{normalized_range}' not in standard_ranges")
            
            # 更新時間を取得
            update_time = ""
            fed_update = card.find('div', class_='fedUpdate')
            if fed_update:
                update_time = fed_update.get_text().replace('更新: ', '').strip()
            
            # テーブルデータがない場合はpercfedRateItemのデータで補完
            existing_ranges = [p['range'] for p in probabilities]
            for range_str in standard_ranges:
                if range_str not in existing_ranges:
                    # percfedRateItemからの値があれば使用
                    current_val = perc_data.get(range_str, '0.0%')
                    probabilities.append({
                        'range': range_str,
                        'current': current_val,
                        'oneDay': current_val,  # 前日データがない場合は現在値を使用
                        'oneWeek': '0.0%',      # 前週データがない場合は0%
                        'type': 'negative'
                    })
            
            # レンジ順にソート
            probabilities.sort(key=lambda x: standard_ranges.index(x['range']))
            
            print(f"Final probabilities for {target_date}: {len(probabilities)} items")
            for prob in probabilities:
                print(f"  {prob['range']}: current={prob['current']}, oneWeek={prob['oneWeek']}")
            
            meetings_data[target_date] = {
                'meeting_time': meeting_time,
                'futures_price': futures_price,
                'probabilities': probabilities,
                'update_time': update_time
            }
        
        return meetings_data
        
    except Exception as e:
        print(f"Error parsing cardWrapper data: {e}")
        import traceback
        traceback.print_exc()
        return {}

def parse_investing_html_table(html_content):
    """Investing.com HTMLテーブルからデータを抽出"""
    try:
        import re
        
        # テーブル行のパターンを検索（Fed Rate Monitor Tool用）
        table_patterns = [
            r'<tr[^>]*>.*?(\d+\.\d+-\d+\.\d+%).*?(\d+\.\d+%).*?(\d+\.\d+%).*?(\d+\.\d+%).*?</tr>',
            r'data-range="([^"]*)".*?data-current="([^"]*)".*?data-1w="([^"]*)".*?data-1m="([^"]*)"',
            r'(\d+\.\d+-\d+\.\d+%)[^<]*<[^>]*>([^<]*%)[^<]*<[^>]*>([^<]*%)[^<]*<[^>]*>([^<]*%)'
        ]
        
        probabilities = []
        
        # 標準的な金利レンジを定義（0.25%刻み）
        standard_ranges = [
            "3.25-3.50%", "3.50-3.75%", "3.75-4.00%",
            "4.00-4.25%", "4.25-4.50%"
        ]
        
        for pattern in table_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL)
            for match in matches:
                if len(match) >= 4:
                    try:
                        range_str = match[0].strip()
                        current = float(match[1].replace('%', '').strip())
                        week = float(match[2].replace('%', '').strip()) if match[2].strip() != '-' else 0.0
                        day = current  # 1日前は現在と同じ値を使用
                        
                        prob_data = {
                            'range': range_str,
                            'current': f"{current:.1f}%",
                            'oneDay': f"{day:.1f}%",
                            'oneWeek': f"{week:.1f}%",
                            'type': 'positive' if current > week else 'negative'
                        }
                        probabilities.append(prob_data)
                    except (ValueError, IndexError):
                        continue
        
        # データが見つからない場合、標準レンジで0%データを作成
        if not probabilities:
            for range_str in standard_ranges:
                prob_data = {
                    'range': range_str,
                    'current': "0.0%",
                    'oneDay': "0.0%",
                    'oneWeek': "0.0%",
                    'type': 'negative'
                }
                probabilities.append(prob_data)
        
        return probabilities
        
    except Exception as e:
        print(f"Error parsing Investing.com HTML table data: {e}")
        return []

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
            {'range': '3.00-3.25%', 'current': '2.8%', 'oneWeek': '3.2%', 'oneMonth': '3.5%'},
            {'range': '3.25-3.50%', 'current': '12.8%', 'oneWeek': '14.2%', 'oneMonth': '16.7%'},
            {'range': '3.50-3.75%', 'current': '25.1%', 'oneWeek': '26.3%', 'oneMonth': '28.9%'},
            {'range': '3.75-4.00%', 'current': '42.3%', 'oneWeek': '41.1%', 'oneMonth': '37.8%'},
            {'range': '4.00-4.25%', 'current': '12.1%', 'oneWeek': '10.8%', 'oneMonth': '7.2%'},
            {'range': '4.25-4.50%', 'current': '2.5%', 'oneWeek': '1.5%', 'oneMonth': '1.1%'},
            {'range': '4.50-4.75%', 'current': '2.4%', 'oneWeek': '1.8%', 'oneMonth': '1.5%'},
            {'range': '4.75-5.00%', 'current': '0.3%', 'oneWeek': '0.2%', 'oneMonth': '0.1%'}
        ],
        '2025-09-17': [
            {'range': '3.00-3.25%', 'current': '5.1%', 'oneWeek': '5.4%', 'oneMonth': '6.2%'},
            {'range': '3.25-3.50%', 'current': '18.4%', 'oneWeek': '20.2%', 'oneMonth': '23.5%'},
            {'range': '3.50-3.75%', 'current': '35.8%', 'oneWeek': '36.1%', 'oneMonth': '35.2%'},
            {'range': '3.75-4.00%', 'current': '28.1%', 'oneWeek': '26.8%', 'oneMonth': '24.1%'},
            {'range': '4.00-4.25%', 'current': '7.9%', 'oneWeek': '6.5%', 'oneMonth': '4.8%'},
            {'range': '4.25-4.50%', 'current': '1.1%', 'oneWeek': '0.6%', 'oneMonth': '0.3%'},
            {'range': '4.50-4.75%', 'current': '0.3%', 'oneWeek': '0.2%', 'oneMonth': '0.1%'},
            {'range': '4.75-5.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '2025-10-29': [
            {'range': '3.00-3.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.25-3.50%', 'current': '28.9%', 'oneWeek': '30.4%', 'oneMonth': '33.2%'},
            {'range': '3.50-3.75%', 'current': '41.2%', 'oneWeek': '40.1%', 'oneMonth': '37.9%'},
            {'range': '3.75-4.00%', 'current': '15.1%', 'oneWeek': '13.8%', 'oneMonth': '10.7%'},
            {'range': '4.00-4.25%', 'current': '2.3%', 'oneWeek': '1.8%', 'oneMonth': '1.2%'},
            {'range': '4.25-4.50%', 'current': '0.2%', 'oneWeek': '0.2%', 'oneMonth': '0.2%'},
            {'range': '4.50-4.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.75-5.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '2025-12-10': [
            {'range': '3.00-3.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.25-3.50%', 'current': '35.7%', 'oneWeek': '37.2%', 'oneMonth': '39.8%'},
            {'range': '3.50-3.75%', 'current': '32.4%', 'oneWeek': '31.1%', 'oneMonth': '28.9%'},
            {'range': '3.75-4.00%', 'current': '12.8%', 'oneWeek': '11.2%', 'oneMonth': '8.1%'},
            {'range': '4.00-4.25%', 'current': '2.1%', 'oneWeek': '1.8%', 'oneMonth': '1.0%'},
            {'range': '4.25-4.50%', 'current': '0.2%', 'oneWeek': '0.2%', 'oneMonth': '0.1%'},
            {'range': '4.50-4.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.75-5.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '2026-01-26': [
            {'range': '3.00-3.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.25-3.50%', 'current': '47.8%', 'oneWeek': '46.2%', 'oneMonth': '43.1%'},
            {'range': '3.50-3.75%', 'current': '25.1%', 'oneWeek': '25.8%', 'oneMonth': '25.2%'},
            {'range': '3.75-4.00%', 'current': '4.3%', 'oneWeek': '3.6%', 'oneMonth': '3.6%'},
            {'range': '4.00-4.25%', 'current': '0.4%', 'oneWeek': '0.3%', 'oneMonth': '0.3%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.50-4.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.75-5.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '2026-03-18': [
            {'range': '3.00-3.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.25-3.50%', 'current': '42.1%', 'oneWeek': '40.8%', 'oneMonth': '38.9%'},
            {'range': '3.50-3.75%', 'current': '17.3%', 'oneWeek': '17.9%', 'oneMonth': '18.1%'},
            {'range': '3.75-4.00%', 'current': '1.8%', 'oneWeek': '1.8%', 'oneMonth': '1.7%'},
            {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.50-4.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.75-5.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '2026-04-29': [
            {'range': '3.00-3.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.25-3.50%', 'current': '35.4%', 'oneWeek': '36.1%', 'oneMonth': '37.8%'},
            {'range': '3.50-3.75%', 'current': '12.1%', 'oneWeek': '11.8%', 'oneMonth': '11.9%'},
            {'range': '3.75-4.00%', 'current': '1.2%', 'oneWeek': '1.2%', 'oneMonth': '1.1%'},
            {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.50-4.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.75-5.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '2026-06-17': [
            {'range': '3.00-3.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.25-3.50%', 'current': '28.7%', 'oneWeek': '29.8%', 'oneMonth': '32.1%'},
            {'range': '3.50-3.75%', 'current': '6.9%', 'oneWeek': '6.7%', 'oneMonth': '6.9%'},
            {'range': '3.75-4.00%', 'current': '0.5%', 'oneWeek': '0.5%', 'oneMonth': '0.5%'},
            {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.50-4.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.75-5.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
        ],
        '0000-00-00': [
            {'range': '3.00-3.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.25-3.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.50-3.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.75-4.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.00-4.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.50-4.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.75-5.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
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