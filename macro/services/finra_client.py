"""FINRA Margin Statistics の取得。

FINRA は月次で証券会社の信用取引残高（Margin Debt）を集計・公開している。

データソース：
  ページ：https://www.finra.org/finra-data/browse-catalog/margin-statistics
公式ページ上の “Download the Data” Excel リンクを都度発見して取得する。
"""

import csv
import io
import logging
from datetime import date, datetime
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .simple_xlsx import read_first_sheet

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
USER_AGENT = 'Mozilla/5.0'

PAGE_URL = (
    'https://www.finra.org/rules-guidance/key-topics/'
    'margin-accounts/margin-statistics'
)
FALLBACK_XLSX_URL = (
    'https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx'
)


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


def _parse_table_rows(rows_list: List[List[str]]) -> List[Tuple[date, float]]:
    if not rows_list:
        return []

    header = [str(h).strip().lower() for h in rows_list[0]]
    date_idx = None
    debit_idx = None
    for i, h in enumerate(header):
        if date_idx is None and ('year' in h or 'month' in h or 'date' in h):
            date_idx = i
        if debit_idx is None and 'debit' in h:
            debit_idx = i

    if date_idx is None or debit_idx is None:
        return []

    rows: List[Tuple[date, float]] = []
    for row in rows_list[1:]:
        if len(row) <= max(date_idx, debit_idx):
            continue
        date_text = str(row[date_idx]).strip()
        value_text = str(row[debit_idx]).strip().replace(',', '')
        if not date_text or not value_text:
            continue
        try:
            obs_date = _parse_year_month(date_text)
            value = float(value_text)
        except ValueError:
            continue
        rows.append((obs_date, value))
    rows.sort(key=lambda x: x[0])
    return rows


def _parse_xlsx(content: bytes) -> List[Tuple[date, float]]:
    return _parse_table_rows(read_first_sheet(content))


def _download_url_from_page(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a'):
        text = ' '.join(link.get_text(' ', strip=True).split()).lower()
        href = link.get('href') or ''
        if 'download the data' in text and href:
            return urljoin(PAGE_URL, href)
    for link in soup.find_all('a'):
        href = link.get('href') or ''
        if 'margin-statistics' in href.lower() and href.lower().endswith('.xlsx'):
            return urljoin(PAGE_URL, href)
    return None


def _fetch_xlsx() -> bytes:
    headers = {'User-Agent': USER_AGENT}
    try:
        page = requests.get(PAGE_URL, headers=headers, timeout=DEFAULT_TIMEOUT)
        page.raise_for_status()
        xlsx_url = _download_url_from_page(page.text) or FALLBACK_XLSX_URL
    except requests.RequestException:
        xlsx_url = FALLBACK_XLSX_URL

    response = requests.get(xlsx_url, headers=headers, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    return response.content


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
    try:
        rows = _parse_xlsx(_fetch_xlsx())
    except Exception as exc:
        raise FinraError(f"FINRA fetch failed: {exc}") from exc
    if observation_start:
        rows = [(d, v) for d, v in rows if d >= observation_start]
    if observation_end:
        rows = [(d, v) for d, v in rows if d <= observation_end]
    return rows
