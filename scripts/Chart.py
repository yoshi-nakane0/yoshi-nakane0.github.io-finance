"""
コードの説明
このコードは、Seleniumを使用してTradingViewのチャートページにアクセスし、異なる時間足のスクリーンショットを撮影するPythonスクリプトです。ログイン情報は直書きされており、ログイン状態を確認した後、指定された時間足のチャートを表示し、スクリーンショットを保存します。
"""
# -*- coding: utf-8 -*-
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import time
import os

def setup_driver():
    chrome_options = Options()    
    user_data_dir = os.path.join(os.getcwd(), "chrome_profile")
    chrome_options.add_argument(f"--user-data-dir={user_data_dir}")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    
    # 自動化フラグを無効化
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(options=chrome_options)
    
    # webdriverプロパティを隠す
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver

def main():
    # ログイン情報 - この値を変更してください
    EMAIL = "n22_y01@yahoo.co.jp"  # あなたのメールアドレスに変更
    PASSWORD = "NakaYos912"        # あなたのパスワードに変更
    
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
                print("Please login manually...")
                input("After login completed, press Enter key...")
        
        # チャートページに移動
        print("Navigating to chart page...")
        driver.get("https://jp.tradingview.com/chart/pnyZf6WV/?symbol=SPREADEX%3ANIKKEI")
        time.sleep(3)
        
        # 異なる時間足でスクリーンショットを撮影
        timeframes = [
            {"name": "1時間", "data_value": "60", "filename": "1hour"},
            {"name": "4時間", "data_value": "240", "filename": "4hour"},
            {"name": "日足", "data_value": "1D", "filename": "daily"},
            {"name": "週足", "data_value": "1W", "filename": "weekly"}
        ]
        
        for timeframe in timeframes:
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
                    
                    # スクリーンショットを撮影
                    screenshot_path = os.path.join(os.getcwd(), f"View_{timeframe['filename']}.png")
                    driver.save_screenshot(screenshot_path)
                    print(f"{timeframe['name']} screenshot saved: {screenshot_path}")
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