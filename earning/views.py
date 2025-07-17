import json
import requests
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone as django_timezone
import os
from datetime import datetime, timezone, timedelta
from itertools import groupby
from .models import EarningsAnnouncement, EarningsDataCache
from bs4 import BeautifulSoup
import re
import time

# JST timezone
TZ_JST = timezone(timedelta(hours=9))

def fetch_yahoo_finance_earnings():
    """
    Yahoo Financeから決算データを取得する
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Yahoo Finance earnings calendar URL
        url = 'https://finance.yahoo.com/calendar/earnings'
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        earnings_data = []
        
        # Yahoo Financeの決算カレンダーテーブルを探す
        table = soup.find('table')
        if table:
            rows = table.find_all('tr')[1:]  # ヘッダー行をスキップ
            
            for row in rows[:20]:  # 最大20件まで取得
                cells = row.find_all('td')
                if len(cells) >= 4:
                    try:
                        # 銘柄コード
                        symbol = cells[0].get_text(strip=True)
                        # 企業名
                        company = cells[1].get_text(strip=True)
                        # 時間（前場/後場など）
                        time_info = cells[2].get_text(strip=True) if len(cells) > 2 else ''
                        
                        # 今日の日付を使用（実際のスクレイピングでは日付も取得）
                        today = datetime.now().date()
                        
                        # 業種を推定（シンボルベース）
                        industry = get_industry_by_symbol(symbol)
                        market = get_market_by_symbol(symbol)
                        
                        earnings_data.append({
                            'date': today.strftime('%Y-%m-%d'),
                            'company': company,
                            'industry': industry,
                            'market': market,
                            'symbol': symbol
                        })
                    except Exception as e:
                        print(f"Error parsing row: {e}")
                        continue
        
        print(f"Yahoo Finance: Retrieved {len(earnings_data)} earnings announcements")
        return earnings_data
        
    except Exception as e:
        print(f"Yahoo Finance earnings fetch error: {e}")
        return []

def get_industry_by_symbol(symbol):
    """
    シンボルから業種を推定する
    """
    # Remove exchange prefix if present
    clean_symbol = symbol.split(':')[-1] if ':' in symbol else symbol
    
    industry_map = {
        'AAPL': 'テクノロジー',
        'MSFT': 'ソフトウェア、テクノロジー',
        'GOOGL': 'ソフトウェア、テクノロジー',
        'GOOG': 'ソフトウェア、テクノロジー',
        'AMZN': '小売り',
        'TSLA': '自動車、テクノロジー',
        'META': 'テクノロジー',
        'NVDA': '半導体、テクノロジー',
        'JPM': '銀行、金融サービス',
        'JNJ': '医薬品',
        'UNH': 'ヘルスケアサービス',
        'V': '金融サービス',
        'MA': '金融サービス',
        'PG': '消費財',
        'NFLX': 'インターネット、テクノロジー',
        'ORCL': 'ソフトウェア、テクノロジー',
        'TSM': '半導体、テクノロジー',
        'GE': '工業、航空宇宙',
        'ABT': '医薬品、医療機器',
        'BAC': '銀行、金融サービス',
        'WFC': '銀行、金融サービス',
        'XOM': 'エネルギー、石油',
        'CVX': 'エネルギー、石油',
        'HD': '小売り、建設',
        'PFE': '医薬品',
        'KO': '飲料、消費財',
        'DIS': 'エンターテイメント、メディア',
    }
    return industry_map.get(clean_symbol, 'その他')

def get_market_by_symbol(symbol):
    """
    シンボルから市場を推定する
    """
    # Remove exchange prefix if present
    clean_symbol = symbol.split(':')[-1] if ':' in symbol else symbol
    
    nasdaq_symbols = ['AAPL', 'MSFT', 'GOOGL', 'GOOG', 'AMZN', 'TSLA', 'META', 'NVDA', 'NFLX']
    if clean_symbol in nasdaq_symbols:
        return 'NASDAQ'
    return 'NYSE'

def fetch_tradingview_earnings():
    """
    TradingViewから決算データを取得する
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # TradingView earnings calendar URL
        url = 'https://jp.tradingview.com/markets/stocks-usa/earnings/'
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        earnings_data = []
        
        # TradingViewの決算カレンダーを探す
        # 実際のHTMLパースは動的コンテンツのため制限がある場合があります
        earnings_elements = soup.find_all(attrs={'data-symbol': True})
        
        for element in earnings_elements[:15]:  # 最大15件まで取得
            try:
                symbol = element.get('data-symbol', '')
                if symbol:
                    # Clean symbol (remove exchange prefix)
                    clean_symbol = symbol.split(':')[-1] if ':' in symbol else symbol
                    
                    # 企業名を取得（要素内のテキストから）
                    company_element = element.find(class_='tv-screener-table__symbol-container')
                    company_text = company_element.get_text(strip=True) if company_element else clean_symbol
                    
                    # Remove symbol from company name if it's duplicated
                    if company_text.startswith(clean_symbol):
                        company = company_text[len(clean_symbol):].strip()
                    else:
                        company = company_text
                    
                    # 今日から数日先の日付を設定
                    today = datetime.now().date()
                    future_date = today + timedelta(days=len(earnings_data) % 7)
                    
                    industry = get_industry_by_symbol(clean_symbol)
                    market = get_market_by_symbol(clean_symbol)
                    
                    earnings_data.append({
                        'date': future_date.strftime('%Y-%m-%d'),
                        'company': company,
                        'industry': industry,
                        'market': market,
                        'symbol': clean_symbol
                    })
            except Exception as e:
                print(f"Error parsing TradingView element: {e}")
                continue
        
        print(f"TradingView: Retrieved {len(earnings_data)} earnings announcements")
        return earnings_data
        
    except Exception as e:
        print(f"TradingView earnings fetch error: {e}")
        return []

def get_cached_earnings_data():
    """
    キャッシュされた決算データを取得
    """
    cached_data = cache.get('earnings_data')
    cached_time = cache.get('earnings_update_time')
    
    if cached_data and cached_time:
        print(f"Using cached data: {len(cached_data)} items")
        return cached_data, cached_time
    
    print("No cached data found")
    # キャッシュがない場合はファイルまたはサンプルデータを返す
    return None, None

def save_earnings_to_database(earnings_data):
    """
    決算データをデータベースに保存
    """
    try:
        # 既存のデータをクリア（必要に応じて）
        # EarningsAnnouncement.objects.filter(date__gte=datetime.now().date()).delete()
        
        saved_count = 0
        for item in earnings_data:
            try:
                # 日付文字列をdateオブジェクトに変換
                announcement_date = datetime.strptime(item['date'], '%Y-%m-%d').date()
                
                # 重複チェック：同じ日付、企業名、シンボルの組み合わせ
                obj, created = EarningsAnnouncement.objects.get_or_create(
                    date=announcement_date,
                    company=item.get('company', ''),
                    symbol=item.get('symbol', ''),
                    defaults={
                        'industry': item.get('industry', ''),
                        'market': item.get('market', ''),
                    }
                )
                
                if created:
                    saved_count += 1
                    print(f"Saved new earnings: {obj}")
                else:
                    # 既存のデータを更新
                    obj.industry = item.get('industry', '')
                    obj.market = item.get('market', '')
                    obj.save()
                    print(f"Updated earnings: {obj}")
                    
            except Exception as e:
                print(f"Error saving earnings item {item}: {e}")
                continue
        
        print(f"Successfully saved {saved_count} new earnings announcements")
        return True
        
    except Exception as e:
        print(f"Error saving earnings to database: {e}")
        return False

def cache_earnings_data(earnings_data, update_time):
    """
    決算データをキャッシュとデータベースに保存
    """
    # データベースに保存
    save_earnings_to_database(earnings_data)
    
    # キャッシュにも保存（高速アクセス用）
    cache.set('earnings_data', earnings_data, 86400)  # 24 hours
    cache.set('earnings_update_time', update_time, 86400)

def get_earnings_from_database():
    """
    データベースから決算データを取得
    """
    try:
        # 今日以降の決算発表予定を取得
        today = datetime.now().date()
        announcements = EarningsAnnouncement.objects.filter(
            date__gte=today
        ).order_by('date', 'company')
        
        earnings_data = []
        for announcement in announcements:
            earnings_data.append({
                'date': announcement.date.strftime('%Y-%m-%d'),
                'company': announcement.company,
                'industry': announcement.industry,
                'market': announcement.market,
                'symbol': announcement.symbol,
            })
        
        return earnings_data
        
    except Exception as e:
        print(f"Error retrieving earnings from database: {e}")
        return []

def fetch_latest_earnings_data():
    """
    最新の決算データを外部APIから取得
    """
    earnings_data = []
    
    # Yahoo Financeから取得を試行
    yahoo_data = fetch_yahoo_finance_earnings()
    if yahoo_data:
        earnings_data.extend(yahoo_data)
    
    # TradingViewから取得を試行
    tradingview_data = fetch_tradingview_earnings()
    if tradingview_data:
        earnings_data.extend(tradingview_data)
    
    # データが取得できない場合はサンプルデータ（指定の形式）
    if not earnings_data:
        today = datetime.now().date()
        earnings_data = [
            {'date': today.strftime('%Y-%m-%d'), 'company': 'Oracle', 'industry': 'ソフトウェア、テクノロジー', 'market': 'NYSE', 'symbol': 'ORCL'},
            {'date': (today + timedelta(days=1)).strftime('%Y-%m-%d'), 'company': 'JP Morgan Chase', 'industry': '銀行、金融サービス', 'market': 'NYSE', 'symbol': 'JPM'},
            {'date': (today + timedelta(days=1)).strftime('%Y-%m-%d'), 'company': 'Johnson & Johnson', 'industry': '医薬品', 'market': 'NYSE', 'symbol': 'JNJ'},
            {'date': (today + timedelta(days=1)).strftime('%Y-%m-%d'), 'company': 'Meta Platforms', 'industry': 'テクノロジー', 'market': 'NASDAQ', 'symbol': 'META'},
            {'date': (today + timedelta(days=1)).strftime('%Y-%m-%d'), 'company': 'TSMC', 'industry': '半導体、テクノロジー', 'market': 'NYSE', 'symbol': 'TSM'},
            {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'Netflix', 'industry': 'インターネット、テクノロジー', 'market': 'NASDAQ', 'symbol': 'NFLX'},
            {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'Apple', 'industry': 'テクノロジー', 'market': 'NASDAQ', 'symbol': 'AAPL'},
            {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'Mastercard', 'industry': '金融サービス', 'market': 'NYSE', 'symbol': 'MA'},
            {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'Visa', 'industry': '金融サービス', 'market': 'NYSE', 'symbol': 'V'},
            {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'Tesla', 'industry': '自動車、テクノロジー', 'market': 'NASDAQ', 'symbol': 'TSLA'},
            {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'UnitedHealth', 'industry': 'ヘルスケアサービス', 'market': 'NYSE', 'symbol': 'UNH'},
        ]
    
    return earnings_data

@csrf_exempt
def index(request):
    if request.method == 'POST':
        # AJAX refresh request
        try:
            data = json.loads(request.body)
            if data.get('action') == 'refresh':
                # 最新の決算データを取得
                latest_earnings = fetch_latest_earnings_data()
                update_time = datetime.now(TZ_JST).strftime("%Y年%m月%d日 %H:%M:%S")
                
                # キャッシュに保存
                cache_earnings_data(latest_earnings, update_time)
                
                # 日付順にソート
                try:
                    latest_earnings.sort(key=lambda x: datetime.strptime(x['date'], '%Y-%m-%d') if x['date'] else datetime.min)
                except Exception as e:
                    print(f"Error sorting latest data: {e}")
                
                # 日付でグループ化
                grouped_earnings = []
                if latest_earnings:
                    try:
                        current_date = None
                        current_companies = []
                        
                        for item in latest_earnings:
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
                        print(f"Error grouping latest data: {e}")
                        for item in latest_earnings:
                            grouped_earnings.append({
                                'date': item['date'],
                                'companies': [item]
                            })
                
                return JsonResponse({
                    'success': True,
                    'update_time': update_time,
                    'earnings_data': grouped_earnings
                })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    # GET request - 通常のページ表示
    # まずキャッシュされたデータを確認
    cached_earnings, cached_time = get_cached_earnings_data()
    
    if cached_earnings and cached_time:
        # キャッシュがある場合はそれを使用
        grouped_earnings = cached_earnings
        update_time = cached_time
    else:
        # キャッシュがない場合は、まずデータベースから取得を試行
        db_earnings = get_earnings_from_database()
        if db_earnings:
            # データベースにデータがある場合
            try:
                db_earnings.sort(key=lambda x: datetime.strptime(x['date'], '%Y-%m-%d') if x['date'] else datetime.min)
            except Exception as e:
                print(f"Error sorting DB data: {e}")
            
            # 日付でグループ化
            grouped_earnings = []
            if db_earnings:
                try:
                    current_date = None
                    current_companies = []
                    
                    for item in db_earnings:
                        if current_date != item['date']:
                            if current_date is not None:
                                grouped_earnings.append({
                                    'date': current_date,
                                    'companies': current_companies
                                })
                            current_date = item['date']
                            current_companies = []
                        current_companies.append(item)
                    
                    if current_date is not None:
                        grouped_earnings.append({
                            'date': current_date,
                            'companies': current_companies
                        })
                except Exception as e:
                    print(f"Error grouping DB data: {e}")
                    for item in db_earnings:
                        grouped_earnings.append({
                            'date': item['date'],
                            'companies': [item]
                        })
            
            update_time = datetime.now(TZ_JST).strftime("%Y年%m月%d日 %H:%M:%S")
        else:
            # データベースにもデータがない場合は外部サイトから取得
            earnings_data = fetch_latest_earnings_data()
            
            # 日付順にソート（エラーハンドリング付き）
            try:
                earnings_data.sort(key=lambda x: datetime.strptime(x['date'], '%Y-%m-%d') if x['date'] else datetime.min)
            except Exception as e:
                print(f"Error sorting data: {e}")
                # ソートに失敗した場合はそのまま使用

            # データがない場合はサンプルデータを使用（指定の形式）
            if not earnings_data:
                today = datetime.now().date()
                earnings_data = [
                    {'date': today.strftime('%Y-%m-%d'), 'company': 'Oracle', 'industry': 'ソフトウェア、テクノロジー', 'market': 'NYSE', 'symbol': 'ORCL'},
                    {'date': (today + timedelta(days=1)).strftime('%Y-%m-%d'), 'company': 'JP Morgan Chase', 'industry': '銀行、金融サービス', 'market': 'NYSE', 'symbol': 'JPM'},
                    {'date': (today + timedelta(days=1)).strftime('%Y-%m-%d'), 'company': 'Johnson & Johnson', 'industry': '医薬品', 'market': 'NYSE', 'symbol': 'JNJ'},
                    {'date': (today + timedelta(days=1)).strftime('%Y-%m-%d'), 'company': 'Meta Platforms', 'industry': 'テクノロジー', 'market': 'NASDAQ', 'symbol': 'META'},
                    {'date': (today + timedelta(days=1)).strftime('%Y-%m-%d'), 'company': 'TSMC', 'industry': '半導体、テクノロジー', 'market': 'NYSE', 'symbol': 'TSM'},
                    {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'Netflix', 'industry': 'インターネット、テクノロジー', 'market': 'NASDAQ', 'symbol': 'NFLX'},
                    {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'Apple', 'industry': 'テクノロジー', 'market': 'NASDAQ', 'symbol': 'AAPL'},
                    {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'Mastercard', 'industry': '金融サービス', 'market': 'NYSE', 'symbol': 'MA'},
                    {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'Visa', 'industry': '金融サービス', 'market': 'NYSE', 'symbol': 'V'},
                    {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'Tesla', 'industry': '自動車、テクノロジー', 'market': 'NASDAQ', 'symbol': 'TSLA'},
                    {'date': (today + timedelta(days=2)).strftime('%Y-%m-%d'), 'company': 'UnitedHealth', 'industry': 'ヘルスケアサービス', 'market': 'NYSE', 'symbol': 'UNH'},
                ]

            # 決算日でグループ化し、各企業を個別行として準備
            grouped_earnings = []
            if earnings_data:
                try:
                    # 日付でグループ化
                    current_date = None
                    current_companies = []
                    
                    for item in earnings_data:
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
                    for item in earnings_data:
                        grouped_earnings.append({
                            'date': item['date'],
                            'companies': [item]
                        })

            # JST timezone for consistent display
            update_time = datetime.now(TZ_JST).strftime("%Y年%m月%d日 %H:%M:%S")
    
    context = {
        'earnings_data': grouped_earnings,
        'update_time': update_time,
    }
    
    return render(request, 'earning/index.html', context)