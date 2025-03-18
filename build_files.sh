#!/bin/bash

# pip の準備とアップグレード
python3.9 -m ensurepip
python3.9 -m pip install --upgrade pip --no-warn-script-location

# 依存関係のインストール
python3.9 -m pip install -r requirements.txt --no-warn-script-location

# 静的ファイルの収集
python3.9 manage.py collectstatic --noinput

# 静的ファイルを staticfiles にコピー（冗長だが一貫性を保つ）
mkdir -p staticfiles
cp -r static/* staticfiles/