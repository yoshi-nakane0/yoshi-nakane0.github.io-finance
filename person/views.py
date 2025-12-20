import csv
import os
from django.conf import settings
from django.shortcuts import render

DOMAIN_ORDER = ("Macro", "Key Remarks", "Equities")
PERIOD_OPTIONS = ("short", "mid", "long")
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


def fetch_people_from_csv():
    csv_path = os.path.join(settings.BASE_DIR, 'static', 'person', 'data', 'person_data.csv')

    if not os.path.exists(csv_path):
        print(f"CSV file not found at {csv_path}")
        return []

    people = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                try:
                    name = row.get('name', '').strip()
                    title = row.get('title', '').strip()
                    link = row.get('link', '').strip()
                    date = row.get('date', '').strip()
                    domain = row.get('domain', '').strip()
                    period = row.get('Period', '').strip()
                    evaluation_raw = row.get('evaluation', '')

                    if not all([name, title, link, domain]):
                        print(f"Skipping incomplete row: {row}")
                        continue

                    evaluation_label = evaluation_raw.strip()
                    if evaluation_label == "":
                        evaluation_label = "-"

                    evaluation_num = _parse_evaluation(evaluation_raw)
                    if evaluation_num is None:
                        evaluation_class = "score-na"
                    else:
                        evaluation_class = f"score-{evaluation_num}"

                    people.append({
                        'name': name,
                        'title': title,
                        'link': link,
                        'date': date,
                        'domain': domain,
                        'period': period,
                        'evaluation': evaluation_label,
                        'evaluation_num': evaluation_num,
                        'evaluation_class': evaluation_class,
                    })
                except Exception as e:
                    print(f"Error parsing CSV row {row}: {e}")
                    continue
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        return []

    return people


def build_domain_summaries(people):
    summaries = []
    for domain in DOMAIN_ORDER:
        counts = {value: 0 for value in EVALUATION_VALUES}
        for person in people:
            if person.get('domain') != domain:
                continue
            evaluation_num = person.get('evaluation_num')
            if evaluation_num in EVALUATION_VALUES:
                counts[evaluation_num] += 1

        total = sum(counts.values())
        gradient = _build_conic_gradient(counts, total)
        evaluations = [{'value': value, 'count': counts[value]} for value in EVALUATION_VALUES]

        summaries.append({
            'domain': domain,
            'total': total,
            'gradient': gradient,
            'evaluations': evaluations,
        })
    return summaries


def index(request):
    people = fetch_people_from_csv()
    domain_summaries = build_domain_summaries(people)
    domain_groups = [
        {'domain': domain, 'people': [p for p in people if p.get('domain') == domain]}
        for domain in DOMAIN_ORDER
    ]

    context = {
        'people': people,
        'domain_summaries': domain_summaries,
        'domain_groups': domain_groups,
    }
    return render(request, 'person/index.html', context)
