# views.py
import csv
from django.shortcuts import render
from django.conf import settings
import os
from django.utils.text import Truncator  # 追加: 文字列を切り詰めるためのユーティリティ


def index(request):
    """
    CSVからプロンプトデータを読み込み、表示する
    """
    data_file_path = os.path.join(settings.STATIC_ROOT, 'prompt/data/prompt_data.csv')

    prompt_data = []

    try:
        with open(data_file_path, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                # カテゴリに対応する絵文字を決定 (必要に応じて追加・変更)
                category_emoji = ""
                if row['category'] == 'システムプロンプト':
                    category_emoji = '⚙️'
                elif row['category'] == '情報収集':
                    category_emoji = '🔍'
                elif row['category'] == '文章作成':
                    category_emoji = '📝'
                elif row['category'] == '仕事':
                    category_emoji = '💼'
                elif row['category'] == '医療':
                    category_emoji = '🏥'
                # 他のカテゴリも同様に追加

                # Truncatorを使ってjp_promptを150文字に制限し、省略記号を追加
                truncated_jp_prompt = Truncator(row['jp']).chars(150)
                prompt_data.append({
                    'category': row['category'],
                    'summary': row['summary'],
                    'jp_prompt': row['jp'],  # 元の全文を保持
                    'truncated_jp_prompt': truncated_jp_prompt, # 切り詰めたプロンプト
                    'en_prompt': row['en'],
                    'target_ai': row['ai'],
                    'category_emoji': category_emoji, # 絵文字を追加
                })
    except FileNotFoundError:
        print(f"Error: File not found at {data_file_path}")
        # 適切なエラーハンドリング。例えば、空のリストを渡す、エラーページを表示するなど。
    except KeyError as e:
        print(f"Error: Missing key in CSV: {e}")
        # キーが存在しない場合のエラーハンドリング
    except Exception as e:
        print(f"Error reading CSV: {e}")
        # その他の例外に対するエラーハンドリング

    return render(request, 'prompt/index.html', {'prompt_data': prompt_data})