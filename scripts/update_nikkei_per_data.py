import datetime
import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

NIKKEI_PER_URL = "https://indexes.nikkei.co.jp/nkave/archives/data?list=per"
REQUEST_TIMEOUT_SEC = (5, 15)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Connection": "close",
}
FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")
DATE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")
OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "BaseCalc"
    / "data"
    / "nikkei_per.json"
)


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


def _extract_latest_per_values(soup):
    if soup is None:
        return None
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = rows[0].find_all(["th", "td"])
        if not header_cells:
            continue
        index_col = None
        weighted_col = None
        for idx, cell in enumerate(header_cells):
            text = cell.get_text(strip=True)
            if "指数ベース" in text:
                index_col = idx
            if "加重平均" in text:
                weighted_col = idx
        if index_col is None and weighted_col is None:
            continue
        for row in reversed(rows[1:]):
            cols = row.find_all(["td", "th"])
            if not cols:
                continue
            index_val = None
            weighted_val = None
            if index_col is not None and len(cols) > index_col:
                index_val = _parse_float(cols[index_col].get_text(strip=True))
            if weighted_col is not None and len(cols) > weighted_col:
                weighted_val = _parse_float(cols[weighted_col].get_text(strip=True))
            if index_val is None and weighted_val is None:
                continue
            date_text = cols[0].get_text(strip=True) if cols else None
            if date_text and not DATE_RE.match(date_text):
                date_text = None
            return {
                "date": date_text,
                "index_based": index_val,
                "weighted_average": weighted_val,
            }
    return None


def main():
    response = requests.get(
        NIKKEI_PER_URL,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    result = _extract_latest_per_values(soup)
    if not result:
        raise RuntimeError("PER data table not found in source HTML.")
    if result.get("index_based") is None or result.get("weighted_average") is None:
        raise RuntimeError("PER values missing from source data.")

    payload = {
        "source": NIKKEI_PER_URL,
        "fetched_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat()
        + "Z",
        "date": result.get("date"),
        "index_based": result["index_based"],
        "weighted_average": result["weighted_average"],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
