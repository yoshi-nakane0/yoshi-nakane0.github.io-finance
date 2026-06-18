#!/usr/bin/env bash
set -euo pipefail

python manage.py run_house_view_backtest --output static/macro/house_view_backtest.json
python manage.py export_macro_house_view_validation --output static/macro/house_view_validation.json
