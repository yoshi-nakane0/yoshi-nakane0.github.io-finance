import csv
import math
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.http import Http404
from django.shortcuts import render

CSV_RELATIVE_PATH = Path("static") / "prediction" / "data" / "prediction_data.csv"
COLUMN_RENAME = {
    "Methods": "methods",
}
EXPECTED_COLUMNS = ("date", "evaluation", "methods", "article", "plan", "scenario")

def _parse_date(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_evaluation(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return ""
    if math.isnan(number):
        return ""
    return str(int(number))


def _string_value(value):
    if value is None:
        return ""
    return str(value)


def _load_prediction_records():
    csv_path = settings.BASE_DIR / CSV_RELATIVE_PATH
    if not csv_path.exists():
        print(f"CSV file not found at {csv_path}")
        return []

    try:
        with open(csv_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            if reader.fieldnames:
                reader.fieldnames = [
                    name.lstrip("\ufeff") if name else name for name in reader.fieldnames
                ]

            records = []
            for row in reader:
                normalized_row = {}
                for key, value in row.items():
                    normalized_key = COLUMN_RENAME.get(key, key)
                    normalized_row[normalized_key] = value

                record = {
                    "date": _parse_date(normalized_row.get("date")),
                    "evaluation": _parse_evaluation(normalized_row.get("evaluation")),
                    "methods": _string_value(normalized_row.get("methods")),
                    "article": _string_value(normalized_row.get("article")),
                    "plan": _string_value(normalized_row.get("plan")),
                    "scenario": _string_value(normalized_row.get("scenario")),
                }
                records.append(record)
    except Exception as exc:
        print(f"Error reading CSV file: {exc}")
        return []

    with_date = [record for record in records if record["date"] is not None]
    without_date = [record for record in records if record["date"] is None]
    with_date.sort(key=lambda record: record["date"], reverse=True)
    ordered = with_date + without_date
    for idx, record in enumerate(ordered):
        record["row_id"] = idx

    return ordered


def _get_prediction_records():
    records = _load_prediction_records()
    if not records:
        return []
    return records


def index(request):
    predictions = _get_prediction_records()
    return render(request, "prediction/list.html", {"predictions": predictions})


def detail(request, row_id):
    predictions = _get_prediction_records()
    prediction = next(
        (item for item in predictions if item.get("row_id") == row_id), None
    )
    if not prediction:
        raise Http404("Prediction not found")
    return render(request, "prediction/detail.html", {"prediction": prediction})
