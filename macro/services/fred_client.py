"""FRED API クライアント。

FRED (Federal Reserve Economic Data) API のラッパー。
環境変数 FRED_API_KEY が必要。未設定時は呼び出し側で対応する。
"""

import logging
import os
import time
from datetime import date, datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRY = 3
DEFAULT_BACKOFF_SEC = 1.5


class FredApiError(Exception):
    """FRED API 呼び出しの失敗"""


def get_api_key() -> Optional[str]:
    """環境変数から API キーを読み出す"""
    key = os.getenv('FRED_API_KEY')
    if key:
        key = key.strip()
    return key or None


def _parse_observations_payload(data):
    observations = []
    for raw in data.get('observations', []):
        value_text = (raw.get('value') or '').strip()
        date_text = (raw.get('date') or '').strip()
        if not value_text or value_text == '.' or not date_text:
            continue
        try:
            obs_date = datetime.strptime(date_text, '%Y-%m-%d').date()
            obs_value = float(value_text)
        except ValueError:
            continue
        observations.append({
            'date': obs_date,
            'value': obs_value,
            'realtime_start': raw.get('realtime_start'),
            'realtime_end': raw.get('realtime_end'),
        })
    return observations


def fetch_observations_with_vintage(
    series_id: str,
    observation_start: Optional[date] = None,
    observation_end: Optional[date] = None,
    api_key: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRY,
):
    """指定系列の観測値と realtime 情報を取得する。

    値が "." (FRED の欠損表記) の行はスキップする。
    取得失敗時は FredApiError を投げる。
    """
    api_key = api_key or get_api_key()
    if not api_key:
        raise FredApiError("FRED_API_KEY が未設定です")

    params = {
        'series_id': series_id,
        'api_key': api_key,
        'file_type': 'json',
        'sort_order': 'asc',
    }
    if observation_start:
        params['observation_start'] = observation_start.isoformat()
    if observation_end:
        params['observation_end'] = observation_end.isoformat()

    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                FRED_BASE_URL,
                params=params,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            return _parse_observations_payload(data)
        except requests.RequestException as exc:
            last_error = exc
            logger.warning(
                "FRED fetch failed (series=%s, attempt=%s): %s",
                series_id, attempt, exc,
            )
            if attempt < retries:
                time.sleep(DEFAULT_BACKOFF_SEC * attempt)
        except ValueError as exc:
            last_error = exc
            logger.warning(
                "FRED parse failed (series=%s): %s",
                series_id, exc,
            )
            break

    raise FredApiError(
        f"FRED fetch failed for {series_id}: {last_error}"
    )


def fetch_observations(
    series_id: str,
    observation_start: Optional[date] = None,
    observation_end: Optional[date] = None,
    api_key: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRY,
):
    """指定系列の観測値を取得し (date, value) のタプル列で返す。"""
    observations = fetch_observations_with_vintage(
        series_id,
        observation_start=observation_start,
        observation_end=observation_end,
        api_key=api_key,
        timeout=timeout,
        retries=retries,
    )
    return [(row['date'], row['value']) for row in observations]
