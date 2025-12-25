from pathlib import Path

import pandas as pd
from django.conf import settings
from django.http import Http404
from django.shortcuts import render

CSV_RELATIVE_PATH = Path("static") / "prediction" / "data" / "prediction_data.csv"
COLUMN_RENAME = {
    "Methods": "methods",
}
EXPECTED_COLUMNS = ("date", "evaluation", "methods", "article", "plan", "scenario")


def _load_prediction_dataframe():
    csv_path = settings.BASE_DIR / CSV_RELATIVE_PATH
    if not csv_path.exists():
        print(f"CSV file not found at {csv_path}")
        return pd.DataFrame(columns=EXPECTED_COLUMNS)

    try:
        df = pd.read_csv(csv_path, encoding="utf-8", engine="python")
    except Exception as exc:
        print(f"Error reading CSV file: {exc}")
        return pd.DataFrame(columns=EXPECTED_COLUMNS)

    df = df.rename(columns=COLUMN_RENAME)
    for column in EXPECTED_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["evaluation"] = pd.to_numeric(df["evaluation"], errors="coerce")
    df = df.sort_values("date", ascending=False, na_position="last").reset_index(drop=True)
    df["row_id"] = df.index

    df["date"] = df["date"].dt.date
    df["date"] = df["date"].where(df["date"].notna(), None)
    df["evaluation"] = df["evaluation"].apply(
        lambda value: "" if pd.isna(value) else str(int(value))
    )
    for column in ("methods", "article", "plan", "scenario"):
        df[column] = df[column].fillna("").astype(str)

    return df


def _get_prediction_records():
    df = _load_prediction_dataframe()
    if df.empty:
        return []
    return df.to_dict(orient="records")


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
