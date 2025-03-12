#!/bin/bash
# staticfilesディレクトリを作成
mkdir -p staticfiles
# ルートディレクトリにいることを確認（cd不要）
# 静的ファイルを収集
python3 manage.py collectstatic --noinput || python manage.py collectstatic --noinput