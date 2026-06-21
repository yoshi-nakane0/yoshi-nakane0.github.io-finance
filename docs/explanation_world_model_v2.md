# Explanation World Model v2

作成日: 2026-06-21

## 目的

Explanation は、Macro と Basecalc を統合し、現在の日経先物価格から `long` / `short` / `no_trade` のどれか一つを返す最終判断レイヤーである。

## 従来項目の意味

- `final_label`: Macro と Basecalc の大まかな統合ラベル。
- `final_stance`: 従来表示向けの姿勢ラベル。売買方向を一意に決める契約ではない。
- `long_judgment` / `short_judgment`: 旧表示では `final_stance` から作る参考表示だった。v2では `trade_decision` を優先し、採用側・非採用側・WATCHを分ける。

## v2の中核項目

- `trade_decision.selected_side`: `long` / `short` / `no_trade` の一意な最終判定。
- `trade_decision.decision_type`: `trend_follow`、`pullback`、`rally_sell`、`reversal_entry`、`no_chase_long`、`no_chase_short`、`no_trade_conflict`、`no_trade_data_blocked` などの行動分類。
- `target_1`、`stop_price`、`invalidation_price`、`reward_risk`: 採用サイドに紐づく売買計画。
- `long_score`、`short_score`、`no_trade_score`: Macro、Basecalc、監査、R/Rを反映した行動スコア。
- `reversal_watch`: 上昇中のショート逆張り候補、または下落中のロング逆張り候補。WATCHとENTRYを分ける。
- `blocked_reasons`: contract error、data quality不足、R/R不足、target不足などの見送り理由。

## 表示方針

ページ最上部は `trade_decision` を使い、最初に最終判定、現在値、エントリー条件、target、stop、無効化、R/R、信頼度、逆方向シナリオを表示する。Macro / Basecalc 詳細と従来の統合ラベルは下部の詳細表示に置く。

## 検証

`ExplanationTradeOutcome` は保存済みの `trade_decision` を 1d / 3d / 5d で評価する。評価対象は target hit、stop hit、direction hit、MFE、MAE、realized RR、confidence bucket、macro regime、technical regime である。
