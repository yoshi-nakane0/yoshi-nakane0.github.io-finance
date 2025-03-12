#!/bin/bash

npx tailwindcss -i ./dashboard/static/dashboard/css/style.css -o ./staticfiles/dashboard/css/style.css

python manage.py collectstatic --noinput