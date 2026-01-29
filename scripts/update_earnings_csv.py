import csv
import random
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, 'static', 'earning', 'data', 'data.csv')

def generate_trend_string(base_val=None, count=8):
    points = []
    current = base_val if base_val is not None else random.uniform(-5, 20)
    for _ in range(count):
        # random walk
        change = random.uniform(-5, 5)
        current += change
        points.append(f"{current:.1f}")
    return ",".join(points)

def update_csv():
    rows = []
    if not os.path.exists(CSV_PATH):
        print(f"File not found: {CSV_PATH}")
        return

    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        # Add new columns if they don't exist
        new_cols = ['revenue_trend', 'eps_trend', 'surprise_trend']
        for col in new_cols:
            if col not in fieldnames:
                fieldnames.append(col)
        
        for row in reader:
            # Generate absolute values for new UI requirements
            # Mock base values based on market or random
            is_jp = row.get('market') == 'TSE'
            base_sales = random.uniform(100, 1000) if is_jp else random.uniform(10, 100) # Billion Yen or Billion USD
            base_eps = random.uniform(50, 500) if is_jp else random.uniform(1, 10)

            # Helpers
            def fuzz(val): return val * random.uniform(0.9, 1.1)

            # Sales
            row['sales_current'] = f"{base_sales:.1f}"
            row['sales_forecast'] = f"{fuzz(base_sales):.1f}"
            row['sales_4q_ago'] = f"{fuzz(base_sales * 0.9):.1f}" # Assume some growth
            # "4期前の同時期" - treating as same as 4q ago for now, or maybe T-8? 
            # Let's assume user means T-4.
            row['sales_4q_prior_period'] = row['sales_4q_ago']

            # EPS
            # Try to parse next_consensus if available
            nc = row.get('next_consensus', '')
            if 'EPS' in nc:
                try:
                    row['eps_forecast'] = nc.replace('EPS', '').strip()
                except:
                    row['eps_forecast'] = f"{fuzz(base_eps):.2f}"
            else:
                row['eps_forecast'] = f"{fuzz(base_eps):.2f}"
            
            # Current EPS (derive from forecast roughly)
            try:
                fcast = float(row['eps_forecast'])
                row['eps_current'] = f"{fcast * random.uniform(0.9, 1.1):.2f}"
            except:
                row['eps_current'] = f"{base_eps:.2f}"

            # 4Q Ago EPS
            try:
                curr = float(row['eps_current'])
                row['eps_4q_ago'] = f"{curr * random.uniform(0.8, 1.2):.2f}"
            except:
                row['eps_4q_ago'] = f"{base_eps * 0.9:.2f}"
            
            row['eps_4q_prior_period'] = row['eps_4q_ago']
            
            # Surprise values (Percentage strings)
            # EPS Surprise is already roughly 'surprise_rate' in original data, but let's ensure we have a dedicated formatted one if needed.
            # actually we can just use the generated current vs forecast for consistency if we wanted, 
            # but let's just generate a specific display value.
            row['sales_surprise'] = f"{random.uniform(-5, 8):.2f}"
            row['eps_surprise'] = f"{random.uniform(-10, 15):.2f}"

            rows.append(row)

    with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
        # Define fields to keep (original + new absolute values, excluding trends if we want to clean completely)
        # But DictWriter needs to know if we are keeping old columns in the file or not. 
        # If we just write 'rows' which contains 'revenue_trend' keys (from read), they will be written if in fieldnames.
        # But wait, 'rows' comes from 'reader'. Reader has 'revenue_trend' if the file currently has it.
        # We want to remove them from the file?
        # The prompt says "delete items associated".
        # So I should remove 'revenue_trend', 'eps_trend', 'surprise_trend', 'trend_4q' from fieldnames and row dicts.
        
        cols_to_remove = ['revenue_trend', 'eps_trend', 'surprise_trend', 'trend_4q']
        final_fieldnames = [f for f in fieldnames if f not in cols_to_remove]
        
        # Ensure new columns are in fieldnames
        new_cols = ['sales_current', 'sales_forecast', 'sales_4q_ago', 'sales_4q_prior_period', 'eps_current', 'eps_forecast', 'eps_4q_ago', 'eps_4q_prior_period', 'sales_surprise', 'eps_surprise']
        for col in new_cols:
            if col not in final_fieldnames:
                final_fieldnames.append(col)

        # Clean rows
        cleaned_rows = []
        for r in rows:
            for col in cols_to_remove:
                if col in r:
                    del r[col]
            cleaned_rows.append(r)

        writer = csv.DictWriter(f, fieldnames=final_fieldnames)
        writer.writeheader()
        writer.writerows(cleaned_rows)
    
    print(f"Updated {len(cleaned_rows)} rows in {CSV_PATH}")

if __name__ == "__main__":
    update_csv()
