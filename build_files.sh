#!/bin/bash
# Exit on error
set -e

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate

# Upgrade pip in virtual environment
python -m pip install --upgrade pip

# Install requirements
pip install -r requirements.txt

# Collect static files
python manage.py collectstatic --noinput