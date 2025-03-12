#!/bin/bash
# staticfilesディレクトリを作成
mkdir -p staticfiles
# 静的ファイルを収集
python manage.py collectstatic --noinput