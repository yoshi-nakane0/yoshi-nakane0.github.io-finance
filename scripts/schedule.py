"""
コードの説明
このスクリプトは、Forex Factoryのカレンダーから特定の通貨（USD, JPY）の経済イベントをスクレイピングし、CSVファイルに出力するものです。
"""
import time
import csv
import datetime
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException
)

# ─────────── ここから設定項目 ───────────
# 月指定（年は 2026 固定）
MONTH_MAP = {
    1: "jan", 2: "feb", 3: "mar", 4: "apr",
    5: "may", 6: "jun", 7: "jul", 8: "aug",
    9: "sep", 10: "oct", 11: "nov", 12: "dec"
}
FIXED_YEAR = 2026

print("スクレイピング対象の月を数字で指定してください（年は 2025 固定）")
print("例: 1,2,3")
while True:
    raw_months = input("月番号を入力 (1-12, カンマ区切り): ").strip()
    if not raw_months:
        print("1〜12の数字をカンマ区切りで入力してください。")
        continue
    parts = [p.strip() for p in raw_months.split(",") if p.strip()]
    if not parts:
        print("1〜12の数字をカンマ区切りで入力してください。")
        continue
    valid = True
    month_numbers = []
    for p in parts:
        if not p.isdigit():
            valid = False
            break
        m = int(p)
        if m < 1 or m > 12:
            valid = False
            break
        month_numbers.append(m)
    if valid and month_numbers:
        break
    print("1〜12の数字をカンマ区切りで入力してください。")

SCRAPE_URLS = [
    f"https://www.forexfactory.com/calendar?month={MONTH_MAP[m]}.{FIXED_YEAR}"
    for m in month_numbers
]

# 取得対象の通貨
TARGET_CURRENCIES = ["USD", "JPY"]

# CSV 出力ファイル名
OUTPUT_CSV = "/Users/naka/yoshi-nakane0.github.io-finance/static/events/data.csv"

# ページ読み込み後およびスクロール後に停止する秒数（必要に応じて変更可）
WAIT_SEC = 3

# ページ読み込みの再試行回数と待機秒数
PAGE_RETRY_MAX = 2
PAGE_RETRY_WAIT_SEC = 5

# スクロール→待機を繰り返す回数
SCROLL_ROUNDS = 18

# 一度にスクロールするピクセル数（必要に応じて変更可）
SCROLL_PIXELS = 600
# ─────────── ここまで設定項目 ───────────

def scrape_forex_factory_calendar_data(url_list):
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    )
    # 英語サイトを対象にする
    options.add_argument("--lang=en-US")
    options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    calendar_events_data = []
    driver = None

    try:
        driver_path = ChromeDriverManager().install()
        # mac64/ ... /THIRD_PARTY_NOTICES.chromedriver が返ってくる場合の対策
        if "THIRD_PARTY_NOTICES.chromedriver" in driver_path:
            driver_path = os.path.join(os.path.dirname(driver_path), "chromedriver")
            # 念のため実行権限を付与
            try:
                os.chmod(driver_path, 0o755)
            except Exception:
                pass

        def create_driver():
            new_driver = webdriver.Chrome(
                service=ChromeService(driver_path),
                options=options
            )
            new_driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"}
            )
            return new_driver, WebDriverWait(new_driver, 30)

        total_steps = max(1, len(url_list) * (SCROLL_ROUNDS + 2))
        completed_steps = 0

        def update_progress():
            percent = int((completed_steps / total_steps) * 100)
            print(f"\r進捗: {percent}%", end="", flush=True)

        def load_calendar_page(active_driver, active_wait, target_url):
            for attempt in range(PAGE_RETRY_MAX + 1):
                if attempt > 0:
                    time.sleep(PAGE_RETRY_WAIT_SEC * attempt)
                active_driver.get(target_url)
                try:
                    active_wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
                    active_wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "tr.calendar__row")) > 0)
                    return True
                except TimeoutException:
                    continue
            return False

        for url in url_list:
            driver, wait = create_driver()
            try:
                if not load_calendar_page(driver, wait, url):
                    print()
                    print(f"エラー: カレンダー行が時間内に見つかりませんでした。URL: {url}")
                    completed_steps += (SCROLL_ROUNDS + 2)
                    update_progress()
                    continue

                # ページが完全に開いたら WAIT_SEC 秒停止
                time.sleep(WAIT_SEC)
                completed_steps += 1
                update_progress()

                # SCROLL_ROUNDS 回ループ：指定したピクセル分ずつスクロールして WAIT_SEC 秒停止
                for _ in range(SCROLL_ROUNDS):
                    driver.execute_script(f"window.scrollBy(0, {SCROLL_PIXELS});")
                    time.sleep(WAIT_SEC)
                    completed_steps += 1
                    update_progress()

                # イベント行を取得して必要なデータを抜き出す
                all_rows = driver.find_elements(By.CSS_SELECTOR, "tr.calendar__row")
                last_date_raw = ""
                last_time = ""
                current_year = datetime.datetime.now().year

                for row in all_rows:
                    try:
                        # （１）日付取得
                        date_cells = row.find_elements(By.CSS_SELECTOR, "td.calendar__cell.calendar__date span.date")
                        if date_cells:
                            last_date_raw = date_cells[0].text.strip()

                        # （２）日付変換
                        if last_date_raw:
                            parts = last_date_raw.split()
                            month_day_str = f"{parts[1]} {parts[2]} {current_year}"
                            try:
                                dt = datetime.datetime.strptime(month_day_str, "%b %d %Y")
                                date_iso = dt.strftime("%Y-%m-%d")
                            except ValueError:
                                date_iso = ""
                        else:
                            date_iso = ""

                        # （３）時間取得と24時間表記への変換
                        time_cells = row.find_elements(By.CSS_SELECTOR, "td.calendar__cell.calendar__time span")
                        if time_cells and time_cells[0].text.strip():
                            last_time = time_cells[0].text.strip()
                        raw_time = last_time.strip()
                        if raw_time.lower() in ("tentative", "all day"):
                            time_24 = "00:00"
                        else:
                            try:
                                dt_time = datetime.datetime.strptime(raw_time, "%I:%M%p")
                                time_24 = dt_time.strftime("%H:%M")
                            except ValueError:
                                time_24 = raw_time

                        # （４）通貨フィルタリング
                        currency_span = row.find_element(By.CSS_SELECTOR, "td.calendar__cell.calendar__currency span")
                        currency = currency_span.text.strip()
                        if currency not in TARGET_CURRENCIES:
                            continue

                        # （５）イベントタイトル
                        title_span = row.find_element(By.CSS_SELECTOR, "span.calendar__event-title")
                        event_title = title_span.text.strip()
                        if not event_title:
                            continue

                        # （６）インパクト判定
                        impact_span = row.find_element(By.CSS_SELECTOR, "td.calendar__cell.calendar__impact span")
                        impact_text = impact_span.get_attribute("title").strip()
                        if "Low Impact" in impact_text:
                            impact_stars = "★"
                        elif "Medium Impact" in impact_text:
                            impact_stars = "★★"
                        elif "High Impact" in impact_text:
                            impact_stars = "★★★"
                        else:
                            impact_stars = ""

                        calendar_events_data.append({
                            "date": date_iso,
                            "time": time_24,
                            "currency": currency,
                            "event": event_title,
                            "impact": impact_stars
                        })

                    except (NoSuchElementException, StaleElementReferenceException):
                        continue

                completed_steps += 1
                update_progress()
                time.sleep(PAGE_RETRY_WAIT_SEC)
            finally:
                if driver:
                    driver.quit()
                    driver = None

    except Exception as e:
        print(f"予期せぬエラーが発生しました: {e}")
    finally:
        if driver:
            driver.quit()
        print()

    return calendar_events_data

def save_to_csv(data_list, path=OUTPUT_CSV):
    fieldnames = ["date", "time", "currency", "event", "impact"]
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data_list)
        print(f"CSV に出力しました → {path}")
    except Exception as e:
        print(f"CSV 出力エラー: {e}")

if __name__ == "__main__":
    events = scrape_forex_factory_calendar_data(SCRAPE_URLS)
    if events:
        save_to_csv(events)
        print(f"取得件数: {len(events)} 件")
    else:
        print("対象通貨のイベントが取得できませんでした。")
