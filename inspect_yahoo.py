from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import time

def inspect_yahoo():
    url = "https://finance.yahoo.com/quote/1329.T/"
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    
    try:
        driver.get(url)
        time.sleep(5) # 待機
        
        page_source = driver.page_source
        
        # PE Ratio (TTM) を含む周辺を表示
        if "PE Ratio (TTM)" in page_source:
            print("Found 'PE Ratio (TTM)'")
            # 単純なgrep的な探索
            lines = page_source.split('\n')
            for i, line in enumerate(lines):
                if "PE Ratio (TTM)" in line:
                    print(f"Line {i}: {line[:500]}...") # 長すぎるのでカット
        else:
            print("Could not find 'PE Ratio (TTM)' text")
            
        # ユーザー指定のクラスがあるか確認
        if "yf-6myrf1" in page_source:
            print("Found class 'yf-6myrf1'")
        else:
            print("Could not find class 'yf-6myrf1'")

    except Exception as e:
        print(e)
    finally:
        driver.quit()

if __name__ == "__main__":
    inspect_yahoo()
