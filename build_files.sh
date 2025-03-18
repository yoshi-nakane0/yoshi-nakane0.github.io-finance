#!/bin/bash
# Vercel環境ではPythonとpipのパスを正確に指定
export PATH=$PATH:/opt/buildhome/.python3/bin

# パッケージのインストール
pip3 install -r requirements.txt

# 静的ファイルの収集
python3 manage.py collectstatic --noinput

# 静的ファイルのディレクトリ確認と移動
# プロジェクト名に合わせて調整が必要
if [ -d "myproject/static" ]; then
    mkdir -p staticfiles
    cp -r myproject/static/* staticfiles/
else
    echo "静的ファイルのディレクトリが見つかりません。パスを確認してください。"
fi