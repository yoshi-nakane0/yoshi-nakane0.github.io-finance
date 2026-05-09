---
name: update-events
description: Use when ユーザーが Events ページ（経済カレンダー）の月次データを更新したいとき。「Eventsを更新して」「イベントを更新」「Eventsページ更新」「カレンダー更新」「経済イベント更新」等の自然言語フレーズで起動。Forex Factory から指定月の経済イベントをスクレイピングし、event 列を日本語訳に置き換えて static/events/data.csv に保存する月次運用作業を半自動化する。
---

# update-events

## 概要

Events ページ（`/events/`）が表示する経済カレンダーの元データ `static/events/data.csv` を月次更新するためのスキル。Forex Factory のスクレイピング → event 列の日本語化 → 同 CSV に上書き保存、を一連の流れで実施する。

ローカル管理者が手動操作で実行する想定。Vercel デプロイ環境では Selenium / Chrome が動かないため絶対に動作しない。

## 起動トリガ

- 「Eventsを更新して」
- 「イベントを更新」
- 「Eventsページを更新」
- 「カレンダーを更新」
- 「経済イベント更新」
- これらに類似する月次更新を依頼するフレーズ全般

## 関連ファイル

- スクリプト: `/Users/naka/yoshi-nakane0.github.io-finance/scripts/schedule.py`
- 出力先 CSV（スクリプトが直接書き込む）: `/Users/naka/yoshi-nakane0.github.io-finance/static/events/data.csv`
- 表示ロジック: `/Users/naka/yoshi-nakane0.github.io-finance/events/views.py`

## 手順

### Step 1. 対象月をユーザーに確認

ユーザーに「取得する月（1〜12）をカンマ区切りで入力してください。例: 5,6」と尋ねる。

入力値の検証:
- カンマ区切りの 1〜12 の数字のみ
- 不正な値（文字列、範囲外、空）は再入力を促す
- 未来月のみ取得するのが通常運用（過去月は既に翻訳済みのため上書きすると消える）。ユーザーが過去月を指定したら「過去月の翻訳済みデータが上書きされますが続行しますか？」と確認する

### Step 2. スクリプトを実行

検証済みの入力値を stdin に流して実行する。実行は数分かかる（月数 × 約1〜2分）ので Bash ツールの timeout を 600000 ms（10分）に設定する。

実行コマンド例:

    echo "5,6" | python /Users/naka/yoshi-nakane0.github.io-finance/scripts/schedule.py

成功条件: 標準出力に `CSV に出力しました → /Users/naka/.../static/events/data.csv` と `取得件数: N 件` が現れる。

失敗時:
- `対象通貨のイベントが取得できませんでした` → スクレイピング失敗。ユーザーに報告。
- Selenium/Chrome 起動エラー → 環境問題。ユーザーに報告して終了。

### Step 3. CSV を読み込み翻訳

`/Users/naka/yoshi-nakane0.github.io-finance/static/events/data.csv` を Read で読み込む。ヘッダ行は `"date","time","currency","event","impact"` で固定。

`event` 列（4列目）の英語表記を日本語に翻訳する。`date`, `time`, `currency`, `impact` 列は **絶対に変更しない**。

### 翻訳ルール

1. **既存訳の踏襲**: data.csv 内に既に同じ英語イベントが翻訳済みであれば必ずその訳語を使う。同一イベントは月をまたいで頻出するため、表記揺れを防ぐ。

2. **代表的な訳例**:
   - `Unemployment Rate` → `失業率`
   - `Tankan Manufacturing Index` → `日銀短観 製造業景況感指数`
   - `Tankan Non-Manufacturing Index` → `日銀短観 非製造業景況感指数`
   - `Final Manufacturing PMI` → `製造業PMI（確報値）`
   - `ISM Manufacturing PMI` → `ISM製造業景況感指数`
   - `ISM Manufacturing Prices` → `ISM製造業価格指数`
   - `JOLTS Job Openings` → `JOLTS求人件数`
   - `Construction Spending m/m` → `建設支出（前月比）`
   - `Wards Total Vehicle Sales` → `ワーズ自動車販売台数`
   - `RCM/TIPP Economic Optimism` → `RCM/TIPP経済楽観指数`
   - `API Weekly Statistical Bulletin` → `API週次石油統計`
   - `Monetary Base y/y` → `マネタリーベース（前年比）`
   - `ADP Non-Farm Employment Change` → `ADP非農業部門雇用者数変化`
   - `Factory Orders m/m` → `製造業新規受注（前月比）`
   - `Crude Oil Inventories` → `原油在庫量`

3. **発言系（Speaks）**:
   - `FOMC Member <名前> Speaks` → `FOMCメンバー <名前カナ> 発言`
   - `<肩書> <名前> Speaks` → `<肩書日本語> <名前カナ> 発言`
   - 例: `FOMC Member Barkin Speaks` → `FOMCメンバー バーキン発言`
   - 例: `Trump Speaks` → `トランプ大統領発言`

4. **頻度サフィックスの統一**:
   - `m/m` → `（前月比）`
   - `y/y` → `（前年比）`
   - `q/q` → `（前期比）`

5. **略語/固有名は原則維持**: `PMI`, `ISM`, `JOLTS`, `ADP`, `FOMC`, `GDP`, `CPI`, `PPI`, `API`, `RCM/TIPP` など金融慣例で英字のまま使うものは英字を残す。

6. **判別不能な場合**: 既存 data.csv にも訳例集にも対応がなく、訳が曖昧な場合はユーザーに確認する。勝手に推測しない。

### Step 4. 翻訳済み CSV を保存

同じパス `/Users/naka/yoshi-nakane0.github.io-finance/static/events/data.csv` に Write で上書き保存する。

CSV の形式維持:
- ヘッダ行: `"date","time","currency","event","impact"`（先頭固定）
- 値はダブルクォート付き
- 改行コードは LF（既存と同じ）
- 文字コードは UTF-8

### Step 5. 結果報告

簡潔に以下を報告する:
- 取得件数（スクリプト出力から）
- 翻訳件数
- 保存先パス
- 既存訳を踏襲した件数 / 新規翻訳した件数（任意）

## 制約と注意

- 本スキルはユーザーから明示的に呼ばれた場合のみ実行する。自動実行や定期実行はしない。
- スクリプトはローカル環境（macOS + Chrome）でのみ動作する。Vercel 等のサーバ環境では実行しない。
- `data.csv` は **スクリプト実行時に英語版で一旦上書きされる** ため、Step 2 と Step 4 の間で Events ページを開くと一時的に英語表示になる。これは仕様。
- スクリプト内の `FIXED_YEAR` は固定値。年をまたぐ更新が必要な場合はスクリプト本体の修正が必要なのでユーザーに知らせる。
