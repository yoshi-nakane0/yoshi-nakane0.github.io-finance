import datetime
import json
import logging
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

NIKKEI_PER_URL = "https://indexes.nikkei.co.jp/nkave/archives/data?list=per"
NIKKEI_DIVIDEND_URL = (
    "https://indexes.nikkei.co.jp/nkave/archives/data?list=dividend"
)
REQUEST_TIMEOUT_SEC = (5, 15)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Connection": "close",
    "Referer": "https://indexes.nikkei.co.jp/",
}
DIVIDEND_YIELD_KEY = "dividend_yield"
PER_AVERAGE_KEY = "weighted_average"
DIVIDEND_AVERAGE_KEY = "simple_average"
PER_AVERAGE_HEADER_TERMS = ("加重平均", "加重", "weighted")
DIVIDEND_AVERAGE_HEADER_TERMS = ("単純平均", "単純", "simple")
PER_AVERAGE_KEY_TERMS = ("weight", "weighted", "加重")
DIVIDEND_AVERAGE_KEY_TERMS = ("simple", "単純", "plain")
FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")
DATE_RE = re.compile(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})")
OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "BaseCalc"
    / "data"
    / "nikkei_per.json"
)
TABLE_SELECTORS = (
    "table#data-table",
    "table#dataTable",
    "table#nkave-table",
    "table#nkaveTable",
    "table.tbl-type01",
    "table.tbl-type-01",
    "table.data-table",
    "table.per-table",
    "table[data-list='per']",
    "table[data-table='per']",
    "table[data-list='dividend']",
    "table[data-table='dividend']",
)
logger = logging.getLogger(__name__)


def _parse_float(text):
    if not text:
        return None
    cleaned = text.replace(",", "").replace("倍", "").strip()
    match = FLOAT_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_date_parts(text):
    if not text:
        return None
    match = DATE_RE.search(text.strip())
    if not match:
        return None
    try:
        year, month, day = (int(part) for part in match.groups())
    except ValueError:
        return None
    return year, month, day


def _normalize_date(text):
    parts = _parse_date_parts(text)
    if not parts:
        return None
    year, month, day = parts
    return f"{year:04d}.{month:02d}.{day:02d}"


def _matches_terms(text, lowered, terms):
    for term in terms:
        if term in text:
            return True
        if term.lower() in lowered:
            return True
    return False


def _header_indices(header_cells, average_terms):
    date_col = None
    index_col = None
    average_col = None
    for idx, text in enumerate(header_cells):
        normalized = text.replace(" ", "").replace("\u3000", "").strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if date_col is None and (
            "日付" in normalized
            or "年月日" in normalized
            or "date" in lowered
        ):
            date_col = idx
        if index_col is None and (
            ("指数" in normalized and "ベース" in normalized)
            or ("index" in lowered and "base" in lowered)
        ):
            index_col = idx
        if average_col is None and _matches_terms(
            normalized, lowered, average_terms
        ):
            average_col = idx
    return date_col, index_col, average_col


def _iter_tables(soup):
    seen = set()
    for selector in TABLE_SELECTORS:
        for table in soup.select(selector):
            table_id = id(table)
            if table_id in seen:
                continue
            seen.add(table_id)
            yield table
    for table in soup.find_all("table"):
        table_id = id(table)
        if table_id in seen:
            continue
        seen.add(table_id)
        yield table


def _extract_latest_values_from_table(table, average_header_terms):
    header_rows = table.select("thead tr")
    if not header_rows:
        header_rows = table.select("tr")[:2]

    date_col = None
    index_col = None
    average_col = None
    for row in header_rows:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        texts = [cell.get_text(strip=True) for cell in cells]
        row_date_col, row_index_col, row_average_col = _header_indices(
            texts, average_header_terms
        )
        if row_date_col is not None:
            date_col = row_date_col
        if row_index_col is not None:
            index_col = row_index_col
        if row_average_col is not None:
            average_col = row_average_col
        if index_col is not None and average_col is not None:
            break

    if index_col is None or average_col is None:
        return None
    if date_col is None:
        date_col = 0

    rows = table.select("tbody tr")
    if not rows:
        rows = table.find_all("tr")

    records = []
    for row in rows:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        max_idx = max(date_col, index_col, average_col)
        if len(cells) <= max_idx:
            continue
        index_val = _parse_float(cells[index_col].get_text(strip=True))
        average_val = _parse_float(cells[average_col].get_text(strip=True))
        if index_val is None or average_val is None:
            continue
        date_text = cells[date_col].get_text(strip=True)
        normalized_date = _normalize_date(date_text)
        if normalized_date is None:
            continue
        date_parts = _parse_date_parts(date_text)
        records.append(
            {
                "date": normalized_date,
                "date_parts": date_parts,
                "index_based": index_val,
                "average_value": average_val,
            }
        )

    if not records:
        return None

    dated_records = [record for record in records if record["date_parts"]]
    if dated_records:
        record = max(dated_records, key=lambda item: item["date_parts"])
    else:
        record = records[-1]

    return {
        "date": record["date"],
        "index_based": record["index_based"],
        "average_value": record["average_value"],
    }


def _extract_from_next_data(soup, average_key_terms):
    script = soup.select_one("script#__NEXT_DATA__")
    if not script:
        return None
    raw = script.string or script.get_text(strip=True)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    candidates = []
    average_key_terms = tuple(average_key_terms)
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.values())
            continue
        if isinstance(current, list):
            stack.extend(current)
            if not current or not all(isinstance(item, dict) for item in current):
                continue
            for item in current:
                date_text = None
                index_val = None
                average_val = None
                for key, value in item.items():
                    if isinstance(value, str) and DATE_RE.search(value):
                        date_text = value
                    if isinstance(value, (int, float, str)):
                        numeric = _parse_float(str(value))
                        if numeric is None:
                            continue
                        key_text = str(key)
                        key_lower = key_text.lower()
                        if "index" in key_lower or "base" in key_lower:
                            index_val = numeric
                        if _matches_terms(key_text, key_lower, average_key_terms):
                            average_val = numeric
                normalized_date = _normalize_date(date_text) if date_text else None
                if (
                    normalized_date is not None
                    and index_val is not None
                    and average_val is not None
                ):
                    date_parts = _parse_date_parts(date_text)
                    candidates.append(
                        {
                            "date": normalized_date,
                            "date_parts": date_parts,
                            "index_based": index_val,
                            "average_value": average_val,
                        }
                    )

    if not candidates:
        return None
    return max(candidates, key=lambda item: item["date_parts"])


def _extract_latest_values(
    soup, average_header_terms, average_key_terms, average_key
):
    if soup is None:
        return None
    from_next = _extract_from_next_data(soup, average_key_terms)
    if from_next:
        return {
            "date": from_next["date"],
            "index_based": from_next["index_based"],
            average_key: from_next["average_value"],
        }
    for table in _iter_tables(soup):
        result = _extract_latest_values_from_table(table, average_header_terms)
        if result:
            return {
                "date": result["date"],
                "index_based": result["index_based"],
                average_key: result["average_value"],
            }
    return None


def _is_positive_number(value):
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _is_valid_result(result, average_key):
    if not isinstance(result, dict):
        return False
    date_value = result.get("date")
    if not isinstance(date_value, str) or not DATE_RE.search(date_value):
        return False
    if not _is_positive_number(result.get("index_based")):
        return False
    if not _is_positive_number(result.get(average_key)):
        return False
    return True


def _is_valid_payload(payload, require_dividend):
    if not _is_valid_result(payload, PER_AVERAGE_KEY):
        return False
    if not require_dividend:
        return True
    dividend = payload.get(DIVIDEND_YIELD_KEY)
    return _is_valid_result(dividend, DIVIDEND_AVERAGE_KEY)


def _load_existing_payload(require_dividend=False):
    if not OUTPUT_PATH.exists():
        return None
    try:
        with OUTPUT_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not _is_valid_payload(payload, require_dividend):
        return None
    return payload


def _warmup_session(session):
    warmup_urls = (
        "https://indexes.nikkei.co.jp/",
        "https://indexes.nikkei.co.jp/nkave/",
    )
    for url in warmup_urls:
        try:
            session.get(url, timeout=REQUEST_TIMEOUT_SEC)
        except requests.RequestException:
            continue


def _fetch_html_with_requests(url):
    session = requests.Session()
    session.headers.update(HEADERS)
    _warmup_session(session)
    return session.get(url, timeout=REQUEST_TIMEOUT_SEC, allow_redirects=True)


def _fetch_html_with_selenium(url):
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError:
        return None

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,800")
    options.add_argument(f"--user-agent={USER_AGENT}")
    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")
    try:
        if chromedriver_path:
            service = Service(chromedriver_path)
            driver = webdriver.Chrome(service=service, options=options)
        else:
            driver = webdriver.Chrome(options=options)
    except Exception:
        return None

    try:
        driver.get(url)
        WebDriverWait(driver, 20).until(
            lambda d: d.find_elements(By.CSS_SELECTOR, "script#__NEXT_DATA__")
            or d.find_elements(By.TAG_NAME, "table")
        )
        return driver.page_source
    except WebDriverException:
        return None
    finally:
        driver.quit()


def _fetch_html(url):
    response = None
    try:
        response = _fetch_html_with_requests(url)
    except requests.RequestException as exc:
        logger.warning("Request failed: %s", exc)
    if response is not None and response.status_code != 403:
        try:
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            logger.warning("HTTP error: %s", exc)
    html = _fetch_html_with_selenium(url)
    if html:
        return html
    if response is not None and response.text:
        return response.text
    return None


def _build_soup(html):
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def main():
    html = _fetch_html(NIKKEI_PER_URL)
    if not html:
        if _load_existing_payload(require_dividend=True):
            logger.warning("PER fetch failed; keeping existing data.")
            return 0
        logger.error("PER fetch failed and no valid local data found.")
        return 1
    soup = _build_soup(html)
    per_result = _extract_latest_values(
        soup,
        PER_AVERAGE_HEADER_TERMS,
        PER_AVERAGE_KEY_TERMS,
        PER_AVERAGE_KEY,
    )
    if not _is_valid_result(per_result, PER_AVERAGE_KEY):
        if _load_existing_payload(require_dividend=True):
            logger.warning("PER parse failed; keeping existing data.")
            return 0
        logger.error("PER data parse failed and no valid local data found.")
        return 1

    dividend_html = _fetch_html(NIKKEI_DIVIDEND_URL)
    if not dividend_html:
        if _load_existing_payload(require_dividend=True):
            logger.warning("Dividend yield fetch failed; keeping existing data.")
            return 0
        logger.error("Dividend yield fetch failed and no valid local data found.")
        return 1
    dividend_soup = _build_soup(dividend_html)
    dividend_result = _extract_latest_values(
        dividend_soup,
        DIVIDEND_AVERAGE_HEADER_TERMS,
        DIVIDEND_AVERAGE_KEY_TERMS,
        DIVIDEND_AVERAGE_KEY,
    )
    if not _is_valid_result(dividend_result, DIVIDEND_AVERAGE_KEY):
        if _load_existing_payload(require_dividend=True):
            logger.warning("Dividend yield parse failed; keeping existing data.")
            return 0
        logger.error(
            "Dividend yield data parse failed and no valid local data found."
        )
        return 1

    payload = {
        "source": NIKKEI_PER_URL,
        "fetched_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat()
        + "Z",
        "date": per_result.get("date"),
        "index_based": per_result["index_based"],
        PER_AVERAGE_KEY: per_result[PER_AVERAGE_KEY],
        DIVIDEND_YIELD_KEY: {
            "source": NIKKEI_DIVIDEND_URL,
            "date": dividend_result.get("date"),
            "index_based": dividend_result["index_based"],
            DIVIDEND_AVERAGE_KEY: dividend_result[DIVIDEND_AVERAGE_KEY],
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
