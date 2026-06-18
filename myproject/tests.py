import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

from django.db import OperationalError
from django.test import SimpleTestCase, TestCase

from myproject.auth import ensure_env_superuser
from myproject.settings import (
    BASE_DIR,
    bootstrap_sqlite_database,
    default_bundled_sqlite_database_path,
)


class SQLiteBootstrapTests(SimpleTestCase):
    def _create_db(self, path, names):
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE sample (name TEXT)')
            connection.executemany(
                'INSERT INTO sample (name) VALUES (?)',
                [(name,) for name in names],
            )

    def _sample_count(self, path):
        with sqlite3.connect(path) as connection:
            return connection.execute('SELECT COUNT(*) FROM sample').fetchone()[0]

    def test_bootstrap_replaces_existing_db_when_schema_matches_but_data_differs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            source = tmpdir / 'source.sqlite3'
            runtime = tmpdir / 'runtime.sqlite3'
            self._create_db(source, ['source-a', 'source-b'])
            self._create_db(runtime, [])

            bootstrap_sqlite_database(runtime, source)

            self.assertEqual(self._sample_count(runtime), 2)

    def test_private_runtime_bundle_is_default_source_when_present(self):
        private_bundle = BASE_DIR / 'runtime' / 'db.sqlite3'
        original_exists = Path.exists

        def fake_exists(path):
            if path == private_bundle:
                return True
            return original_exists(path)

        with mock.patch.dict('os.environ', {'BUNDLED_SQLITE_PATH': ''}):
            with mock.patch('pathlib.Path.exists', fake_exists):
                self.assertEqual(
                    default_bundled_sqlite_database_path(),
                    private_bundle,
                )


class RuntimeAdminProvisioningTests(TestCase):
    @mock.patch.dict(
        'os.environ',
        {
            'DJANGO_SUPERUSER_USERNAME': 'runtime-admin',
            'DJANGO_SUPERUSER_PASSWORD': 'runtime-password',
            'DJANGO_SUPERUSER_EMAIL': 'runtime@example.com',
        },
    )
    def test_env_superuser_is_created(self):
        user = ensure_env_superuser()

        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.check_password('runtime-password'))


class ExplanationRoutingTests(TestCase):
    def test_explanation_page_exists(self):
        response = self.client.get('/explanation/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '最終判断')
        self.assertContains(response, 'Macro')
        self.assertContains(response, 'Basecalc')
        self.assertContains(response, 'Audit')

    def test_explanation_latest_api_exists(self):
        response = self.client.get('/explanation/api/latest/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('final', payload)
        self.assertIn('macro', payload)
        self.assertIn('basecalc', payload)
        self.assertIn('audit', payload)

    def test_explanation_page_falls_back_when_snapshot_table_is_missing(self):
        with mock.patch(
            'explanation.views.ExplanationSnapshot.objects.order_by',
            side_effect=OperationalError('no such table: explanation_explanationsnapshot'),
        ):
            response = self.client.get('/explanation/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '保存済み判断がないため')
        self.assertContains(response, '最終判断')

    def test_explanation_api_falls_back_when_snapshot_table_is_missing(self):
        with mock.patch(
            'explanation.views.ExplanationSnapshot.objects.order_by',
            side_effect=OperationalError('no such table: explanation_explanationsnapshot'),
        ):
            response = self.client.get('/explanation/api/latest/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('final', response.json())
