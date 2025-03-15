#!/bin/bash

# Install dependencies
pip install -r requirements.txt

# Run collectstatic with DJANGO_SETTINGS_MODULE
export DJANGO_SETTINGS_MODULE=myproject.settings  # settings.pyの場所を適切に指定
python manage.py collectstatic --noinput