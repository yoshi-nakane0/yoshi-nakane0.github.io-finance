#!/bin/bash

# pip の準備とアップグレード
python3.9 -m ensurepip
python3.9 -m pip install --upgrade pip --no-warn-script-location

# 依存関係のインストール
python3.9 -m pip install -r requirements.txt --no-warn-script-location

# 静的ファイルの収集
python3.9 manage.py collectstatic --noinput --clear

# デバッグ用ログ
echo "Static files collected, checking staticfiles directory"
ls -la staticfiles/