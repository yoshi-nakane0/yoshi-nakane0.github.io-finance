# Macro World Model

## 定義

Macro World Model は、マクロ指標・金融環境・市場価格をまとめて、現在の経済状態を保存し、予測と後追い検証につなげる仕組みです。

## WorldStateSnapshot

`WorldStateSnapshot` は経済状態ベクトルです。主な点数は 0 から 100 で保存します。

- `growth_score`: 成長の強さ
- `labor_score`: 雇用の強さ
- `inflation_score`: 物価再加速リスク
- `policy_pressure_score`: 金融引き締め圧力
- `liquidity_score`: 流動性の良さ
- `credit_score`: 信用環境の安定度
- `risk_appetite_score`: リスク選好
- `market_trend_score`: 市場トレンド
- `market_stress_score`: 市場ストレス
- `data_quality`: 入力データの揃い方

## FeatureSnapshot

`FeatureSnapshot` は、予測に使った特徴量を保存するテーブルです。`ForecastSnapshot.metadata.feature_snapshot_id` から、どの特徴量で予測したかを追えます。

## 予測モデル

- `crash_probability_logistic_v1`: 急落確率モデル。日次価格がある場合は日次最大ドローダウンを優先します。
- `return_lightgbm_v2`: GSPC / IXIC / DJI / N225 のリターン参考予測です。
- `macro_forecast_lightgbm_v1`: 金利・物価・雇用などの変化幅の参考予測です。

## 表示上の違い

- ルール由来スコア: 現在の指標がどの局面に近いかを示す参考分布です。
- 検証済み予測確率: 過去データで検証したモデルの参考確率です。

## 制限

予測はすべて参考値です。売買推奨ではありません。データ不足、イベント数不足、モデル鮮度低下がある場合は信頼性が下がります。
