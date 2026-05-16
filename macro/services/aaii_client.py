"""AAII Sentiment Survey の取得。

AAII（American Association of Individual Investors）は週次で個人投資家の
強気・弱気・中立比率を公開している。

AAII 本体の履歴ファイルはサーバ側で 403 になりやすいため、
AAII 公式 Insights の週次記事から直近値を抽出する。
"""

import csv
import io
import logging
import re
from datetime import date, datetime
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/118.0 Safari/537.36'
)

FEED_URL = 'https://insights.aaii.com/feed'
ARTICLE_TITLE_KEYWORD = 'aaii sentiment survey'


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
    for fmt in (
        '%Y-%m-%d',
        '%Y-%m-%dT%H:%M:%S%z',
        '%a, %d %b %Y %H:%M:%S %Z',
        '%m/%d/%Y',
        '%m/%d/%y',
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {text}")


def _parse_article_text(text: str, fallback_date: date) -> Optional[Tuple[date, float]]:
    """AAII Insights 記事本文から Bullish% を抽出する。"""
    text = re.sub(r'\s+', ' ', text)
    match = re.search(
        r"This week(?:'|’)?s Sentiment Survey results:.*?"
        r"Bullish:\s*([0-9]+(?:\.[0-9]+)?)%",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        match = re.search(
            r"Bullish sentiment.*?(?:increased|decreased|rose|fell|jumped|declined)"
            r".*?to\s*([0-9]+(?:\.[0-9]+)?)%",
            text,
            flags=re.IGNORECASE,
        )
    if match is None:
        return None
    return fallback_date, float(match.group(1))


def _fetch_recent_articles() -> List[Tuple[str, date]]:
    headers = {'User-Agent': USER_AGENT}
    response = requests.get(FEED_URL, headers=headers, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    articles: List[Tuple[str, date]] = []
    for item in root.findall('./channel/item'):
        title = item.findtext('title') or ''
        link = item.findtext('link') or ''
        pub_date = item.findtext('pubDate') or ''
        if ARTICLE_TITLE_KEYWORD not in title.lower() or not link:
            continue
        try:
            published = _parse_flexible_date(pub_date)
        except ValueError:
            published = date.today()
        articles.append((link, published))
    return articles


def _fetch_article_value(url: str, fallback_date: date) -> Optional[Tuple[date, float]]:
    headers = {'User-Agent': USER_AGENT}
    response = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    published = fallback_date
    meta = soup.find('meta', attrs={'itemprop': 'datePublished'})
    if meta and meta.get('content'):
        try:
            published = _parse_flexible_date(meta['content'])
        except ValueError:
            published = fallback_date

    return _parse_article_text(soup.get_text('\n', strip=True), published)


def fetch_observations(
    series_id: str,
    observation_start: Optional[date] = None,
    observation_end: Optional[date] = None,
) -> List[Tuple[date, float]]:
    """AAII Sentiment（Bullish %）を取得。"""
    if series_id != 'AAII_BULLISH':
        raise AaiiError(f"AAII series 未定義: {series_id}")

    try:
        rows = []
        for url, published in _fetch_recent_articles()[:20]:
            item = _fetch_article_value(url, published)
            if item is not None:
                rows.append(item)
    except requests.RequestException as exc:
        raise AaiiError(f"AAII fetch failed: {exc}") from exc

    dedup = {d: v for d, v in rows}
    rows = sorted(dedup.items())
    if observation_start:
        rows = [(d, v) for d, v in rows if d >= observation_start]
    if observation_end:
        rows = [(d, v) for d, v in rows if d <= observation_end]
    if not rows:
        raise AaiiError("AAII fetch failed: no sentiment rows parsed")
    return rows
