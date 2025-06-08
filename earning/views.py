import csv
from django.shortcuts import render
from django.conf import settings
import os
from datetime import datetime

def index(request):
    """
    CSVからデータを読み込み、日付でソート。
    表示列は「決算日」「企業」「業種」のみ。
    """
    data_file_path = os.path.join(settings.STATIC_ROOT, 'earning/data/data.csv')
    earnings_data = []

    try:
        with open(data_file_path, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                # 必要な項目のみを辞書に追加
                earnings_data.append({
                    'date': row['date'],
                    'company': row['company'],
                    'industry': row['industry'],
                    # テンプレートのリンク用
                    'market': row.get('market', ''),
                    'symbol': row.get('symbol', ''),
                })
        # 日付順にソート
        earnings_data.sort(key=lambda x: datetime.strptime(x['date'], '%Y-%m-%d'))

    except FileNotFoundError:
        # ファイルがない場合は空のデータで表示
        pass
    except Exception as e:
        print(f"Error reading CSV: {e}")

    return render(request, 'earning/index.html', {'earnings_data': earnings_data})