#!/bin/bash
pip3.9 install -r requirements.txt
python3.9 manage.py collectstatic --noinput
cp -r myproject/static/* staticfiles/