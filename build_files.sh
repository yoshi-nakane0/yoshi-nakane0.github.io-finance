#!/bin/bash
set -e

# 環境変数を設定
export DJANGO_SETTINGS_MODULE=myproject.settings
export SQLITE_DB_PATH="${SQLITE_DB_PATH:-/tmp/db.sqlite3}"
export BUNDLED_SQLITE_PATH="${BUNDLED_SQLITE_PATH:-$PWD/runtime/db.sqlite3}"

# pip の準備とアップグレード
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN=python
fi
$PYTHON_BIN -m ensurepip
$PYTHON_BIN -m pip install --upgrade pip --no-warn-script-location

# 依存関係のインストール（本番向け）
$PYTHON_BIN -m pip install -r requirements-prod.txt --no-warn-script-location

if [ -z "${DATABASE_URL:-}" ]; then
  rm -f "$SQLITE_DB_PATH"
  mkdir -p "$(dirname "$SQLITE_DB_PATH")" "$(dirname "$BUNDLED_SQLITE_PATH")"
fi

# DBマイグレーション
# DATABASE_URL がある場合は外部 DB へ、ない場合は一時 SQLite を更新する。
$PYTHON_BIN manage.py migrate --noinput

# Earnings CSV を一時 SQLite に取り込む（CSV を deploy 時の真実として再投入）
$PYTHON_BIN manage.py import_earnings_csv static/earning/data/data.csv
if [ -f static/earning/data/eps_sales.csv ]; then
  $PYTHON_BIN manage.py import_eps_sales_csv static/earning/data/eps_sales.csv
fi

if [ -n "${FRED_API_KEY:-}" ]; then
  if ! $PYTHON_BIN manage.py refresh_macro_data; then
    echo "Macro data refresh failed; continuing with existing data"
  fi
else
  echo "FRED_API_KEY is not set; skipping macro data refresh"
fi

# Macro ページの重い計算結果を deploy 時に作り、アクセス時の 504 を防ぐ
$PYTHON_BIN manage.py precompute_dashboard

if [ -z "${DATABASE_URL:-}" ]; then
  cp "$SQLITE_DB_PATH" "$BUNDLED_SQLITE_PATH"
fi

# 静的ファイルの収集
$PYTHON_BIN manage.py collectstatic --noinput --clear

# 重要: staticディレクトリを保持
mkdir -p staticfiles
cp -r static/* staticfiles/ 2>/dev/null || echo "No additional files to copy"

# トップページを静的HTMLとして生成
$PYTHON_BIN scripts/build_static_home.py
