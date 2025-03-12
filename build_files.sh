#!/bin/bash
# staticfilesディレクトリを作成
mkdir -p staticfiles
# manage.pyがあるディレクトリに移動
cd ./myproject
# 静的ファイルを収集
python manage.py collectstatic --noinput