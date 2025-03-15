#!/bin/bash

# Use Vercel's Python path
export PATH="/vercel/path0/python3/bin:$PATH"

# Create staticfiles directory if it doesn't exist
mkdir -p staticfiles/dashboard/css

# Copy CSS directly instead of using Tailwind (workaround)
mkdir -p staticfiles/dashboard/css
cp -r dashboard/static/dashboard/css/* staticfiles/dashboard/css/

# Run collectstatic
python3 manage.py collectstatic --noinput