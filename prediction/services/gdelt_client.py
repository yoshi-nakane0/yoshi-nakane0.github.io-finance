"""GDELT Doc 2.0 API クライアント。

GDELT の DOC API は API キー不要・登録不要で利用可能。
公式: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/

利用するモード:
  - ArtList: 個別記事の URL/タイトル/日時/媒体（tone は含まれない）
  - TimelineTone: 期間内の平均トーン（時系列）

GDELT は「1リクエスト/5秒」のレート制限を案内している。
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

GDELT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRY = 2
REQUEST_SPACING_SEC = 6.0
RETRY_BACKOFF_SEC = 8.0

_last_request_at: Optional[float] = None


class GdeltApiError(Exception):
    """GDELT API 呼び出しの失敗"""


def _format_datetime(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime('%Y%m%d%H%M%S')


def _enforce_spacing():
    global _last_request_at
    if _last_request_at is None:
        _last_request_at = time.monotonic()
        return
    elapsed = time.monotonic() - _last_request_at
    if elapsed < REQUEST_SPACING_SEC:
        time.sleep(REQUEST_SPACING_SEC - elapsed)
    _last_request_at = time.monotonic()


def _request(params: dict) -> dict:
    last_error: Optional[Exception] = None
    for attempt in range(1, DEFAULT_RETRY + 2):
        _enforce_spacing()
        try:
            response = requests.get(
                GDELT_BASE_URL,
                params=params,
                timeout=DEFAULT_TIMEOUT,
                headers={'User-Agent': 'prediction-app/1.0'},
            )
            if response.status_code == 429:
                raise requests.HTTPError('429 Too Many Requests', response=response)
            response.raise_for_status()
            text = response.text.strip()
            if not text or text.startswith('Please'):
                raise GdeltApiError(f'GDELT rate-limit notice: {text[:120]}')
            return response.json()
        except (requests.RequestException, ValueError, GdeltApiError) as exc:
            last_error = exc
            logger.warning(
                "GDELT fetch failed (attempt=%s): %s",
                attempt, exc,
            )
            if attempt <= DEFAULT_RETRY:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise GdeltApiError(f"GDELT fetch failed: {last_error}")


def fetch_articles(
    query: str,
    start: datetime,
    end: datetime,
    max_records: int = 75,
) -> list:
    """指定期間の英語記事リストを取得（tone は含まれない）。"""
    params = {
        'query': f'{query} sourcelang:eng',
        'mode': 'ArtList',
        'format': 'json',
        'startdatetime': _format_datetime(start),
        'enddatetime': _format_datetime(end),
        'maxrecords': max(1, min(int(max_records), 250)),
        'sort': 'datedesc',
    }
    data = _request(params)
    articles = data.get('articles') or []
    normalized = []
    seen_title_keys = set()
    for raw in articles:
        url = (raw.get('url') or '').strip()
        if not url:
            continue
        seen_text = (raw.get('seendate') or '').strip()
        try:
            published_at = datetime.strptime(seen_text, '%Y%m%dT%H%M%SZ').replace(
                tzinfo=timezone.utc,
            )
        except ValueError:
            published_at = None
        if published_at is None:
            continue
        title = (raw.get('title') or '').strip()
        # タイトルを正規化（小文字・空白統合）してシンジケート記事の重複を除外
        title_key = ' '.join(title.lower().split()) if title else ''
        if title_key:
            if title_key in seen_title_keys:
                continue
            seen_title_keys.add(title_key)
        domain = (raw.get('domain') or '').strip()
        if not domain:
            try:
                domain = urlparse(url).netloc
            except ValueError:
                domain = ''
        normalized.append({
            'url': url,
            'title': title,
            'published_at': published_at,
            'domain': domain[:128],
            'sourcecountry': (raw.get('sourcecountry') or '').strip(),
        })
    return normalized


def fetch_timeline_tone(
    query: str,
    start: datetime,
    end: datetime,
) -> dict:
    """指定期間の平均トーンと記事ボリュームを取得。

    Returns: {
        'tone_avg': float または None,
        'tone_min': float または None,
        'tone_max': float または None,
        'volume': int,    # 期間内の総記事数（概算）
        'daily': [{'date': YYYY-MM-DD, 'tone': float, 'volume': float}, ...]
    }
    """
    params = {
        'query': f'{query} sourcelang:eng',
        'mode': 'TimelineTone',
        'format': 'json',
        'startdatetime': _format_datetime(start),
        'enddatetime': _format_datetime(end),
    }
    data = _request(params)
    timelines = data.get('timeline') or []
    daily = []
    tones = []
    for series in timelines:
        for point in series.get('data') or []:
            date_text = (point.get('date') or '').strip()
            value_raw = point.get('value')
            try:
                tone_value = float(value_raw)
            except (TypeError, ValueError):
                continue
            try:
                point_date = datetime.strptime(date_text, '%Y%m%dT%H%M%SZ').date()
            except ValueError:
                continue
            daily.append({
                'date': point_date.isoformat(),
                'tone': tone_value,
            })
            tones.append(tone_value)

    return {
        'tone_avg': sum(tones) / len(tones) if tones else None,
        'tone_min': min(tones) if tones else None,
        'tone_max': max(tones) if tones else None,
        'daily': daily,
    }


def fetch_topic_window(
    query: str,
    days: int = 1,
    max_records: int = 75,
    end: Optional[datetime] = None,
) -> dict:
    """1トピックを期間内で取得し、記事リストとトーン集計を合わせて返す。"""
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    articles = fetch_articles(query, start, end, max_records=max_records)
    tone = fetch_timeline_tone(query, start, end)
    return {
        'start': start,
        'end': end,
        'articles': articles,
        'articles_count': len(articles),
        'tone_avg': tone['tone_avg'],
        'tone_min': tone['tone_min'],
        'tone_max': tone['tone_max'],
        'daily_tone': tone['daily'],
    }
