#!/bin/bash
# インストール時に依存関係をインストール
pip install -r requirements.txt || pip3 install -r requirements.txt

# staticfilesディレクトリを作成
mkdir -p staticfiles

# 環境変数を出力してデバッグ
echo "PATH=$PATH"
echo "Finding Python..."
which python3 || which python || echo "Python not found"

python3 manage.py collectstatic --noinput || \
/opt/vercel/python3/bin/python3 manage.py collectstatic --noinput || \
/vercel/path0/.pythonpath/bin/python manage.py collectstatic --noinput