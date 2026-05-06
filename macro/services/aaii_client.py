"""AAII Sentiment Survey の取得。

AAII（American Association of Individual Investors）は週次で個人投資家の
強気・弱気・中立比率を公開している。

データソース：
  AAII公式（CSV）: https://www.aaii.com/files/surveys/sentiment.xls

xls はバイナリ Excel。プロジェクトの依存（lxml はあるが xlrd / openpyxl は未追加）の関係で、
本実装は Stooq などの代替CSVソース、または HTML 抽出を試みるフォールバック路線を取る。

本ファイルでは「Stooq の AAII 互換時系列ファイル」または将来的な独自ミラーを叩く形で実装。
URL は SERIES_TO_URL の差し替えで切り替え可能。
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

# 候補CSVソース（公式 .xls のCSVミラー、または互換ソース）
# 公式 .xls をパースするには openpyxl/xlrd 等が必要なため、CSV ミラーを使う。
# 公式 URL の CSV 化版：今後変更される可能性ありなので CANDIDATE_URLS に列挙して順次トライ。
CANDIDATE_URLS = {
    'AAII_BULLISH': [
        # AAII 公式は基本 .xls だが、一部期間は CSV エンドポイントが公開されている
        # 本URLは仮置き：実環境で安定動作するURLが見つかれば差し替え可
        'https://www.aaii.com/files/surveys/sentiment.csv',
    ],
}


class AaiiError(Exception):
    pass


def _parse_csv(text: str) -> List[Tuple[date, float]]:
    """AAII CSV をパース。Bullish 列を取り出す。"""
    rows: List[Tuple[date, float]] = []
    buf = io.StringIO(text)
    reader = csv.reader(buf)
    rows_list = list(reader)
    if not rows_list:
        return []

    header = [h.strip().lower() for h in rows_list[0]]
    date_idx = None
    bullish_idx = None
    for i, h in enumerate(header):
        if date_idx is None and 'date' in h:
            date_idx = i
        if bullish_idx is None and 'bullish' in h:
            bullish_idx = i

    if date_idx is None or bullish_idx is None:
        return []

    for row in rows_list[1:]:
        if len(row) <= max(date_idx, bullish_idx):
            continue
        date_text = row[date_idx].strip()
        value_text = row[bullish_idx].strip().rstrip('%')
        if not date_text or not value_text:
            continue
        try:
            obs_date = _parse_flexible_date(date_text)
        except ValueError:
            continue
        try:
            value = float(value_text)
            # 0〜1 の比率で来た場合は %にする
            if 0 < value <= 1.0:
                value *= 100.0
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
    """AAII Sentiment（Bullish %）を取得。"""
    urls = CANDIDATE_URLS.get(series_id) or []
    if not urls:
        raise AaiiError(f"AAII URL 未定義: {series_id}")

    headers = {'User-Agent': USER_AGENT}
    last_error: Optional[Exception] = None
    text = None
    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            text = response.text
            break
        except requests.RequestException as exc:
            last_error = exc
            continue

    if text is None:
        raise AaiiError(f"AAII fetch failed: {last_error}")

    rows = _parse_csv(text)
    if observation_start:
        rows = [(d, v) for d, v in rows if d >= observation_start]
    if observation_end:
        rows = [(d, v) for d, v in rows if d <= observation_end]
    return rows
