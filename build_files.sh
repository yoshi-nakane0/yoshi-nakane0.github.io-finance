#!/bin/bash

# Install required packages
pip install -r requirements.txt

# Create staticfiles directory if it doesn't exist
mkdir -p staticfiles/dashboard/css

# Build Tailwind CSS
npx tailwindcss -i ./dashboard/static/dashboard/css/style.css -o ./staticfiles/dashboard/css/style.css

# Run collectstatic
python manage.py collectstatic --noinput