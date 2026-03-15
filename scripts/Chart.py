"""
コードの説明
このコードは、Seleniumを使用してTradingViewのチャートページにアクセスし、異なる時間足のスクリーンショットを取得してDropbox APIへアップロードするPythonスクリプトです。refresh token を優先して短命 access token を都度取得し、生成した画像4枚をDropboxへ上書き保存します。
"""
# -*- coding: utf-8 -*-
import json
import os
import re
import sys
import time

import requests
from selenium import webdriver
from selenium.common.exceptions import (
    JavascriptException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DROPBOX_API_URL = "https://api.dropboxapi.com/2/files/create_folder_v2"
DROPBOX_CONTENT_API_URL = "https://content.dropboxapi.com/2/files/upload"
DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
DEFAULT_DROPBOX_UPLOAD_DIR = "/trade/AI/ChartData"
DEFAULT_USER_DATA_DIR = os.path.join(BASE_DIR, "chrome_profile")
TRADINGVIEW_SIGNIN_URL = "https://jp.tradingview.com/accounts/signin/"
TRADINGVIEW_CHART_URL = "https://jp.tradingview.com/chart/pnyZf6WV/?symbol=SPREADEX%3ANIKKEI"
DROPBOX_REQUEST_TIMEOUT = 60
DROPBOX_MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
LOGIN_TIMEOUT = 60
CHART_LOAD_TIMEOUT = 30
LOGIN_MAX_ATTEMPTS = 2
ACTIVE_CLASS_PATTERN = re.compile(r"(^|[\s_-])(active|selected|checked|isactive)([\s_-]|$)")
CHART_NOT_FOUND_TEXTS = (
    "このチャートレイアウトを開くことができません",
    "Chart Not Found",
)
CHART_RENDER_STABLE_POLLS = 3
CHART_RENDER_POLL_INTERVAL = 0.5
TIMEFRAMES = [
    {"name": "1時間", "data_value": "60", "filename": "View_1hour.png"},
    {"name": "4時間", "data_value": "240", "filename": "View_4hour.png"},
    {"name": "日足", "data_value": "1D", "filename": "View_daily.png"},
    {"name": "週足", "data_value": "1W", "filename": "View_weekly.png"},
]
DROPBOX_TOKEN_CACHE = {"access_token": None, "expires_at": 0.0}
LOGIN_INPUT_LOCATORS = [
    (By.CSS_SELECTOR, "input#id_username"),
    (By.CSS_SELECTOR, "input[name='username']"),
    (By.CSS_SELECTOR, "input[name='email']"),
    (By.CSS_SELECTOR, "input[type='email']"),
]
PASSWORD_INPUT_LOCATORS = [
    (By.CSS_SELECTOR, "input#id_password"),
    (By.CSS_SELECTOR, "input[name='password']"),
    (By.CSS_SELECTOR, "input[type='password']"),
]
EMAIL_LOGIN_TRIGGER_LOCATORS = [
    (By.CSS_SELECTOR, "[data-name='email']"),
    (By.CSS_SELECTOR, "button[name='email']"),
    (By.CSS_SELECTOR, "button[aria-label*='mail']"),
    (By.CSS_SELECTOR, "button[title*='mail']"),
    (
        By.XPATH,
        "//button[contains(translate(normalize-space(.), "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'email')]",
    ),
    (By.XPATH, "//button[contains(normalize-space(.), 'Eメール')]"),
]

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
DROPBOX_REFRESH_TOKEN = getenv_str("DROPBOX_REFRESH_TOKEN", getenv_str("REFRESH_TOKEN"))
DROPBOX_APP_KEY = getenv_str("DROPBOX_APP_KEY", getenv_str("APP_KEY"))
DROPBOX_APP_SECRET = getenv_str("DROPBOX_APP_SECRET", getenv_str("APP_SECRET"))
USER_DATA_DIR = getenv_str("CHART_USER_DATA_DIR", DEFAULT_USER_DATA_DIR)
EMAIL = getenv_str("TRADINGVIEW_EMAIL")
PASSWORD = getenv_str("TRADINGVIEW_PASSWORD")

def normalize_dropbox_dir(path):
    normalized = (path or DEFAULT_DROPBOX_UPLOAD_DIR).strip()
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/")


def require_env_value(name, value):
    if value:
        return value
    raise RuntimeError(f"{name} is required.")


def require_tradingview_credentials():
    return (
        require_env_value("TRADINGVIEW_EMAIL", EMAIL),
        require_env_value("TRADINGVIEW_PASSWORD", PASSWORD),
    )

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
    if response.status_code == 401:
        raise RuntimeError(
            "Dropbox authentication failed. Update DROPBOX_ACCESS_TOKEN or configure "
            "DROPBOX_REFRESH_TOKEN with DROPBOX_APP_KEY and DROPBOX_APP_SECRET."
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
    if response.status_code == 401:
        raise RuntimeError(
            "Dropbox authentication failed. Update DROPBOX_ACCESS_TOKEN or configure "
            "DROPBOX_REFRESH_TOKEN with DROPBOX_APP_KEY and DROPBOX_APP_SECRET."
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


def wait_for_document_ready(driver, timeout):
    def document_ready(current_driver):
        try:
            return current_driver.execute_script("return document.readyState") == "complete"
        except JavascriptException:
            return False

    WebDriverWait(driver, timeout).until(document_ready)


def find_first_visible_element(driver, locators, *, require_enabled=False):
    for by, value in locators:
        for element in driver.find_elements(by, value):
            try:
                if not element.is_displayed():
                    continue
                if require_enabled and not element.is_enabled():
                    continue
                return element
            except StaleElementReferenceException:
                continue
    return None


def wait_for_visible_element(driver, locators, timeout, description):
    def locate(current_driver):
        element = find_first_visible_element(current_driver, locators)
        return element if element is not None else False

    return WebDriverWait(driver, timeout).until(
        locate,
        message=f"Timed out waiting for {description}.",
    )


def wait_for_clickable_element(driver, locators, timeout, description):
    def locate(current_driver):
        element = find_first_visible_element(current_driver, locators, require_enabled=True)
        return element if element is not None else False

    return WebDriverWait(driver, timeout).until(
        locate,
        message=f"Timed out waiting for {description}.",
    )


def click_element(driver, element):
    try:
        element.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", element)


def is_on_signin_page(driver):
    return "/accounts/signin" in driver.current_url


def login_form_is_visible(driver):
    email_input = find_first_visible_element(driver, LOGIN_INPUT_LOCATORS)
    password_input = find_first_visible_element(driver, PASSWORD_INPUT_LOCATORS)
    return email_input is not None and password_input is not None


def open_signin_page(driver):
    driver.get(TRADINGVIEW_SIGNIN_URL)
    wait_for_document_ready(driver, LOGIN_TIMEOUT)
    print(f"Current URL: {driver.current_url}")


def open_email_login_form(driver):
    if login_form_is_visible(driver):
        return
    trigger = wait_for_clickable_element(
        driver,
        EMAIL_LOGIN_TRIGGER_LOCATORS,
        LOGIN_TIMEOUT,
        "TradingView email login trigger",
    )
    click_element(driver, trigger)
    wait_for_visible_element(driver, LOGIN_INPUT_LOCATORS, LOGIN_TIMEOUT, "TradingView email input")
    wait_for_visible_element(
        driver,
        PASSWORD_INPUT_LOCATORS,
        LOGIN_TIMEOUT,
        "TradingView password input",
    )


def submit_login_form(driver, password_input):
    try:
        form = password_input.find_element(By.XPATH, "./ancestor::form[1]")
        driver.execute_script(
            """
            const form = arguments[0];
            if (typeof form.requestSubmit === "function") {
                form.requestSubmit();
            } else {
                form.submit();
            }
            """,
            form,
        )
        return
    except (JavascriptException, NoSuchElementException, WebDriverException):
        password_input.send_keys(Keys.ENTER)


def wait_for_login_complete(driver):
    def login_complete(current_driver):
        return not is_on_signin_page(current_driver) or not login_form_is_visible(current_driver)

    WebDriverWait(driver, LOGIN_TIMEOUT).until(
        login_complete,
        message="Timed out waiting for TradingView sign-in to complete.",
    )


def session_can_access_chart(driver):
    driver.get(TRADINGVIEW_CHART_URL)
    wait_for_document_ready(driver, LOGIN_TIMEOUT)
    if is_on_signin_page(driver):
        return False
    ensure_chart_page_available(driver)
    return True


def build_timeframe_locators(timeframe):
    return [
        (By.CSS_SELECTOR, f"button[data-value='{timeframe['data_value']}']"),
        (By.XPATH, f"//button[@data-value='{timeframe['data_value']}']"),
        (By.XPATH, f"//button[contains(@aria-label, '{timeframe['name']}')]"),
    ]


def build_all_timeframe_locators():
    locators = []
    for timeframe in TIMEFRAMES:
        locators.extend(build_timeframe_locators(timeframe))
    return locators


def wait_for_visible_chart_canvas(driver, timeout):
    def canvas_visible(current_driver):
        try:
            return current_driver.execute_script(
                """
                return Array.from(document.querySelectorAll("canvas")).some((canvas) => {
                    const rect = canvas.getBoundingClientRect();
                    const style = window.getComputedStyle(canvas);
                    return rect.width > 0 &&
                        rect.height > 0 &&
                        style.display !== "none" &&
                        style.visibility !== "hidden";
                });
                """
            )
        except JavascriptException:
            return False

    WebDriverWait(driver, timeout).until(
        canvas_visible,
        message="Timed out waiting for the TradingView chart canvas.",
    )


def get_chart_render_signature(driver):
    try:
        return driver.execute_script(
            """
            return Array.from(document.querySelectorAll("canvas"))
                .map((canvas) => {
                    const rect = canvas.getBoundingClientRect();
                    const style = window.getComputedStyle(canvas);
                    if (
                        rect.width <= 0 ||
                        rect.height <= 0 ||
                        style.display === "none" ||
                        style.visibility === "hidden"
                    ) {
                        return null;
                    }

                    const area = rect.width * rect.height;
                    return { area, canvas };
                })
                .filter(Boolean)
                .sort((left, right) => right.area - left.area)
                .slice(0, 4)
                .map(({ canvas }) => {
                    try {
                        const image = canvas.toDataURL("image/png");
                        return [
                            canvas.width,
                            canvas.height,
                            image.length,
                            image.slice(0, 64),
                            image.slice(-64),
                        ].join(":");
                    } catch (error) {
                        return [
                            canvas.width,
                            canvas.height,
                            canvas.childElementCount,
                        ].join(":");
                    }
                })
                .join("|");
            """
        )
    except JavascriptException:
        return ""


def wait_for_chart_render_complete(driver, timeout, previous_signature=None, require_change=False):
    wait_for_visible_chart_canvas(driver, timeout)
    deadline = time.time() + timeout
    last_signature = None
    stable_polls = 0
    chart_changed = previous_signature is None or not require_change

    while time.time() < deadline:
        signature = get_chart_render_signature(driver)
        if not signature:
            last_signature = None
            stable_polls = 0
            time.sleep(CHART_RENDER_POLL_INTERVAL)
            continue

        if previous_signature is not None and signature != previous_signature:
            chart_changed = True

        if chart_changed:
            if signature == last_signature:
                stable_polls += 1
            else:
                stable_polls = 1
            if stable_polls >= CHART_RENDER_STABLE_POLLS:
                return signature
        else:
            stable_polls = 0

        last_signature = signature
        time.sleep(CHART_RENDER_POLL_INTERVAL)

    raise RuntimeError("Timed out waiting for the TradingView chart render to stabilize.")


def ensure_chart_page_available(driver):
    page_text = driver.find_element(By.TAG_NAME, "body").text
    title = driver.title or ""
    if any(text in page_text or text in title for text in CHART_NOT_FOUND_TEXTS):
        raise RuntimeError(
            "TradingView chart layout is not accessible. Login may have failed or the layout requires owner access."
        )


def wait_for_chart_ready(driver):
    wait_for_document_ready(driver, CHART_LOAD_TIMEOUT)
    ensure_chart_page_available(driver)
    wait_for_visible_element(
        driver,
        build_all_timeframe_locators(),
        CHART_LOAD_TIMEOUT,
        "TradingView timeframe button",
    )
    wait_for_chart_render_complete(driver, CHART_LOAD_TIMEOUT)


def is_active_timeframe_button(button):
    try:
        for attribute in ("aria-pressed", "aria-selected", "aria-checked", "data-active"):
            if (button.get_attribute(attribute) or "").lower() == "true":
                return True
        if (button.get_attribute("data-state") or "").lower() in {"active", "selected", "checked"}:
            return True
        class_name = (button.get_attribute("class") or "").lower()
        return ACTIVE_CLASS_PATTERN.search(class_name) is not None
    except StaleElementReferenceException:
        return False


def wait_for_timeframe_button(driver, timeframe, timeout):
    return wait_for_clickable_element(
        driver,
        build_timeframe_locators(timeframe),
        timeout,
        f"TradingView timeframe button for {timeframe['name']}",
    )


def wait_for_timeframe_selected(driver, timeframe, timeout):
    locators = build_timeframe_locators(timeframe)

    def timeframe_selected(current_driver):
        button = find_first_visible_element(current_driver, locators, require_enabled=True)
        return is_active_timeframe_button(button) if button is not None else False

    WebDriverWait(driver, timeout).until(
        timeframe_selected,
        message=f"Timed out waiting for {timeframe['name']} to become active.",
    )


def login_if_needed(driver, email, password):
    open_signin_page(driver)
    if not is_on_signin_page(driver):
        print("Already logged in! Navigating to chart page...")
        return

    last_error = None
    for attempt in range(1, LOGIN_MAX_ATTEMPTS + 1):
        print(f"Need to login... ({attempt}/{LOGIN_MAX_ATTEMPTS})")
        try:
            if attempt > 1:
                open_signin_page(driver)
            open_email_login_form(driver)
            email_input = wait_for_visible_element(
                driver,
                LOGIN_INPUT_LOCATORS,
                LOGIN_TIMEOUT,
                "TradingView email input",
            )
            password_input = wait_for_visible_element(
                driver,
                PASSWORD_INPUT_LOCATORS,
                LOGIN_TIMEOUT,
                "TradingView password input",
            )
            email_input.clear()
            email_input.send_keys(email)
            print("Email entered.")
            password_input.clear()
            password_input.send_keys(password)
            print("Password entered.")
            submit_login_form(driver, password_input)
            print("Login form submitted.")
            try:
                wait_for_login_complete(driver)
                return
            except TimeoutException as error:
                print(f"Login redirect timed out: {error}")
                if session_can_access_chart(driver):
                    print("Authenticated session confirmed via chart page.")
                    return
                raise RuntimeError("TradingView sign-in did not complete.") from error
        except Exception as error:
            last_error = error
            print(f"Login attempt {attempt} failed: {error}")

    if sys.stdin.isatty():
        print("Please login manually...")
        input("After login completed, press Enter key...")
        return
    raise RuntimeError("Interactive login is required, but no terminal is available.") from last_error

def main():
    failures = []
    driver = None
    email, password = require_tradingview_credentials()
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    ensure_dropbox_directory(normalize_dropbox_dir(DROPBOX_UPLOAD_DIR))
    
    driver = setup_driver()
    
    try:
        login_if_needed(driver, email, password)
        print("Navigating to chart page...")
        driver.get(TRADINGVIEW_CHART_URL)
        wait_for_chart_ready(driver)
        
        for timeframe in TIMEFRAMES:
            try:
                button = wait_for_timeframe_button(driver, timeframe, CHART_LOAD_TIMEOUT)
                was_active = is_active_timeframe_button(button)
                previous_signature = get_chart_render_signature(driver)
                click_element(driver, button)
                wait_for_timeframe_selected(driver, timeframe, CHART_LOAD_TIMEOUT)
                wait_for_chart_render_complete(
                    driver,
                    CHART_LOAD_TIMEOUT,
                    previous_signature=previous_signature,
                    require_change=not was_active,
                )
                upload_screenshot_to_dropbox(driver, timeframe["filename"])
            except Exception as error:
                message = f"Error taking screenshot for {timeframe['name']}: {error}"
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
            driver.quit()

if __name__ == "__main__":
    raise SystemExit(main())
