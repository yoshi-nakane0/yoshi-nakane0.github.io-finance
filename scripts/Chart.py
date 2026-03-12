"""
コードの説明
このコードは、Seleniumを使用してTradingViewのチャートページにアクセスし、異なる時間足のスクリーンショットを取得してDropbox APIへアップロードするPythonスクリプトです。ログイン状態を確認した後、指定された時間足のチャートを表示し、生成した画像4枚をDropboxへ上書き保存します。
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
DEFAULT_DROPBOX_UPLOAD_DIR = "/trade/AI/ChartData"
DROPBOX_UPLOAD_DIR = os.environ.get("DROPBOX_UPLOAD_DIR", DEFAULT_DROPBOX_UPLOAD_DIR)
DROPBOX_ACCESS_TOKEN = os.environ.get("DROPBOX_ACCESS_TOKEN")
DEFAULT_USER_DATA_DIR = os.path.join(BASE_DIR, "chrome_profile")
USER_DATA_DIR = os.environ.get("CHART_USER_DATA_DIR", DEFAULT_USER_DATA_DIR)
EMAIL = os.environ.get("TRADINGVIEW_EMAIL", "n22_y01@yahoo.co.jp")
PASSWORD = os.environ.get("TRADINGVIEW_PASSWORD", "NakaYos912")
TIMEFRAMES = [
    {"name": "1時間", "data_value": "60", "filename": "View_1hour.png"},
    {"name": "4時間", "data_value": "240", "filename": "View_4hour.png"},
    {"name": "日足", "data_value": "1D", "filename": "View_daily.png"},
    {"name": "週足", "data_value": "1W", "filename": "View_weekly.png"},
]

def normalize_dropbox_dir(path):
    normalized = (path or DEFAULT_DROPBOX_UPLOAD_DIR).strip()
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/")

def dropbox_api_headers():
    if not DROPBOX_ACCESS_TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN is not set.")
    return {"Authorization": f"Bearer {DROPBOX_ACCESS_TOKEN}"}

def create_dropbox_folder(path):
    response = requests.post(
        DROPBOX_API_URL,
        headers=dropbox_api_headers(),
        json={"path": path, "autorename": False},
        timeout=30,
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
    headers = dropbox_api_headers()
    headers["Content-Type"] = "application/octet-stream"
    headers["Dropbox-API-Arg"] = json.dumps(
        {
            "path": dropbox_path,
            "mode": "overwrite",
            "autorename": False,
            "mute": True,
        }
    )
    response = requests.post(
        DROPBOX_CONTENT_API_URL,
        headers=headers,
        data=driver.get_screenshot_as_png(),
        timeout=60,
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
                time.sleep(5)
                
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
                    print(f"Could not find {timeframe['name']} button")
                
            except Exception as e:
                print(f"Error taking screenshot for {timeframe['name']}: {e}")
        
    except Exception as e:
        print(f"Error occurred: {e}")
    
    finally:
        time.sleep(2)
        driver.quit()

if __name__ == "__main__":
    main()
