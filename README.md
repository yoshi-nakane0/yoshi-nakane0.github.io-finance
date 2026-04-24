
1. TTM集計（実務寄り・推奨）

- 方法: 構成銘柄の直近12か月（TTM）利益を集計して指数EPSを推計。
- 価格依存: なし。
- 更新頻度: 四半期ごと。
- 特徴: 価格が上下してもレンジは固定され、利益更新時だけ動く。
- 実現性: 公開決算（EDINET等）から機械的に集計可能。初期コストはあるが個人でも構築可能。

---

## TradingView チャート自動保存

GitHub Actions を使って TradingView の日経225チャートを定期的にスクリーンショットし、Dropboxに保存する仕組みです。

### 実行スケジュール

毎日 JST 07:00 / 17:00 / 22:00 に自動実行されます（1日3回）。
手動実行もGitHub Actionsの画面から可能です。

### 保存先・ファイル構成

Dropbox の `/TradingView/YYYY-MM-DD/` フォルダに以下4枚のPNGが保存されます。

- `1h_HHMM.png` — 1時間足
- `4h_HHMM.png` — 4時間足
- `1D_HHMM.png` — 日足
- `1W_HHMM.png` — 週足

`HHMM` は実行時のJST時刻（例: 17:00実行なら `1700`）。

### 保存期間

**3日間**（直近3日分のフォルダのみ保持）。古いフォルダは実行のたびに自動削除されます。
長期保存したい場合は、手動でDropboxの別フォルダにコピーしてください。

### セッション切れ時の対応

TradingViewのログインセッション（クッキー）は数ヶ月〜1年程度で失効することがあります。
失効するとGitHub Actionsがエラーで失敗します。その場合は以下の手順で再ログインしてください。

1. `scripts/capture_charts/` に移動し `npm install`
2. `node login.js` を実行（ローカルのブラウザが開く）
3. TradingViewに手動ログイン → チャートが表示されたらブラウザを閉じる
4. 生成された `storageState.json` の中身をコピー
5. GitHub Secretsの `TRADINGVIEW_STORAGE_STATE` を新しい値で上書き更新

### 使用しているGitHub Secrets

- `TRADINGVIEW_STORAGE_STATE` — ログイン済みセッション情報
- `APP_KEY` / `APP_SECRET` — Dropboxアプリの認証情報
- `DROPBOX_REFRESH_TOKEN` — Dropboxリフレッシュトークン

### 注意事項

- TradingViewのチャートURLは `scripts/capture_charts/capture.js` の `CHART_URL` で指定（現在は日経225/SPREADEX:NIKKEI）
- Dropboxの無料プラン容量は2GB。保存期間3日なら十分収まりますが、手動で長期保存したファイルが増えると圧迫される可能性があります
- GitHub Actionsのログや実行履歴は公開されます（publicリポジトリのため）
