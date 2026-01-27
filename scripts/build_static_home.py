import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django
from django.conf import settings
from django.template.loader import render_to_string


def main():
    django.setup()
    html = render_to_string("dashboard/index.html")
    output_dir = Path(settings.BASE_DIR) / "staticfiles"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"Static home generated: {output_path}")


if __name__ == "__main__":
    main()
