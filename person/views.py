import csv
import os
from datetime import datetime
from statistics import mean
from django.conf import settings
from django.shortcuts import render

# Configuration
DOMAIN_MAPPING = {
    'Macro': 'Macro',
    'Key Remarks': 'LEADS',
    'Equities': 'Equities',
    'Leads': 'LEADS',
    'LEADS': 'LEADS',
}

DOMAIN_ORDER = ['Macro', 'Equities', 'LEADS']

# Affiliation Lookup (Name -> Affiliation)
NAME_TO_AFFILIATION = {
    "Jan Hatzius": "Goldman Sachs",
    "Bruce C. Kasman": "JPMorgan",
    "Nathan Sheets": "Citi",
    "Seth Carpenter": "Morgan Stanley",
    "Mark Zandi": "Moody’s Analytics",
    "Neil Shearing": "Capital Economics",
    "Ellen Zentner": "Morgan Stanley Wealth Management",
    "David Kostin": "Goldman Sachs",
    "Savita Subramanian": "BofA",
    "Dubravko Lakos-Bujas": "J.P. Morgan",
    "Bankim Binky Chadha": "Deutsche Bank",
    "Mike Wilson": "Morgan Stanley",
    "Edward Yardeni": "Yardeni Research",
    "Lori Calvasina": "RBC Capital Markets",
    "Donald John Trump": "47th U.S. President",
    "Federal Reserve Chair": "Federal Reserve",
    "Governor of the Bank of Japan": "Bank of Japan",
}

EVALUATION_VALUES = (1, 2, 3, 4, 5)
EVALUATION_COLORS = {
    1: "#ff453a",
    2: "#ff9f0a",
    3: "#ffd60a",
    4: "#32d74b",
    5: "#64d2ff",
}
EMPTY_CHART_COLOR = "#3a3a3c"


def _parse_evaluation(value):
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "" or normalized == "-":
        return None
    try:
        parsed = int(float(normalized))
    except ValueError:
        return None
    if parsed in EVALUATION_VALUES:
        return parsed
    return None


def _build_conic_gradient(counts, total):
    if total == 0:
        return f"conic-gradient({EMPTY_CHART_COLOR} 0 100%)"

    segments = []
    start = 0.0
    for value in EVALUATION_VALUES:
        count = counts.get(value, 0)
        if count == 0:
            continue
        end = start + (count / total * 100)
        segments.append(f"{EVALUATION_COLORS[value]} {start:.2f}% {end:.2f}%")
        start = end

    if not segments:
        return f"conic-gradient({EMPTY_CHART_COLOR} 0 100%)"

    return "conic-gradient(" + ", ".join(segments) + ")"


def _format_average(value):
    if value is None:
        return "-"
    rounded_value = round(value, 1)
    if rounded_value.is_integer():
        return str(int(rounded_value))
    return f"{rounded_value:.1f}"


def fetch_and_process_data():
    csv_path = os.path.join(settings.BASE_DIR, 'static', 'person', 'data', 'person_data.csv')

    if not os.path.exists(csv_path):
        print(f"CSV file not found at {csv_path}")
        return {}, [], []

    articles_by_name = {}
    summary_counts = {d: {v: 0 for v in EVALUATION_VALUES} for d in DOMAIN_ORDER}
    summary_score_totals = {d: 0 for d in DOMAIN_ORDER}
    summary_score_counts = {d: 0 for d in DOMAIN_ORDER}

    try:
        with open(csv_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                try:
                    name = row.get('name', '').strip()
                    if not name:
                        continue

                    raw_domain = row.get('domain', '').strip()
                    domain = DOMAIN_MAPPING.get(raw_domain)
                    
                    # Parse evaluation
                    evaluation_raw = row.get('evaluation', '')
                    evaluation_label = evaluation_raw.strip() if evaluation_raw.strip() else "-"
                    evaluation_num = _parse_evaluation(evaluation_raw)
                    evaluation_class = f"score-{evaluation_num}" if evaluation_num is not None else "score-na"
                    if domain in DOMAIN_ORDER and evaluation_num is not None:
                        summary_counts[domain][evaluation_num] += 1
                        summary_score_totals[domain] += evaluation_num
                        summary_score_counts[domain] += 1

                    # Parse date
                    date_str = row.get('date', '').strip()
                    try:
                        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                    except ValueError:
                        date_obj = datetime.min
                    
                    article = {
                        'title': row.get('title', '').strip(),
                        'link': row.get('link', '').strip(),
                        'date': date_str,
                        'date_obj': date_obj,
                        'period': row.get('Period', '').strip(),
                        'evaluation': evaluation_label,
                        'evaluation_num': evaluation_num,
                        'evaluation_class': evaluation_class,
                        'raw_domain': raw_domain
                    }

                    if name not in articles_by_name:
                        articles_by_name[name] = []
                    articles_by_name[name].append(article)

                except Exception as e:
                    print(f"Error parsing CSV row {row}: {e}")
                    continue
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        return {}, [], []

    # Process each person
    processed_people = []
    
    for name, articles in articles_by_name.items():
        # Sort articles by date desc
        articles.sort(key=lambda x: x['date_obj'], reverse=True)

        # Determine Domain (use the most recent article's domain)
        raw_domain = articles[0]['raw_domain']
        domain = DOMAIN_MAPPING.get(raw_domain)

        # If domain is not in our target list, skip or put in others?
        # Request implies specific order and labels. If not mapped, we might skip.
        if not domain or domain not in DOMAIN_ORDER:
            continue

        # Calculate Average Score
        scores = [a['evaluation_num'] for a in articles if a['evaluation_num'] is not None]
        if scores:
            avg_score = mean(scores)
            person_score_num = int(round(avg_score)) # Round to nearest integer for display/chart
            # Clamp to 1-5 just in case
            person_score_num = max(1, min(5, person_score_num))
            person_score_label = str(person_score_num)
            person_score_class = f"score-{person_score_num}"
        else:
            person_score_num = None
            person_score_label = "-"
            person_score_class = "score-na"

        # Affiliation
        affiliation = NAME_TO_AFFILIATION.get(name, "")

        person_data = {
            'name': name,
            'affiliation': affiliation,
            'articles': articles,
            'latest_evaluation': person_score_label, # This is now Average Score
            'latest_evaluation_class': person_score_class,
            'evaluation_num': person_score_num,
            'domain': domain
        }

        processed_people.append(person_data)

    # Build Domain Groups (Ordered)
    domain_groups = []
    for domain in DOMAIN_ORDER:
        people_in_domain = [p for p in processed_people if p['domain'] == domain]
        if people_in_domain:
            domain_groups.append({
                'domain': domain,
                'people': people_in_domain
            })

    # Build Domain Summaries (Ordered)
    domain_summaries = []
    for domain in DOMAIN_ORDER:
        counts = summary_counts[domain]
        total = sum(counts.values())
        average_value = None
        if summary_score_counts[domain]:
            average_value = summary_score_totals[domain] / summary_score_counts[domain]
        average_label = _format_average(average_value)
        gradient = _build_conic_gradient(counts, total)
        domain_summaries.append({
            'domain': domain,
            'average': average_label,
            'gradient': gradient,
        })

    return articles_by_name, domain_groups, domain_summaries


def index(request):
    _, domain_groups, domain_summaries = fetch_and_process_data()

    context = {
        'domain_groups': domain_groups,
        'domain_summaries': domain_summaries,
    }
    return render(request, 'person/index.html', context)
