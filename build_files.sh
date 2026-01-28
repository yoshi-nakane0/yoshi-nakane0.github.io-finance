#!/bin/bash

# 環境変数を設定
export DJANGO_SETTINGS_MODULE=myproject.settings

# pip の準備とアップグレード
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN=python
fi
$PYTHON_BIN -m ensurepip
$PYTHON_BIN -m pip install --upgrade pip --no-warn-script-location

# 依存関係のインストール（本番向け）
$PYTHON_BIN -m pip install -r requirements-prod.txt --no-warn-script-location

# DBマイグレーション
$PYTHON_BIN manage.py migrate --noinput

# 静的ファイルの収集
$PYTHON_BIN manage.py collectstatic --noinput --clear

# 収集後のデバッグ用ログ
echo "Static files collected, checking staticfiles directory"
find staticfiles -type f | grep "\.css$"

# 重要: staticディレクトリを保持
cp -r static/* staticfiles/ 2>/dev/null || echo "No additional files to copy"

# トップページを静的HTMLとして生成
$PYTHON_BIN scripts/build_static_home.py
