#!/bin/bash

# 静的ファイル用のディレクトリを作成
mkdir -p staticfiles/dashboard/css

# CSS ファイルを直接コピー
cp -r dashboard/static/dashboard/css/* staticfiles/dashboard/css/