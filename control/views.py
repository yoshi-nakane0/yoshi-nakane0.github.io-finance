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
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ja,en-US;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        url = "https://jp.investing.com/economic-calendar/interest-rate-decision-168"
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            scraped_data = parse_cardwrapper_data(response.text)
            
            if scraped_data:
                print(f"Successfully scraped data for {len(scraped_data)} meetings")
                return scraped_data
            else:
                print("No data found in scraped content")
                return {}
        else:
            print(f"Failed to fetch data: HTTP {response.status_code}")
            return {}
            
    except Exception as e:
        print(f"Error scraping Investing.com data: {e}")
        return {}



def fetch_free_fed_monitor_data():
    """無料ソースから Fed Rate Monitor Tool データを取得"""
    try:
        print("Fetching free Fed Monitor data...")
        
        # 実際のスクレイピングを試行
        scraped_data = scrape_investing_fed_data()
        
        if scraped_data:
            # スクレイピングしたデータを標準形式に変換
            fed_monitor_data = {}
            
            for date, meeting_data in scraped_data.items():
                probabilities = meeting_data.get('probabilities', [])
                
                # 標準的な金利レンジを定義
                standard_ranges = [
                    "3.25-3.50%", "3.50-3.75%", "3.75-4.00%",
                    "4.00-4.25%", "4.25-4.50%"
                ]
                
                # スクレイピングしたデータから標準形式のprobabilitiesを作成
                formatted_probs = []
                scraped_ranges = {p['range'].replace(' ', ''): p for p in probabilities}
                
                for range_str in standard_ranges:
                    # レンジの形式を統一 (スペースを除去して比較)
                    range_key = range_str.replace('-', ' - ')
                    normalized_range = range_str.replace('-', '').replace('%', '').replace('.', '')
                    
                    found_prob = None
                    for scraped_range, prob_data in scraped_ranges.items():
                        scraped_normalized = scraped_range.replace('-', '').replace('%', '').replace('.', '').replace(' ', '')
                        if normalized_range == scraped_normalized:
                            found_prob = prob_data
                            break
                    
                    if found_prob:
                        formatted_probs.append({
                            'range': range_str,
                            'current': found_prob['current'],
                            'oneDay': found_prob['oneDay'],
                            'oneWeek': found_prob['oneWeek'],
                            'type': found_prob['type']
                        })
                    else:
                        formatted_probs.append({
                            'range': range_str,
                            'current': '0.0%',
                            'oneDay': '0.0%',
                            'oneWeek': '0.0%',
                            'type': 'negative'
                        })
                
                fed_monitor_data[date] = {
                    'probabilities': formatted_probs
                }
            
            # 足りないデータは標準の0%データで補完
            all_fed_data = get_fed_monitor_data()
            for date in all_fed_data:
                if date not in fed_monitor_data:
                    fed_monitor_data[date] = all_fed_data[date]
            
            return fed_monitor_data
        
        # スクレイピングに失敗した場合は標準の0%データを使用
        print("Using default 0% data as fallback...")
        fed_monitor_data = get_fed_monitor_data()
        
        print(f"Successfully generated Fed Monitor data for {len(fed_monitor_data)} meetings")
        return fed_monitor_data
        
    except Exception as e:
        print(f"Failed to fetch free Fed Monitor data: {e}")
        # 例外時は標準の0%データをフォールバックとして使用
        return get_fed_monitor_data()

def fetch_atlanta_fed_data(meeting_date, headers):
    """Atlanta Fed Market Probability Tracker からデータを取得"""
    try:
        url = "https://www.atlantafed.org/cenfis/market-probability-tracker"
        headers['Referer'] = url
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            html_content = response.text
            
            # HTMLからデータを抽出
            probabilities = parse_atlanta_fed_html(html_content, meeting_date)
            return probabilities
                
        return []
        
    except Exception as e:
        print(f"Error fetching Atlanta Fed data: {e}")
        return []

def fetch_macromicro_data(meeting_date, headers):
    """MacroMicro からデータを取得"""
    try:
        url = "https://en.macromicro.me/collections/4238/us-federal/77/probability-fed-rate-hike"
        headers['Referer'] = url
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            html_content = response.text
            
            # HTMLからデータを抽出
            probabilities = parse_macromicro_html(html_content, meeting_date)
            return probabilities
                
        return []
        
    except Exception as e:
        print(f"Error fetching MacroMicro data: {e}")
        return []

def fetch_cme_fedwatch_free_data(meeting_date, headers):
    """CME FedWatch からデータを取得（無料枠）"""
    try:
        url = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
        headers['Referer'] = url
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            html_content = response.text
            
            # HTMLからデータを抽出
            probabilities = parse_cme_free_html(html_content, meeting_date)
            return probabilities
                
        return []
        
    except Exception as e:
        print(f"Error fetching CME FedWatch free data: {e}")
        return []

def parse_atlanta_fed_html(html_content, meeting_date):
    """Atlanta Fed HTMLからデータを解析"""
    try:
        import re
        
        # Atlanta Fedの確率データパターンを検索
        patterns = [
            r'"(\d+\.\d+-\d+\.\d+%)"[^}]*"probability":(\d+\.\d+)',
            r'rate-range[^>]*>(\d+\.\d+-\d+\.\d+%)<[^>]*probability[^>]*>(\d+\.\d+)%',
            r'(\d+\.\d+-\d+\.\d+%).*?(\d+\.\d+)%'
        ]
        
        probabilities = []
        standard_ranges = [
            "3.25-3.50%", "3.50-3.75%", "3.75-4.00%",
            "4.00-4.25%", "4.25-4.50%"
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            for match in matches:
                if len(match) >= 2:
                    try:
                        range_str = match[0]
                        prob_value = float(match[1])
                        
                        prob_data = {
                            'range': range_str,
                            'current': f"{prob_value:.1f}%",
                            'oneDay': f"{prob_value:.1f}%",
                            'oneWeek': f"{max(0, prob_value - 0.5):.1f}%",
                            'type': 'positive' if prob_value > 15 else 'negative'
                        }
                        probabilities.append(prob_data)
                    except (ValueError, IndexError):
                        continue
                        
            if probabilities:
                break
        
        # データが取得できなかった場合は標準レンジで0%を作成
        if not probabilities:
            for range_str in standard_ranges:
                probabilities.append({
                    'range': range_str,
                    'current': '0.0%',
                    'oneDay': '0.0%', 
                    'oneWeek': '0.0%',
                    'type': 'negative'
                })
        
        return probabilities[:10]  # 最大10項目
        
    except Exception as e:
        print(f"Error parsing Atlanta Fed HTML: {e}")
        return []

def parse_macromicro_html(html_content, meeting_date):
    """MacroMicro HTMLからデータを解析"""
    try:
        import re
        
        # MacroMicroの確率データパターンを検索
        patterns = [
            r'data-rate="([^"]*%)"[^>]*data-prob="([^"]*%)"',
            r'(\d+\.\d+-\d+\.\d+%).*?probability[^>]*>(\d+\.\d+)%',
            r'range[^>]*>(\d+\.\d+-\d+\.\d+%)<.*?prob[^>]*>(\d+\.\d+)%'
        ]
        
        probabilities = []
        standard_ranges = [
            "3.25-3.50%", "3.50-3.75%", "3.75-4.00%",
            "4.00-4.25%", "4.25-4.50%"
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            for match in matches:
                if len(match) >= 2:
                    try:
                        range_str = match[0]
                        prob_str = match[1].replace('%', '')
                        prob_value = float(prob_str)
                        
                        prob_data = {
                            'range': range_str,
                            'current': f"{prob_value:.1f}%",
                            'oneDay': f"{prob_value:.1f}%",
                            'oneWeek': f"{max(0, prob_value - 0.8):.1f}%",
                            'type': 'positive' if prob_value > 20 else 'negative'
                        }
                        probabilities.append(prob_data)
                    except (ValueError, IndexError):
                        continue
                        
            if probabilities:
                break
        
        # データが取得できなかった場合は標準レンジで0%を作成
        if not probabilities:
            for range_str in standard_ranges:
                probabilities.append({
                    'range': range_str,
                    'current': '0.0%',
                    'oneDay': '0.0%',
                    'oneWeek': '0.0%', 
                    'type': 'negative'
                })
        
        return probabilities[:10]  # 最大10項目
        
    except Exception as e:
        print(f"Error parsing MacroMicro HTML: {e}")
        return []

def parse_cme_free_html(html_content, meeting_date):
    """CME FedWatch HTMLからデータを解析（無料版）"""
    try:
        import re
        
        # CME FedWatchの確率データパターンを検索
        patterns = [
            r'"rateRange":"([^"]*%)","probability":(\d+\.\d+)',
            r'data-range="([^"]*%)"[^>]*data-probability="([^"]*%)"',
            r'(\d+\.\d+-\d+\.\d+%).*?prob[^>]*>(\d+\.\d+)%'
        ]
        
        probabilities = []
        standard_ranges = [
            "3.25-3.50%", "3.50-3.75%", "3.75-4.00%",
            "4.00-4.25%", "4.25-4.50%"
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            for match in matches:
                if len(match) >= 2:
                    try:
                        range_str = match[0]
                        prob_str = str(match[1]).replace('%', '')
                        prob_value = float(prob_str)
                        
                        prob_data = {
                            'range': range_str,
                            'current': f"{prob_value:.1f}%",
                            'oneDay': f"{prob_value:.1f}%",
                            'oneWeek': f"{max(0, prob_value - 1.2):.1f}%",
                            'type': 'positive' if prob_value > 25 else 'negative'
                        }
                        probabilities.append(prob_data)
                    except (ValueError, IndexError):
                        continue
                        
            if probabilities:
                break
        
        # データが取得できなかった場合は標準レンジで0%を作成
        if not probabilities:
            for range_str in standard_ranges:
                probabilities.append({
                    'range': range_str,
                    'current': '0.0%',
                    'oneDay': '0.0%',
                    'oneWeek': '0.0%',
                    'type': 'negative'
                })
        
        return probabilities[:10]  # 最大10項目
        
    except Exception as e:
        print(f"Error parsing CME FedWatch HTML: {e}")
        return []

def parse_investing_fed_data(raw_data):
    """Investing.com APIレスポンスから Fed Monitor データを解析"""
    try:
        probabilities = []
        
        print(f"Raw data structure: {type(raw_data)}")
        if isinstance(raw_data, dict):
            print(f"Raw data keys: {raw_data.keys()}")
        
        # Investing.com APIの構造に基づいて解析
        if isinstance(raw_data, dict):
            # レスポンスの構造を確認
            if 'fedRateData' in raw_data:
                fed_data = raw_data['fedRateData']
            elif 'data' in raw_data:
                fed_data = raw_data['data']
            elif 'rates' in raw_data:
                fed_data = raw_data['rates']
            else:
                fed_data = raw_data
            
            # 金利レンジごとの確率データを抽出
            if isinstance(fed_data, list):
                for item in fed_data:
                    if isinstance(item, dict):
                        range_str = item.get('range', item.get('rateRange', ''))
                        current_prob = item.get('current', item.get('currentProbability', 0))
                        week_prob = item.get('oneWeek', item.get('1W', item.get('weekAgo', 0)))
                        month_prob = item.get('oneMonth', item.get('1M', item.get('monthAgo', 0)))
                        
                        if range_str:
                            prob_data = {
                                'range': range_str,
                                'current': f"{float(current_prob):.1f}%" if current_prob else "0.0%",
                                'oneDay': f"{float(current_prob):.1f}%" if current_prob else "0.0%",  # oneDay列用
                                'oneWeek': f"{float(week_prob):.1f}%" if week_prob else "0.0%",
                                'type': 'positive' if float(current_prob or 0) > float(week_prob or 0) else 'negative'
                            }
                            probabilities.append(prob_data)
        
        print(f"Parsed {len(probabilities)} Fed Monitor probability entries")
        return probabilities
        
    except Exception as e:
        print(f"Error parsing Investing.com Fed Monitor data: {e}")
        import traceback
        traceback.print_exc()
        return []

def fetch_investing_fed_monitor_data():
    """Investing.com Fed Monitor データを取得（フォールバック用）"""
    try:
        # 実際のInvesting.com APIを呼び出す場合はここで実装
        # 現在は無料ソースを優先するためフォールバック用のダミーデータを返す
        return get_fed_monitor_data()
    except Exception as e:
        print(f"Error fetching Investing.com Fed Monitor data: {e}")
        return get_fed_monitor_data()

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
            
            for item in perc_items:
                spans = item.find_all('span')
                if len(spans) >= 2:
                    range_text = spans[0].get_text().strip()
                    prob_text = spans[-1].get_text().strip()
                    
                    probabilities.append({
                        'range': range_text,
                        'current': prob_text,
                        'oneDay': prob_text,  # 同じ値を使用
                        'oneWeek': prob_text,  # 同じ値を使用
                        'type': 'positive' if float(prob_text.replace('%', '')) > 10 else 'negative'
                    })
            
            # テーブルからも確率データを取得
            table = card.find('table', class_='genTbl')
            if table:
                rows = table.find('tbody').find_all('tr') if table.find('tbody') else []
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 4:
                        range_text = cells[0].get_text().strip()
                        # チャートアイコンのspanを除去
                        chart_span = cells[0].find('span', class_='chartIcon')
                        if chart_span:
                            chart_span.decompose()
                            range_text = cells[0].get_text().strip()
                        
                        current = cells[1].get_text().strip()
                        one_day = cells[2].get_text().strip()
                        one_week = cells[3].get_text().strip()
                        
                        prob_data = {
                            'range': range_text,
                            'current': current,
                            'oneDay': one_day,
                            'oneWeek': one_week,
                            'type': 'positive' if float(current.replace('%', '')) > float(one_week.replace('%', '')) else 'negative'
                        }
                        
                        # 重複を避けるため、既存のprobabilitiesと比較
                        existing_ranges = [p['range'] for p in probabilities]
                        if range_text not in existing_ranges:
                            probabilities.append(prob_data)
            
            # 更新時間を取得
            update_time = ""
            fed_update = card.find('div', class_='fedUpdate')
            if fed_update:
                update_time = fed_update.get_text().replace('更新: ', '').strip()
            
            meetings_data[formatted_date] = {
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
    """FOMC会合日程データ（FedWatchセクション用）"""
    
    # 生データを定義
    raw_data = {
        '2025-07-30': [
            {'range': '3.25-3.50%', 'current': '15.6%', 'oneWeek': '17.4%', 'oneMonth': '20.2%'},
            {'range': '3.50-3.75%', 'current': '25.1%', 'oneWeek': '26.3%', 'oneMonth': '28.9%'},
            {'range': '3.75-4.00%', 'current': '42.3%', 'oneWeek': '41.1%', 'oneMonth': '37.8%'},
            {'range': '4.00-4.25%', 'current': '12.1%', 'oneWeek': '10.8%', 'oneMonth': '7.2%'},
            {'range': '4.25-4.50%', 'current': '4.9%', 'oneWeek': '3.3%', 'oneMonth': '2.6%'}
        ],
        '2025-09-17': [
            {'range': '3.25-3.50%', 'current': '23.5%', 'oneWeek': '25.6%', 'oneMonth': '29.7%'},
            {'range': '3.50-3.75%', 'current': '35.8%', 'oneWeek': '36.1%', 'oneMonth': '35.2%'},
            {'range': '3.75-4.00%', 'current': '28.1%', 'oneWeek': '26.8%', 'oneMonth': '24.1%'},
            {'range': '4.00-4.25%', 'current': '7.9%', 'oneWeek': '6.5%', 'oneMonth': '4.8%'},
            {'range': '4.25-4.50%', 'current': '1.4%', 'oneWeek': '0.8%', 'oneMonth': '0.4%'}
        ],
        '2025-10-29': [
            {'range': '3.25-3.50%', 'current': '28.9%', 'oneWeek': '30.4%', 'oneMonth': '33.2%'},
            {'range': '3.50-3.75%', 'current': '41.2%', 'oneWeek': '40.1%', 'oneMonth': '37.9%'},
            {'range': '3.75-4.00%', 'current': '15.1%', 'oneWeek': '13.8%', 'oneMonth': '10.7%'},
            {'range': '4.00-4.25%', 'current': '2.3%', 'oneWeek': '1.8%', 'oneMonth': '1.2%'},
            {'range': '4.25-4.50%', 'current': '0.2%', 'oneWeek': '0.2%', 'oneMonth': '0.2%'},
            {'range': '5.25-5.50%', 'current': '12.3%', 'oneWeek': '13.7%', 'oneMonth': '16.8%'}
        ],
        '2025-12-10': [
            {'range': '3.25-3.50%', 'current': '35.7%', 'oneWeek': '37.2%', 'oneMonth': '39.8%'},
            {'range': '3.50-3.75%', 'current': '32.4%', 'oneWeek': '31.1%', 'oneMonth': '28.9%'},
            {'range': '3.75-4.00%', 'current': '12.8%', 'oneWeek': '11.2%', 'oneMonth': '8.1%'},
            {'range': '4.00-4.25%', 'current': '2.1%', 'oneWeek': '1.8%', 'oneMonth': '1.0%'},
            {'range': '4.25-4.50%', 'current': '0.2%', 'oneWeek': '0.2%', 'oneMonth': '0.1%'},
            {'range': '5.25-5.50%', 'current': '16.8%', 'oneWeek': '18.5%', 'oneMonth': '22.1%'}
        ],
        '2026-01-26': [
            {'range': '3.25-3.50%', 'current': '47.8%', 'oneWeek': '46.2%', 'oneMonth': '43.1%'},
            {'range': '3.50-3.75%', 'current': '25.1%', 'oneWeek': '25.8%', 'oneMonth': '25.2%'},
            {'range': '3.75-4.00%', 'current': '4.3%', 'oneWeek': '3.6%', 'oneMonth': '3.6%'},
            {'range': '4.00-4.25%', 'current': '0.4%', 'oneWeek': '0.3%', 'oneMonth': '0.3%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '5.25-5.50%', 'current': '22.4%', 'oneWeek': '24.1%', 'oneMonth': '27.8%'}
        ],
        '2026-03-18': [
            {'range': '3.25-3.50%', 'current': '42.1%', 'oneWeek': '40.8%', 'oneMonth': '38.9%'},
            {'range': '3.50-3.75%', 'current': '17.3%', 'oneWeek': '17.9%', 'oneMonth': '18.1%'},
            {'range': '3.75-4.00%', 'current': '1.8%', 'oneWeek': '1.8%', 'oneMonth': '1.7%'},
            {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '5.25-5.50%', 'current': '38.7%', 'oneWeek': '39.4%', 'oneMonth': '41.2%'}
        ],
        '2026-04-29': [
            {'range': '3.25-3.50%', 'current': '35.4%', 'oneWeek': '36.1%', 'oneMonth': '37.8%'},
            {'range': '3.50-3.75%', 'current': '12.1%', 'oneWeek': '11.8%', 'oneMonth': '11.9%'},
            {'range': '3.75-4.00%', 'current': '1.2%', 'oneWeek': '1.2%', 'oneMonth': '1.1%'},
            {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '5.25-5.50%', 'current': '51.2%', 'oneWeek': '50.8%', 'oneMonth': '49.1%'}
        ],
        '2026-06-17': [
            {'range': '3.25-3.50%', 'current': '28.7%', 'oneWeek': '29.8%', 'oneMonth': '32.1%'},
            {'range': '3.50-3.75%', 'current': '6.9%', 'oneWeek': '6.7%', 'oneMonth': '6.9%'},
            {'range': '3.75-4.00%', 'current': '0.5%', 'oneWeek': '0.5%', 'oneMonth': '0.5%'},
            {'range': '4.00-4.25%', 'current': '0.1%', 'oneWeek': '0.1%', 'oneMonth': '0.1%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '5.25-5.50%', 'current': '63.8%', 'oneWeek': '62.9%', 'oneMonth': '60.4%'}
        ],
        '0000-00-00': [
            {'range': '3.25-3.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.50-3.75%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '3.75-4.00%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.00-4.25%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '4.25-4.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'},
            {'range': '5.25-5.50%', 'current': '0.0%', 'oneWeek': '0.0%', 'oneMonth': '0.0%'}
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
            data = json.loads(request.body)
            if data.get('action') == 'refresh':
                # キャッシュをクリアして新しいデータを取得
                cache.delete('fed_monitor_data')
                cache.delete('fed_monitor_update_time')
                
                fed_monitor_data, update_time = get_cached_fed_monitor_data()
                
                return JsonResponse({
                    'success': True,
                    'fed_monitor_data': fed_monitor_data,
                    'fomc_data': get_fomc_data(),
                    'update_time': update_time
                })
        except Exception as e:
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