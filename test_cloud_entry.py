"""Fail-closed entry and PostgreSQL route checks, with fictional sessions only."""
from pathlib import Path
import os,unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
import pg_store


class CloudConfigurationTests(unittest.TestCase):
    def test_supabase_pooler_uses_bundled_ca_and_verifies_hostname(self):
        with patch.dict(os.environ,{'JAMIE_DATABASE_URL':'postgresql://example:dummy@aws-0-eu-west-1.pooler.supabase.com/postgres?sslmode=verify-full&sslrootcert=system'}):
            params=pg_store.settings()
        self.assertEqual(params['sslmode'],'verify-full')
        self.assertEqual(Path(params['sslrootcert']).name,'supabase-ca.crt')
        self.assertTrue(Path(params['sslrootcert']).read_text().startswith('-----BEGIN CERTIFICATE-----'))

    def test_missing_cloud_configuration_never_falls_back_to_sqlite(self):
        with patch.dict(os.environ,{'JAMIE_DATABASE_URL':'','JAMIE_DB_SCHEMA':'jamie_tennis'}):
            app=AppTest.from_file(str(Path(__file__).with_name('cloud_app.py')),default_timeout=15)
            app.secrets['JAMIE_DATABASE_URL']=''
            app.run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(app.error),1)
        self.assertFalse(app.dataframe)
        self.assertFalse(app.text_input)

    def test_remote_database_requires_verified_tls(self):
        with patch.dict(os.environ,{'JAMIE_DATABASE_URL':'postgresql://example:dummy@example.com/postgres?sslmode=disable'}):
            with self.assertRaises(RuntimeError):pg_store.settings()

    def test_schema_name_cannot_select_public_or_inject_sql(self):
        for value in ('public','auth','storage','pg_catalog','a; DROP SCHEMA public'):
            with patch.dict(os.environ,{'JAMIE_DB_SCHEMA':value}):
                with self.assertRaises(RuntimeError):pg_store.schema_name()

    def test_parameter_and_literal_translation(self):
        text,identity=pg_store.prepare("SELECT '?' as q, ? as value, '100%' as percent")
        self.assertEqual(text,"SELECT '?' as q, %s as value, '100%%' as percent")
        self.assertIsNone(identity)
        text,identity=pg_store.prepare('INSERT OR IGNORE INTO student_coaches VALUES(?,?)')
        self.assertIn('ON CONFLICT DO NOTHING',text);self.assertIsNone(identity)


# Additional PG-specific entry cases live in test_pg_integration to share its fixture.
