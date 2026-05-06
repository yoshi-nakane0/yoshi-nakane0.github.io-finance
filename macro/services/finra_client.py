"""FINRA Margin Statistics の取得。

FINRA は月次で証券会社の信用取引残高（Margin Debt）を集計・公開している。

データソース：
  ページ：https://www.finra.org/finra-data/browse-catalog/margin-statistics
  CSV/Excel ダウンロードリンク（公式の安定 URL）：
    https://www.finra.org/sites/default/files/...margin-statistics.csv

URL は変動の可能性があるため、CANDIDATE_URLS に複数列挙して順次試行する。
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

# FINRA はデータ提供が時々URLを更新するため複数候補を持っておく
CANDIDATE_URLS = [
    # FINRA Data Catalog の Margin Statistics CSV エンドポイント例
    'https://www.finra.org/sites/default/files/2024-12/margin-statistics.csv',
    'https://www.finra.org/sites/default/files/margin-statistics.csv',
]


class FinraError(Exception):
    pass


def _parse_csv(text: str) -> List[Tuple[date, float]]:
    """FINRA Margin CSV をパース。Debit Balances 列（信用買い残）を取り出す。

    実物のフォーマット例：
      Year-Month,Debit Balances in Customers' Securities Margin Accounts,Free Credit Balances in Customers' Cash Accounts,Free Credit Balances in Customers' Securities Margin Accounts
      2024-12,815523,...
    """
    rows: List[Tuple[date, float]] = []
    buf = io.StringIO(text)
    reader = csv.reader(buf)
    rows_list = list(reader)
    if not rows_list:
        return []

    header = [h.strip().lower() for h in rows_list[0]]
    date_idx = None
    debit_idx = None
    for i, h in enumerate(header):
        if date_idx is None and ('year' in h or 'month' in h or 'date' in h):
            date_idx = i
        if debit_idx is None and 'debit' in h:
            debit_idx = i

    if date_idx is None or debit_idx is None:
        return []

    for row in rows_list[1:]:
        if len(row) <= max(date_idx, debit_idx):
            continue
        date_text = row[date_idx].strip()
        value_text = row[debit_idx].strip().replace(',', '')
        if not date_text or not value_text:
            continue
        try:
            obs_date = _parse_year_month(date_text)
        except ValueError:
            continue
        try:
            value = float(value_text)
        except ValueError:
            continue
        rows.append((obs_date, value))
    rows.sort(key=lambda x: x[0])
    return rows


def _parse_year_month(text: str) -> date:
    """`YYYY-MM` または `YYYY-MM-DD` または `MM/YYYY` を月初日で返す。"""
    for fmt in ('%Y-%m', '%Y-%m-%d', '%m/%Y', '%b-%y', '%b %Y', '%B %Y'):
        try:
            d = datetime.strptime(text, fmt).date()
            return d.replace(day=1)
        except ValueError:
            continue
    raise ValueError(f"unparseable year-month: {text}")


def fetch_observations(
    series_id: str,
    observation_start: Optional[date] = None,
    observation_end: Optional[date] = None,
) -> List[Tuple[date, float]]:
    """FINRA Margin Debt を取得。"""
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
        raise FinraError(f"FINRA fetch failed: {last_error}")

    rows = _parse_csv(text)
    if observation_start:
        rows = [(d, v) for d, v in rows if d >= observation_start]
    if observation_end:
        rows = [(d, v) for d, v in rows if d <= observation_end]
    return rows
