"""SQLite build/runtime release gate: classify official WAL-reset patch levels."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import subprocess
import sys
import unittest

from sqlite_build_safety import patched_wal_reset,report

class SQLiteBuildSafetyTests(unittest.TestCase):
    def test_documented_patched_versions(self):
        for version in [(3,44,6),(3,50,7),(3,51,3),(3,53,0),(3,53,3),(3,54,0)]:
            with self.subTest(version=version):
                self.assertTrue(patched_wal_reset(version))
    def test_unpatched_or_withdrawn_versions_are_rejected(self):
        for version in [(3,43,0),(3,44,5),(3,45,2),(3,50,6),(3,51,0),(3,51,2),(3,52,0)]:
            with self.subTest(version=version):
                self.assertFalse(patched_wal_reset(version))
    def test_report_matches_actual_runtime(self):
        r=report()
        self.assertEqual(r["sqliteVersion"],sqlite3.sqlite_version)
        self.assertEqual(r["releaseReadyForWAL"],patched_wal_reset())
    def test_production_gate_does_not_silently_pass_unsafe_build(self):
        mod=Path(__file__).resolve().parent.parent/"sqlite_build_safety.py"
        process=subprocess.run([sys.executable,str(mod),"--require-safe"],
                               capture_output=True,text=True,timeout=10)
        expected=0 if patched_wal_reset() else 2
        self.assertEqual(process.returncode,expected,process.stderr)
        self.assertIn("sqliteVersion",process.stdout)
    def test_integrity_pragmas_are_real_not_just_schema_version(self):
        with TemporaryDirectory(prefix="sqlite-health-") as temp:
            db=Path(temp)/"synthetic.db"
            with closing(sqlite3.connect(db)) as con:
                con.execute("PRAGMA foreign_keys=ON")
                con.execute("CREATE TABLE main_entity(id INTEGER PRIMARY KEY)")
                con.execute("CREATE TABLE linked(id INTEGER PRIMARY KEY, parent INTEGER NOT NULL REFERENCES main_entity(id))")
                con.execute("INSERT INTO main_entity VALUES(1)")
                con.execute("INSERT INTO linked VALUES(10,1)")
                con.commit()
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0],"ok")
                self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(),[])

if __name__=="__main__":unittest.main()
