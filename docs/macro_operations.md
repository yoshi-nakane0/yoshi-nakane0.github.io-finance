# Macro Operations

## 日次更新

1. GitHub Actions `macro-operations.yml` の日次ジョブが Vercel deploy hook を叩きます。
2. Vercel build で `build_files.sh` が動きます。
3. 本番はリポジトリに保存済みの `static/macro/*.json` を読みます。
4. build 中のデータ更新は標準では実行しません。

実行例:

```bash
python manage.py refresh_macro_data
python manage.py precompute_dashboard
```

## 週次検証

`weekly_macro_validation` は既存DBの最新データで軽い検証だけを行います。データ取得はしません。

```bash
python manage.py weekly_macro_validation
```

処理内容:

- World State 更新
- 期限到来予測の実績反映
- ダッシュボードキャッシュ更新

## 月次メンテナンス

月次メンテナンスは重い検証とモデル更新を含むため、Web画面からは実行しません。
GitHub Actions でも実行しません。
ローカルで生成した `static/macro/*.json` と `static/macro/*.csv` を commit / push し、
本番は保存済み結果だけを表示します。

```bash
scripts/run_macro_monthly_local_pipeline.sh
git diff
git commit -m "Update monthly macro model outputs"
git push
```

主な処理:

- 履歴アーカイブ
- 日次価格履歴同期
- World State バックフィル
- 急落確率モデル更新
- リターン予測モデル更新
- マクロ予測モデル更新
- walk-forward 検証
- 表示キャッシュ更新

学習依存がない環境では以下を使います。

```bash
python manage.py monthly_macro_maintenance --skip-lightgbm --skip-macro-forecast
```

GitHub Actions の `macro-operations.yml` は日次・週次の軽い処理だけを担当します。
月次のモデル学習、長い検証、全履歴バックテストはローカル実行に固定します。

## 必要な secrets

- `FRED_API_KEY`
- `DATABASE_URL`
- `VERCEL_DEPLOY_HOOK_URL`
- `SECRET_KEY` または `DJANGO_SECRET_KEY`

## よくある失敗

- `FRED_API_KEY` がない: 日次指標取得を skip または失敗として記録します。
- `DATABASE_URL` がない: 週次 workflow は skip します。
- `requirements-train.txt` が未導入: 学習コマンドは依存不足の `CommandError` を出します。
- Yahoo Finance 取得失敗: 価格同期の失敗として記録し、既存データは消しません。
- 月次成果物が古い: ローカルで `scripts/run_macro_monthly_local_pipeline.sh` を実行して、生成済みファイルを push します。

## 復旧手順

1. secrets を確認します。
2. `python manage.py migrate --noinput` を実行します。
3. `python manage.py refresh_macro_data` を再実行します。
4. `python manage.py compute_world_state` を実行します。
5. `python manage.py precompute_dashboard` を実行します。
