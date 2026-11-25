"""Critical safety regression for SQLite startup, migrations and backup publication."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import sqlite3
import unittest

from store import Store,DataError

class CriticalSafety(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.db=self.root/"lab.sqlite"
    def tearDown(self):
        self.temp.cleanup()
    def test_unversioned_nonempty_db_never_modified(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TABLE legacy_data(secret TEXT)")
            con.execute("INSERT INTO legacy_data VALUES ('PRESERVE')")
            con.commit()
        with self.assertRaises(DataError):Store(self.db)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT secret FROM legacy_data").fetchone()[0],"PRESERVE")
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='products'").fetchone())
    def test_unknown_version_does_not_migrate(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("PRAGMA user_version=77")
        with self.assertRaises(DataError):Store(self.db)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0],77)
    def test_corrupted_v3_schema_must_not_autorepair(self):
        store=Store(self.db)
        store.close()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("DROP TABLE finance_events")
            con.commit()
        with self.assertRaises(DataError):Store(self.db)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='finance_events'").fetchone())
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0],4)
    def test_v1_backup_before_any_mutating_schema_statement(self):
        store=Store(self.db)
        store.close()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("DROP TABLE finance_events")
            con.execute("DROP TABLE contract_trash")
            con.execute("DROP TABLE contract_lifecycle")
            con.execute("DROP TABLE managed_files")
            con.execute("DROP TABLE file_events")
            con.execute("PRAGMA user_version=1")
            con.execute("CREATE TABLE sentinel(value TEXT)")
            con.execute("INSERT INTO sentinel VALUES('preserved')")
            con.commit()
        store=Store(self.db)
        store.close()
        files=list((self.root/"backups").glob("before-v2-*.sqlite"))
        self.assertEqual(len(files),1)
        with closing(sqlite3.connect(files[0])) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0],1)
            self.assertIsNone(backup.execute("SELECT name FROM sqlite_master WHERE name='finance_events'").fetchone())
            self.assertEqual(backup.execute("SELECT value FROM sentinel").fetchone()[0],"preserved")
        with closing(sqlite3.connect(self.db)) as updated:
            self.assertEqual(updated.execute("PRAGMA user_version").fetchone()[0],4)
            self.assertIsNotNone(updated.execute("SELECT name FROM sqlite_master WHERE name='finance_events'").fetchone())
    def test_backup_does_not_overwrite_concurrently_created_target(self):
        store=Store(self.db)
        target=self.root/"backups"/"critical.zip.sqlite"
        orig_link=os.link
        def colliding_link(source,destination):
            Path(destination).write_bytes(b"DO NOT REPLACE")
            return orig_link(source,destination)
        try:
            with patch("store.os.link",side_effect=colliding_link):
                with self.assertRaises(FileExistsError):store.backup(target)
            self.assertEqual(target.read_bytes(),b"DO NOT REPLACE")
            self.assertFalse(list(target.parent.glob("critical.zip.sqlite.staging-*")))
        finally:
            store.close()

if __name__=="__main__":unittest.main()
