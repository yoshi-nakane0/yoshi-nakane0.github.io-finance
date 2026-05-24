"""LightGBM クラッシュ予測モデルの学習＋推論。

ローカル/学習環境のみで実行する Management Command。
依存: requirements-train.txt（lightgbm, numpy, pandas）

使い方:
    python manage.py train_crash_model

出力:
    static/macro/lightgbm_prediction.json

このコマンドは Vercel 本番では実行されない（依存を含めていないため）。
本番側は出力済みの JSON を読むだけ。
"""

import json
import hashlib
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from macro.models import ForecastSnapshot, Indicator, Observation, PriceObservation

logger = logging.getLogger(__name__)


HORIZONS_MONTHS = [1, 3]
RECENT_VALIDATION_MONTHS = 24
MIN_TRAINING_SAMPLES = 60
# 学習に使うには指標ごとにこの月数以上の観測値が必要
MIN_OBSERVATIONS_PER_FEATURE = 60
# 学習対象に含める指標は、これ以前のデータを持っていることを必須とする
FEATURE_START_DATE_REQUIRED = date(2014, 1, 1)
OUTPUT_RELATIVE_PATH = Path('static') / 'macro' / 'lightgbm_prediction.json'
MODEL_VERSION = 'v1'


class Command(BaseCommand):
    help = 'FRED指標で LightGBM を学習し、SP500 の 1ヶ月後・3ヶ月後リターン予測を JSON 出力する'

    def handle(self, *args, **options):
        try:
            import lightgbm as lgb  # noqa: F401
            import numpy as np
            import pandas as pd
        except ImportError as exc:
            raise CommandError(
                f"学習用依存が見つかりません: {exc}. "
                "`pip install -r requirements-train.txt` を実行してください。"
            )

        feature_indicators = self._load_feature_indicators()
        if not feature_indicators:
            raise CommandError("学習に使う指標が見つかりません。")

        self.stdout.write(f'特徴量指標: {len(feature_indicators)} 系列')

        sp500 = self._load_sp500_monthly()
        if not sp500:
            raise CommandError("SP500 月次データが空です。yfinance同期を先に実行してください。")

        feature_df = self._build_feature_matrix(feature_indicators)
        if feature_df.empty:
            raise CommandError("特徴量行列が空です。データを取得してから再実行してください。")

        self.stdout.write(
            f'特徴量行列: {feature_df.shape[0]} 月 × {feature_df.shape[1]} 列'
        )

        results: List[Dict] = []
        latest_features = None
        for h in HORIZONS_MONTHS:
            x_train, y_train, x_valid, y_valid, latest_x = self._build_xy(
                feature_df, sp500, h
            )
            if len(y_train) < MIN_TRAINING_SAMPLES:
                self.stdout.write(self.style.WARNING(
                    f'{h}ヶ月先: 訓練サンプル {len(y_train)} 件のみで MIN={MIN_TRAINING_SAMPLES} 未満。スキップ。'
                ))
                continue
            pred, mae = self._train_and_predict(x_train, y_train, x_valid, y_valid, latest_x)
            results.append({
                'months': h,
                'predicted_return_pct': round(float(pred) * 100.0, 2),
                'validation_mae_pct': round(float(mae) * 100.0, 2),
            })
            latest_features = latest_x

        if not results:
            raise CommandError("どのホライズンでも学習できませんでした。データ量を確認してください。")

        payload = {
            'predicted_at': timezone.localdate().isoformat(),
            'horizons': results,
            'training_samples': feature_df.shape[0] - RECENT_VALIDATION_MONTHS,
            'feature_count': feature_df.shape[1],
            'model_version': MODEL_VERSION,
        }

        out_path = Path(settings.BASE_DIR) / OUTPUT_RELATIVE_PATH
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

        latest_feature_payload = {
            key: float(value)
            for key, value in feature_df.iloc[-1].to_dict().items()
        }
        features_hash = hashlib.sha256(
            json.dumps(
                latest_feature_payload,
                sort_keys=True,
                separators=(',', ':'),
            ).encode('utf-8')
        ).hexdigest()
        as_of_date = timezone.localdate()
        for result in results:
            ForecastSnapshot.objects.update_or_create(
                as_of_date=as_of_date,
                model_version=f'lightgbm_return_{MODEL_VERSION}',
                target='GSPC',
                horizon=f"{result['months']}m",
                defaults={
                    'prediction_value': result['predicted_return_pct'],
                    'prediction_interval': {
                        'type': 'validation_mae_pct',
                        'mae_pct': result['validation_mae_pct'],
                    },
                    'features_hash': features_hash,
                    'metadata': {
                        'prediction_kind': 'return_pct',
                        'horizon_months': result['months'],
                        'validation_mae_pct': result['validation_mae_pct'],
                    },
                },
            )

        self.stdout.write(self.style.SUCCESS(
            f'予測 JSON 書き出し: {out_path.relative_to(settings.BASE_DIR)}'
        ))
        for r in results:
            self.stdout.write(
                f"  {r['months']}ヶ月後: {r['predicted_return_pct']:+.2f}% "
                f"(±{r['validation_mae_pct']:.2f}%)"
            )

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _load_feature_indicators(self) -> List[Indicator]:
        """重要度A/Bかつ十分な観測月数と古いデータを持つ指標を返す。"""
        from django.db.models import Count, Min

        candidates = (
            Indicator.objects
            .filter(is_active=True, importance__in=['A', 'B'])
            .annotate(
                obs_count=Count('observations'),
                obs_start=Min('observations__observation_date'),
            )
            .filter(
                obs_count__gte=MIN_OBSERVATIONS_PER_FEATURE,
                obs_start__lte=FEATURE_START_DATE_REQUIRED,
            )
            .order_by('display_order')
        )
        return list(candidates)

    def _load_sp500_monthly(self) -> Dict[date, float]:
        qs = (
            PriceObservation.objects
            .filter(ticker=PriceObservation.Ticker.SP500)
            .order_by('observation_month')
            .values_list('observation_month', 'close_price')
        )
        return {d.replace(day=1): v for d, v in qs}

    def _build_feature_matrix(self, indicators: List[Indicator]):
        """各指標を月次最新値に集約した DataFrame を返す。
        欠損は前方補完してから先頭の欠損行は落とす。
        """
        import numpy as np
        import pandas as pd

        per_indicator: Dict[str, Dict[date, float]] = {}
        for ind in indicators:
            obs_list = list(
                Observation.objects
                .filter(indicator=ind)
                .order_by('observation_date')
                .values_list('observation_date', 'value')
            )
            monthly: Dict[date, float] = {}
            for d, v in obs_list:
                monthly[d.replace(day=1)] = v
            if monthly:
                per_indicator[ind.fred_series_id] = monthly

        if not per_indicator:
            return pd.DataFrame()

        all_months = sorted({m for v in per_indicator.values() for m in v.keys()})
        df = pd.DataFrame(index=pd.to_datetime(all_months))
        for sid, m in per_indicator.items():
            series = pd.Series(
                {pd.Timestamp(d): v for d, v in m.items()}, dtype='float64'
            )
            df[sid] = series

        df = df.sort_index()
        # 月次の前方補完。先頭の NaN 行は落とす。
        df = df.ffill()
        df = df.dropna(how='any')
        return df

    def _build_xy(self, feature_df, sp500: Dict[date, float], horizon: int):
        """X, y を作成し、訓練・検証・推論用の最新行を返す。"""
        import numpy as np
        import pandas as pd

        sp500_series = pd.Series(
            {pd.Timestamp(d): v for d, v in sp500.items()},
            dtype='float64',
        ).sort_index()

        common_idx = feature_df.index.intersection(sp500_series.index)
        if len(common_idx) == 0:
            return (
                np.empty((0, feature_df.shape[1])), np.empty(0),
                np.empty((0, feature_df.shape[1])), np.empty(0),
                feature_df.iloc[[-1]].to_numpy(),
            )

        x_full = feature_df.loc[common_idx].to_numpy(dtype='float64')
        prices = sp500_series.loc[common_idx]

        # 目的変数: 対数リターン（horizon ヶ月先）
        future_prices = prices.shift(-horizon)
        y_full = np.log(future_prices.to_numpy() / prices.to_numpy())

        # 直近の horizon ヶ月は将来値が未知なのでラベル NaN → 訓練対象外
        valid_mask = ~np.isnan(y_full)
        x_known = x_full[valid_mask]
        y_known = y_full[valid_mask]
        idx_known = common_idx[valid_mask]

        if len(y_known) <= RECENT_VALIDATION_MONTHS:
            split = max(0, len(y_known) - 1)
        else:
            split = len(y_known) - RECENT_VALIDATION_MONTHS

        x_train = x_known[:split]
        y_train = y_known[:split]
        x_valid = x_known[split:]
        y_valid = y_known[split:]

        # 推論用: feature_df の最終行（最新月）
        latest_x = feature_df.iloc[[-1]].to_numpy(dtype='float64')

        return x_train, y_train, x_valid, y_valid, latest_x

    def _train_and_predict(self, x_train, y_train, x_valid, y_valid, latest_x):
        import lightgbm as lgb
        import numpy as np

        params = {
            'objective': 'regression',
            'metric': 'mae',
            'learning_rate': 0.05,
            'num_leaves': 16,
            'max_depth': 4,
            'min_data_in_leaf': 8,
            'feature_fraction': 0.85,
            'bagging_fraction': 0.85,
            'bagging_freq': 3,
            'lambda_l2': 1.0,
            'verbose': -1,
        }
        train_set = lgb.Dataset(x_train, label=y_train)
        if len(y_valid) > 0:
            valid_set = lgb.Dataset(x_valid, label=y_valid, reference=train_set)
            booster = lgb.train(
                params,
                train_set,
                num_boost_round=200,
                valid_sets=[valid_set],
                callbacks=[lgb.early_stopping(stopping_rounds=20), lgb.log_evaluation(0)],
            )
            valid_pred = booster.predict(x_valid)
            mae = float(np.mean(np.abs(valid_pred - y_valid)))
        else:
            booster = lgb.train(params, train_set, num_boost_round=200)
            mae = float('nan')

        latest_pred = float(booster.predict(latest_x)[0])
        return latest_pred, mae
