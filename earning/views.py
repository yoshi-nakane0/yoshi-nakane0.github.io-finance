import csv
from django.shortcuts import render
from django.conf import settings
import os
from datetime import datetime

def index(request):
    """
    CSVからデータを読み込み、日付でソート。下方修正にはCSSクラスを付与。
    """
    data_file_path = os.path.join(settings.STATIC_ROOT, 'earning/data/data.csv')
    earnings_data = []

    try:
        with open(data_file_path, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                eps_change = 0
                eps_forecast = row['eps2']
                eps_class = ""  # 追加: CSSクラス用の変数

                if row['near1'] == 'EPS上昇':
                    eps_change = 1
                elif row['near1'] == 'EPS下落':
                    eps_change = -1
                elif row['near1'] == '下方修正':
                    eps_change = 2
                    eps_class = "modified"  # クラス名を設定


                revenue_change = 0
                revenue_forecast = row['sales2']
                revenue_class = ""  # 追加: CSSクラス用の変数

                if row['near2'] == '売上上昇':
                    revenue_change = 1
                elif row['near2'] == '売上下落':
                    revenue_change = -1
                elif row['near2'] == '下方修正':
                    revenue_change = 2
                    revenue_class = "modified"  # クラス名を設定


                earnings_data.append({
                    'date': row['date'],
                    'company': row['company'],
                    'industry': row['industry'],
                    'eps': row['eps1'],
                    'eps_change': eps_change,
                    'eps_forecast': eps_forecast,
                    'eps_class': eps_class,  # クラス名をテンプレートに渡す
                    'revenue': row['sales1'],
                    'revenue_change': revenue_change,
                    'revenue_forecast': revenue_forecast,
                    'revenue_class': revenue_class,  # クラス名をテンプレートに渡す
                })

        earnings_data.sort(key=lambda item: datetime.strptime(item['date'], '%Y-%m-%d'), reverse=False)

    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Error reading CSV: {e}")
        pass

    return render(request, 'earning/index.html', {'earnings_data': earnings_data})