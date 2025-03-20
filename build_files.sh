#!/bin/bash

# 環境変数を設定
export DJANGO_SETTINGS_MODULE=myproject.settings

# pip の準備とアップグレード
python3.9 -m ensurepip
python3.9 -m pip install --upgrade pip --no-warn-script-location

# 依存関係のインストール
python3.9 -m pip install -r requirements.txt --no-warn-script-location



# 静的ファイルの収集
python3.9 manage.py collectstatic --noinput --clear

# 収集後のデバッグ用ログ
echo "Static files collected, checking staticfiles directory"
find staticfiles -type f | grep "\.css$"

# 重要: staticディレクトリを保持
cp -r static/* staticfiles/ 2>/dev/null || echo "No additional files to copy"
