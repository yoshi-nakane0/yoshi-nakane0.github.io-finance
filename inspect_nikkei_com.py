
import requests
from bs4 import BeautifulSoup

def inspect_nikkei_com():
    url = "https://www.nikkei.com/markets/kabu/japanidx/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    }
    
    print(f"Fetching {url}...")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        
        soup = BeautifulSoup(response.text, "lxml")
        
        tables = soup.find_all("table")
        print(f"Found {len(tables)} tables.")
        
        for i, table in enumerate(tables):
            text = table.get_text(strip=True)
            if "株価収益率" in text or "日経平均" in text:
                print(f"--- Table {i} ---")
                # Print headers
                headers = []
                thead = table.find("thead")
                if thead:
                    headers = [th.get_text(strip=True) for th in thead.find_all(["th", "td"])]
                
                print(f"Headers: {headers}")
                
                # Print rows
                rows = table.find_all("tr")
                for j, row in enumerate(rows[:5]): # First 5 rows
                    cols = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
                    print(f"Row {j}: {cols}")
                
                if "前期基準" in text and "予想" in text:
                    print(">>> This table looks promising (has '前期基準' and '予想')")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_nikkei_com()
