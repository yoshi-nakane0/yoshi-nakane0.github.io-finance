"""
コードの説明
このコードは、Seleniumを使用してTradingViewのチャートページにアクセスし、異なる時間足のスクリーンショットを取得してDropbox APIへアップロードするPythonスクリプトです。refresh token を優先して短命 access token を都度取得し、生成した画像4枚をDropboxへ上書き保存します。
"""
# -*- coding: utf-8 -*-
import json
import os
import sys
import time

import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DROPBOX_API_URL = "https://api.dropboxapi.com/2/files/create_folder_v2"
DROPBOX_CONTENT_API_URL = "https://content.dropboxapi.com/2/files/upload"
DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
DEFAULT_DROPBOX_UPLOAD_DIR = "/trade/AI/ChartData"
DEFAULT_USER_DATA_DIR = os.path.join(BASE_DIR, "chrome_profile")
DROPBOX_REQUEST_TIMEOUT = 60
DROPBOX_MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
TIMEFRAMES = [
    {"name": "1時間", "data_value": "60", "filename": "View_1hour.png"},
    {"name": "4時間", "data_value": "240", "filename": "View_4hour.png"},
    {"name": "日足", "data_value": "1D", "filename": "View_daily.png"},
    {"name": "週足", "data_value": "1W", "filename": "View_weekly.png"},
]
DROPBOX_TOKEN_CACHE = {"access_token": None, "expires_at": 0.0}

def getenv_str(name, default=None):
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    if not value:
        return default
    return value

DROPBOX_UPLOAD_DIR = getenv_str("DROPBOX_UPLOAD_DIR", DEFAULT_DROPBOX_UPLOAD_DIR)
DROPBOX_ACCESS_TOKEN = getenv_str("DROPBOX_ACCESS_TOKEN")
DROPBOX_REFRESH_TOKEN = getenv_str("DROPBOX_REFRESH_TOKEN")
DROPBOX_APP_KEY = getenv_str("DROPBOX_APP_KEY")
DROPBOX_APP_SECRET = getenv_str("DROPBOX_APP_SECRET")
USER_DATA_DIR = getenv_str("CHART_USER_DATA_DIR", DEFAULT_USER_DATA_DIR)
EMAIL = getenv_str("TRADINGVIEW_EMAIL", "n22_y01@yahoo.co.jp")
PASSWORD = getenv_str("TRADINGVIEW_PASSWORD", "NakaYos912")

def normalize_dropbox_dir(path):
    normalized = (path or DEFAULT_DROPBOX_UPLOAD_DIR).strip()
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/")

def request_dropbox_access_token():
    if not DROPBOX_REFRESH_TOKEN:
        if DROPBOX_ACCESS_TOKEN:
            return DROPBOX_ACCESS_TOKEN
        raise RuntimeError("Dropbox credentials are not set.")
    if not DROPBOX_APP_KEY or not DROPBOX_APP_SECRET:
        raise RuntimeError(
            "DROPBOX_APP_KEY and DROPBOX_APP_SECRET are required with DROPBOX_REFRESH_TOKEN."
        )
    response = None
    for attempt in range(1, DROPBOX_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                DROPBOX_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": DROPBOX_REFRESH_TOKEN,
                    "client_id": DROPBOX_APP_KEY,
                    "client_secret": DROPBOX_APP_SECRET,
                },
                timeout=DROPBOX_REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            if attempt == DROPBOX_MAX_ATTEMPTS:
                raise
            time.sleep(attempt)
            continue
        if response.status_code in RETRYABLE_STATUS_CODES and attempt < DROPBOX_MAX_ATTEMPTS:
            time.sleep(attempt)
            continue
        response.raise_for_status()
        break
    payload = response.json()
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in", 0)
    if not access_token:
        raise RuntimeError("Dropbox token response did not include access_token.")
    DROPBOX_TOKEN_CACHE["access_token"] = access_token
    DROPBOX_TOKEN_CACHE["expires_at"] = time.time() + max(int(expires_in) - 60, 0)
    return access_token

def get_dropbox_access_token(force_refresh=False):
    if DROPBOX_REFRESH_TOKEN:
        if not force_refresh:
            cached_access_token = DROPBOX_TOKEN_CACHE["access_token"]
            if cached_access_token and time.time() < DROPBOX_TOKEN_CACHE["expires_at"]:
                return cached_access_token
        return request_dropbox_access_token()
    if DROPBOX_ACCESS_TOKEN:
        return DROPBOX_ACCESS_TOKEN
    raise RuntimeError(
        "Configure DROPBOX_REFRESH_TOKEN with DROPBOX_APP_KEY and DROPBOX_APP_SECRET, "
        "or set DROPBOX_ACCESS_TOKEN."
    )

def dropbox_api_headers(force_refresh=False):
    return {"Authorization": f"Bearer {get_dropbox_access_token(force_refresh=force_refresh)}"}

def dropbox_post(url, *, headers=None, json_body=None, data=None):
    last_error = None
    force_refresh = False
    for attempt in range(1, DROPBOX_MAX_ATTEMPTS + 1):
        request_headers = dropbox_api_headers(force_refresh=force_refresh)
        if headers:
            request_headers.update(headers)
        try:
            response = requests.post(
                url,
                headers=request_headers,
                json=json_body,
                data=data,
                timeout=DROPBOX_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt == DROPBOX_MAX_ATTEMPTS:
                raise
            time.sleep(attempt)
            continue
        if response.status_code == 401 and DROPBOX_REFRESH_TOKEN and not force_refresh:
            DROPBOX_TOKEN_CACHE["access_token"] = None
            DROPBOX_TOKEN_CACHE["expires_at"] = 0.0
            force_refresh = True
            continue
        if response.status_code in RETRYABLE_STATUS_CODES and attempt < DROPBOX_MAX_ATTEMPTS:
            time.sleep(attempt)
            force_refresh = False
            continue
        return response
    if last_error:
        raise last_error
    raise RuntimeError("Dropbox request failed without a response.")

def create_dropbox_folder(path):
    response = dropbox_post(
        DROPBOX_API_URL,
        json_body={"path": path, "autorename": False},
    )
    if response.ok:
        return
    payload = {}
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error_summary = payload.get("error_summary", "")
    if error_summary.startswith("path/conflict"):
        return
    response.raise_for_status()

def ensure_dropbox_directory(path):
    current = ""
    for part in [segment for segment in path.split("/") if segment]:
        current = f"{current}/{part}"
        create_dropbox_folder(current)

def upload_screenshot_to_dropbox(driver, filename):
    dropbox_dir = normalize_dropbox_dir(DROPBOX_UPLOAD_DIR)
    dropbox_path = f"{dropbox_dir}/{filename}"
    headers = {
        "Content-Type": "application/octet-stream",
        "Dropbox-API-Arg": json.dumps(
        {
            "path": dropbox_path,
            "mode": "overwrite",
            "autorename": False,
            "mute": True,
        }
        ),
    }
    response = dropbox_post(
        DROPBOX_CONTENT_API_URL,
        headers=headers,
        data=driver.get_screenshot_as_png(),
    )
    response.raise_for_status()
    print(f"Screenshot uploaded: {dropbox_path}")

def setup_driver():
    chrome_options = Options()    
    chrome_options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    if os.environ.get("CHART_HEADLESS") == "1":
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        chrome_options.binary_location = chrome_bin
    
    # 自動化フラグを無効化
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(options=chrome_options)
    
    # webdriverプロパティを隠す
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver

def main():
    failures = []
    driver = None
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    ensure_dropbox_directory(normalize_dropbox_dir(DROPBOX_UPLOAD_DIR))
    
    driver = setup_driver()
    
    try:
        # ステップ1: ログインページにアクセスしてログイン状態を確認
        driver.get("https://jp.tradingview.com/accounts/signin/")
        time.sleep(3)
        
        # 現在のURLを確認してログイン状態をチェック
        current_url = driver.current_url
        print(f"Current URL: {current_url}")
        
        if "signin" not in current_url:
            # 既にログイン済み、直接チャートページへ移動
            print("Already logged in! Navigating to chart page...")
        else:
            # ログインが必要
            print("Need to login...")
            wait = WebDriverWait(driver, 15)
            
            try:
                # Eメールボタンをクリック
                email_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Eメール') or @name='Eメール']")))
                email_button.click()
                print("Email button clicked.")
                
                # Eメール入力欄に入力
                email_input = wait.until(EC.presence_of_element_located((By.ID, "id_username")))
                email_input.clear()
                email_input.send_keys(EMAIL)
                print("Email entered.")
                
                # パスワード入力欄に入力
                password_input = driver.find_element(By.ID, "id_password")
                password_input.clear()
                password_input.send_keys(PASSWORD)
                print("Password entered.")
                
                # ログインボタンをクリック
                login_submit_button = driver.find_element(By.XPATH, "//button[contains(@class, 'submitButton-LQwxK8Bm')]")
                login_submit_button.click()
                print("Login form submitted.")
                
                # ログイン完了まで待機
                WebDriverWait(driver, 15).until(lambda d: "signin" not in d.current_url)
                
            except Exception as e:
                print(f"Login error: {e}")
                if sys.stdin.isatty():
                    print("Please login manually...")
                    input("After login completed, press Enter key...")
                else:
                    raise RuntimeError("Interactive login is required, but no terminal is available.") from e
        
        # チャートページに移動
        print("Navigating to chart page...")
        driver.get("https://jp.tradingview.com/chart/pnyZf6WV/?symbol=SPREADEX%3ANIKKEI")
        time.sleep(3)
        
        # 異なる時間足でスクリーンショットを撮影
        for timeframe in TIMEFRAMES:
            try:
                # 時間足ボタンを検索
                button = None
                try:
                    button = driver.find_element(By.XPATH, f"//button[@data-value='{timeframe['data_value']}']")
                except:
                    try:
                        button = driver.find_element(By.XPATH, f"//button[contains(@aria-label, '{timeframe['name']}')]")
                    except:
                        pass
                
                if button:
                    # ボタンをクリック
                    try:
                        button.click()
                    except:
                        # 通常のクリックが失敗した場合はJavaScriptクリックを試行
                        driver.execute_script("arguments[0].click();", button)
                    
                    # チャートの更新を待機
                    time.sleep(2)
                    
                    # スクリーンショットをDropboxへアップロード
                    upload_screenshot_to_dropbox(driver, timeframe["filename"])
                else:
                    message = f"Could not find {timeframe['name']} button"
                    print(message)
                    failures.append(message)
                
            except Exception as e:
                message = f"Error taking screenshot for {timeframe['name']}: {e}"
                print(message)
                failures.append(message)
        if failures:
            print("Chart upload failed.")
            return 1
        return 0
        
    except Exception as e:
        print(f"Error occurred: {e}")
        return 1
    
    finally:
        if driver is not None:
            time.sleep(2)
            driver.quit()

if __name__ == "__main__":
    raise SystemExit(main())
