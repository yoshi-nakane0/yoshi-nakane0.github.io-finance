"""Cboe（Chicago Board Options Exchange）から SKEW Index などを取得。

Cboe は SKEW / VIX 等の歴史的データを CSV で公開している。
SKEW Index URL: https://cdn.cboe.com/api/global/us_indices/daily_prices/SKEW_History.csv
"""

import csv
import io
import logging
from datetime import date, datetime
from typing import List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/118.0 Safari/537.36'
)

# series_id → CSV URL マッピング
SERIES_TO_URL = {
    'CBOE_SKEW': 'https://cdn.cboe.com/api/global/us_indices/daily_prices/SKEW_History.csv',
}


class CboeError(Exception):
    """Cboe 取得失敗"""


def _parse_csv(text: str) -> List[Tuple[date, float]]:
    """SKEW CSV をパース。先頭行はヘッダ。

    SKEW_History.csv のフォーマット例：
      Date,SKEW
      1990-01-02,123.45
      ...
    """
    rows: List[Tuple[date, float]] = []
    buf = io.StringIO(text)
    reader = csv.reader(buf)
    header_seen = False
    for row in reader:
        if not row or len(row) < 2:
            continue
        if not header_seen:
            header_seen = True
            # ヘッダ行の判定: 1列目が "Date" っぽい / 数字でない
            if not row[0].strip().lower().startswith('date'):
                # 実はヘッダがなかった場合は1行目もデータとして処理
                pass
            else:
                continue
        date_text = row[0].strip()
        value_text = row[1].strip()
        if not date_text or not value_text:
            continue
        try:
            obs_date = _parse_flexible_date(date_text)
        except ValueError:
            continue
        try:
            value = float(value_text)
        except ValueError:
            continue
        rows.append((obs_date, value))
    rows.sort(key=lambda x: x[0])
    return rows


def _parse_flexible_date(text: str) -> date:
    """`YYYY-MM-DD` か `M/D/YYYY` のいずれかをパース。"""
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%-m/%-d/%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {text}")


def fetch_observations(
    series_id: str,
    observation_start: Optional[date] = None,
    observation_end: Optional[date] = None,
) -> List[Tuple[date, float]]:
    """Cboe から daily 観測値を取得。observation_start/end でクライアント側フィルタ。"""
    url = SERIES_TO_URL.get(series_id)
    if not url:
        raise CboeError(f"Cboe URL 未定義: {series_id}")

    headers = {'User-Agent': USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise CboeError(f"Cboe fetch failed for {series_id}: {exc}")

    rows = _parse_csv(response.text)
    if observation_start:
        rows = [(d, v) for d, v in rows if d >= observation_start]
    if observation_end:
        rows = [(d, v) for d, v in rows if d <= observation_end]
    return rows
