import sqlite3
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from myproject.settings import bootstrap_sqlite_database


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
