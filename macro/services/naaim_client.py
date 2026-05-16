"""NAAIM Exposure Index の取得。

NAAIM（National Association of Active Investment Managers）は週次で
アクティブマネージャーの株式エクスポージャー指数を公開している。

データソース：
  https://www.naaim.org/programs/naaim-exposure-index/
  公式ページ上の Excel リンクを都度発見して取得する。
"""

import csv
import io
import logging
from datetime import date, datetime
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .simple_xlsx import excel_serial_to_date, read_first_sheet

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/118.0 Safari/537.36'
)

PAGE_URL = 'https://naaim.org/programs/naaim-exposure-index/'


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


def _parse_xlsx(content: bytes) -> List[Tuple[date, float]]:
    rows_list = read_first_sheet(content)
    if not rows_list:
        return []

    header = [str(h).strip().lower() for h in rows_list[0]]
    date_idx = None
    value_idx = None
    for i, h in enumerate(header):
        if date_idx is None and 'date' in h:
            date_idx = i
        if value_idx is None and ('mean' in h or 'average' in h or 'exposure' in h):
            value_idx = i
    if date_idx is None or value_idx is None:
        date_idx, value_idx = 0, 1

    rows: List[Tuple[date, float]] = []
    for row in rows_list[1:]:
        if len(row) <= max(date_idx, value_idx):
            continue
        date_text = str(row[date_idx]).strip()
        value_text = str(row[value_idx]).strip()
        if not date_text or not value_text:
            continue
        try:
            if date_text.replace('.', '', 1).isdigit():
                obs_date = excel_serial_to_date(float(date_text))
            else:
                obs_date = _parse_flexible_date(date_text)
            value = float(value_text)
        except ValueError:
            continue
        rows.append((obs_date, value))
    rows.sort(key=lambda x: x[0])
    return rows


def _download_url_from_page(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a'):
        href = link.get('href') or ''
        text = ' '.join(link.get_text(' ', strip=True).split()).lower()
        lower_href = href.lower()
        if href and lower_href.endswith(('.xlsx', '.xls', '.csv')) and (
            'use_data' in lower_href
            or 'exposure' in lower_href
            or text == 'here'
            or 'download' in text
        ):
            return urljoin(PAGE_URL, href)
    return None


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
    try:
        page = requests.get(PAGE_URL, headers=headers, timeout=DEFAULT_TIMEOUT)
        page.raise_for_status()
        data_url = _download_url_from_page(page.text)
        if not data_url:
            raise NaaimError("NAAIM download URL not found")
        response = requests.get(data_url, headers=headers, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        if data_url.lower().endswith('.csv'):
            rows = _parse_csv(response.text)
        else:
            rows = _parse_xlsx(response.content)
    except (requests.RequestException, NaaimError) as exc:
        raise NaaimError(f"NAAIM fetch failed: {exc}") from exc
    if observation_start:
        rows = [(d, v) for d, v in rows if d >= observation_start]
    if observation_end:
        rows = [(d, v) for d, v in rows if d <= observation_end]
    return rows
