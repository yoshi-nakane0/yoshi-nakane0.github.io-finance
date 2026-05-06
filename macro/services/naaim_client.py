"""NAAIM Exposure Index の取得。

NAAIM（National Association of Active Investment Managers）は週次で
アクティブマネージャーの株式エクスポージャー指数を公開している。

データソース：
  https://www.naaim.org/programs/naaim-exposure-index/
  CSV エンドポイント例：
    https://naaim.org/wp-content/uploads/...exposure-index-data.csv
  公式の安定 URL は不確実なため、フォールバックとしてHTMLから抽出する経路も持たせる。
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

# 候補URL（順番に試行）
CANDIDATE_URLS = [
    'https://www.naaim.org/wp-content/uploads/2014/01/NAAIM-Exposure-Index-Data.csv',
    'https://naaim.org/wp-content/uploads/2014/01/NAAIM-Exposure-Index-Data.csv',
]


class NaaimError(Exception):
    pass


def _parse_csv(text: str) -> List[Tuple[date, float]]:
    """NAAIM CSV をパース。

    実物のフォーマットは "Date,NAAIM Exposure Index,..." のような並びになる。
    Date 列と Exposure 列（または平均、Mean Average）を取り出す。
    """
    rows: List[Tuple[date, float]] = []
    buf = io.StringIO(text)
    reader = csv.reader(buf)
    rows_list = list(reader)
    if not rows_list:
        return []

    header = [h.strip() for h in rows_list[0]]
    date_idx = None
    value_idx = None
    for i, h in enumerate(header):
        lower = h.lower()
        if date_idx is None and 'date' in lower:
            date_idx = i
        if value_idx is None and (
            'naaim' in lower or 'exposure' in lower or 'mean' in lower
        ):
            value_idx = i

    if date_idx is None or value_idx is None:
        # ヘッダが取れなければ先頭2列を仮で使う
        date_idx, value_idx = 0, 1

    for row in rows_list[1:]:
        if len(row) <= max(date_idx, value_idx):
            continue
        date_text = row[date_idx].strip()
        value_text = row[value_idx].strip()
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
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
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
    """NAAIM Exposure を取得。series_id は 'NAAIM_EXPOSURE' を想定。"""
    headers = {'User-Agent': USER_AGENT}
    last_error: Optional[Exception] = None
    text = None
    for url in CANDIDATE_URLS:
        try:
            response = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            text = response.text
            break
        except requests.RequestException as exc:
            last_error = exc
            continue

    if text is None:
        raise NaaimError(f"NAAIM fetch failed: {last_error}")

    rows = _parse_csv(text)
    if observation_start:
        rows = [(d, v) for d, v in rows if d >= observation_start]
    if observation_end:
        rows = [(d, v) for d, v in rows if d <= observation_end]
    return rows
