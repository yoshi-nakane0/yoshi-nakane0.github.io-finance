import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from . import views


class EventsViewTests(TestCase):
    def test_index_renders_future_events_without_bootstrap_cdn(self):
        future_groups = [
            {
                "date": "2026-03-10",
                "items": [
                    {
                        "time": "21:30",
                        "currency": "USD",
                        "event": "CPI",
                        "display_event": "CPI",
                        "impact": "★★★",
                        "is_important": True,
                    }
                ],
            }
        ]
        past_groups = [
            {
                "date": "2026-03-08",
                "items": [
                    {
                        "time": "08:50",
                        "currency": "JPY",
                        "event": "GDP",
                        "display_event": "GDP",
                        "impact": "★",
                        "is_important": False,
                    }
                ],
            }
        ]

        with patch.object(views, "_load_grouped_events", return_value=(future_groups, past_groups)):
            response = self.client.get(reverse("events:index"))

        html = response.content.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("CPI", html)
        self.assertNotIn("GDP", html)
        self.assertIn('id="past-events-container"', html)
        self.assertIn("data-has-past-events=\"true\"", html)
        self.assertNotIn("cdn.jsdelivr.net/npm/bootstrap", html)

    def test_past_events_endpoint_renders_only_past_sections(self):
        future_groups = [
            {
                "date": "2026-03-10",
                "items": [
                    {
                        "time": "21:30",
                        "currency": "USD",
                        "event": "CPI",
                        "display_event": "CPI",
                        "impact": "★★★",
                        "is_important": True,
                    }
                ],
            }
        ]
        past_groups = [
            {
                "date": "2026-03-08",
                "items": [
                    {
                        "time": "08:50",
                        "currency": "JPY",
                        "event": "GDP",
                        "display_event": "GDP",
                        "impact": "★",
                        "is_important": False,
                    }
                ],
            }
        ]

        with patch.object(views, "_load_grouped_events", return_value=(future_groups, past_groups)):
            response = self.client.get(reverse("events:past_events"))

        html = response.content.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("GDP", html)
        self.assertNotIn("CPI", html)

    def test_load_grouped_events_splits_rows_by_today(self):
        csv_content = "\n".join(
            [
                "date,time,currency,event,impact",
                "2026-03-08,08:50,JPY,Past Event,★",
                "2026-03-09,21:30,USD,Future Event With Very Long Name ABCDEFGHIJKLMNOP,★★★",
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "data.csv"
            csv_path.write_text(csv_content, encoding="utf-8")

            with patch.object(views, "_events_csv_path", return_value=csv_path):
                future_groups, past_groups = views._load_grouped_events(today_iso="2026-03-09")

        self.assertEqual([group["date"] for group in future_groups], ["2026-03-09"])
        self.assertEqual([group["date"] for group in past_groups], ["2026-03-08"])
        self.assertEqual(
            future_groups[0]["items"][0]["display_event"],
            "Future Event With Very Long Na...",
        )
