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

# 観測値入りの最新 db.sqlite3 を macro-data ブランチから取り込む（存在する場合のみ）。
# 存在しなければ同梱の db.sqlite3（指標定義のみ）をそのまま使う。
DATA_BRANCH="${MACRO_DATA_BRANCH:-macro-data}"
if git rev-parse --git-dir > /dev/null 2>&1; then
  if git fetch origin "$DATA_BRANCH" --depth=1 2>/dev/null; then
    if git cat-file -e "origin/${DATA_BRANCH}:db.sqlite3" 2>/dev/null; then
      git show "origin/${DATA_BRANCH}:db.sqlite3" > db.sqlite3
      echo "Loaded db.sqlite3 from origin/${DATA_BRANCH}"
    fi
  fi
fi

# DBマイグレーション
# DATABASE_URL がある場合は外部 DB へ、ない場合は同梱する SQLite を更新する。
SQLITE_DB_PATH="$PWD/db.sqlite3" $PYTHON_BIN manage.py migrate --noinput

# Earnings CSV を同梱 SQLite に取り込む（CSV を deploy 時の真実として再投入）
SQLITE_DB_PATH="$PWD/db.sqlite3" $PYTHON_BIN manage.py import_earnings_csv static/earning/data/data.csv

# Macro ページの重い計算結果を deploy 時に作り、アクセス時の 504 を防ぐ
SQLITE_DB_PATH="$PWD/db.sqlite3" $PYTHON_BIN manage.py precompute_dashboard

# 静的ファイルの収集
$PYTHON_BIN manage.py collectstatic --noinput --clear

# 重要: staticディレクトリを保持
mkdir -p staticfiles
cp -r static/* staticfiles/ 2>/dev/null || echo "No additional files to copy"

# トップページを静的HTMLとして生成
$PYTHON_BIN scripts/build_static_home.py
