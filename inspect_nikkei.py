
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time

def inspect_nikkei_summary():
    url = "https://indexes.nikkei.co.jp/nkave/archives/summary"
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    print(f"Fetching {url} with Selenium...")
    driver = None
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.get(url)
        time.sleep(5) # Wait for page load/JS
        
        html = driver.page_source
        soup = BeautifulSoup(html, "lxml")
        
        # Find all "指数ベース" and their context
        ibs = soup.find_all(string=lambda t: "指数ベース" in t if t else False)
        print(f"Found {len(ibs)} occurrences of '指数ベース'")
        
        for i, ib in enumerate(ibs):
            print(f"--- Occurrence {i} ---")
            parent = ib.parent
            print(f"Tag: {parent.name}, Class: {parent.get('class')}")
            
            # Value seems to be next sibling
            val = parent.find_next_sibling()
            if val:
                print(f"Value Tag: {val.name}, Class: {val.get('class')}, Text: {val.get_text(strip=True)}")
            
            # Try to find the section title.
            # Traverse up and find previous siblings or parents' previous siblings
            curr = parent
            found_title = None
            for _ in range(10): # Limit depth
                if not curr: break
                # Check for title-link in previous siblings of current or parents
                prevs = curr.find_previous_siblings()
                for p in prevs:
                    if p.find("a", class_="title-link"):
                        found_title = p.find("a", class_="title-link").get_text(strip=True)
                        break
                    # Maybe the title is just text in a div
                    if "株価収益率" in p.get_text():
                        found_title = p.get_text(strip=True)
                        break
                
                if found_title: break
                curr = curr.parent
            
            print(f"Guessed Section Title: {found_title}")

        # Also check PER link context
        per_link = soup.find("a", string=lambda t: "株価収益率" in t if t else False)
        if per_link:
            print("--- PER Link Context ---")
            print(per_link.parent.parent.prettify()[:1000]) # Print containing div/row


    except Exception as e:
        print(f"Error: {e}")
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    inspect_nikkei_summary()
