#!/bin/bash

# 環境変数を設定
export DJANGO_SETTINGS_MODULE=myproject.settings

# pip の準備とアップグレード
python3.9 -m ensurepip
python3.9 -m pip install --upgrade pip --no-warn-script-location

# 依存関係のインストール
python3.9 -m pip install -r requirements.txt --no-warn-script-location

# 静的ファイルの収集前にディレクトリを確認
echo "Checking static directory before collectstatic"
ls -la static/ || echo "Error: static/ directory not found"
ls -la static/dashboard/css/ || echo "Error: static/dashboard/css/ directory not found"

# 静的ファイルの収集
python3.9 manage.py collectstatic --noinput --clear

# 収集後のデバッグ用ログ
echo "Static files collected, checking staticfiles directory"
ls -la staticfiles/ || echo "Error: staticfiles/ directory not found"
ls -la staticfiles/dashboard/css/ || echo "Error: staticfiles/dashboard/css/ directory not found"