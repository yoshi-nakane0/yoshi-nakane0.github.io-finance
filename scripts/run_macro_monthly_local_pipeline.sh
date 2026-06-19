#!/usr/bin/env bash
set -euo pipefail

python manage.py migrate --noinput

python manage.py monthly_macro_maintenance

python manage.py export_macro_payload --output static/macro/latest_dashboard.json
python manage.py export_macro_house_view --output static/macro/house_view.json
python manage.py export_macro_quality --output static/macro/data_quality_report.json
python manage.py export_macro_forecast_ledger --output static/macro/forecast_ledger.json
python manage.py export_macro_scenarios --output static/macro/scenario_ledger.json
python manage.py export_macro_model_validation --output static/macro/model_validation_report.json
python manage.py export_macro_model_cards --output static/macro/model_cards.json
python manage.py export_macro_operations_status --output static/macro/operations_status.json
python manage.py export_macro_goldman_outlook --output static/macro/goldman_outlook_comparison.json
python manage.py export_macro_house_view_validation --output static/macro/house_view_validation.json
python manage.py export_macro_vintage_quality --output static/macro/vintage_quality_report.json
python manage.py export_macro_validation_weights --output static/macro/validation_weights.json

test -f static/macro/return_forecast_model.json
test -f static/macro/macro_forecast_model.json
test -f static/macro/crash_probability_model.json

python manage.py test macro

git add static/macro/*.json static/macro/*.csv
