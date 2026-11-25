"""Independent SQLite failure-mode tests using ONLY throwaway local databases."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import os
from sqlite_runtime import sqlite3
import subprocess
import sys
import unittest
import uuid

from store import Store

def rid():return uuid.uuid4().hex

class SQLiteRecoveryTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix="sqlite-acid-recovery-")
        self.root=Path(self.temp.name)
        self.path=self.root/"synthetic.sqlite"
        self.store=Store(self.path)
    def tearDown(self):
        try:self.store.close()
        except sqlite3.Error:pass
        self.temp.cleanup()
    def test_01_wal_and_integrity_and_foreign_keys(self):
        from sqlite_build_safety import patched_wal_reset
        expected="wal" if patched_wal_reset() else "delete"
        self.assertEqual(self.store.db.execute("PRAGMA journal_mode").fetchone()[0],expected)
        self.assertEqual(self.store.db.execute("PRAGMA foreign_keys").fetchone()[0],1)
        self.assertEqual(self.store.db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        self.assertEqual(self.store.db.execute("PRAGMA foreign_key_check").fetchall(),[])
    def test_02_transaction_rollback_all_rows_on_fk_failure(self):
        self.store.add_product("TEST 1010")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction():
                self.store.db.execute("INSERT INTO products(name) VALUES(?)",("MUST ROLLBACK",))
                self.store.db.execute("""INSERT INTO contract_lines(contract_id,position,product,quantity,unit_price)
                                       VALUES(?,?,?,?,?)""",
                                      ("nonexistent-contract",1,"TEST 1010",1,10000))
        self.assertIsNone(self.store.db.execute(
            "SELECT name FROM products WHERE name='MUST ROLLBACK'").fetchone())
        self.assertEqual(self.store.db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
    def test_03_busy_writer_is_rejected_without_partial_data(self):
        with closing(sqlite3.connect(self.path,isolation_level=None)) as holder:
            holder.execute("BEGIN IMMEDIATE")
            self.store.db.execute("PRAGMA busy_timeout=80")
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    self.store.add_product("WAITING FOR SQLITE BUSY")
                self.assertIsNone(self.store.db.execute(
                    "SELECT name FROM products WHERE name='WAITING FOR SQLITE BUSY'").fetchone())
            finally:
                holder.execute("ROLLBACK")
        self.store.add_product("AFTER LOCK RELEASED")
        self.assertIsNotNone(self.store.db.execute(
            "SELECT name FROM products WHERE name='AFTER LOCK RELEASED'").fetchone())
    def test_04_sqlite_online_backup_includes_committed_wal(self):
        self.store.add_product("TEST 3120")
        self.store.receipt(rid(),"TEST 3120",9,"1225.10")
        backup=self.store.backup(self.root/"backup.sqlite")
        self.assertEqual(len(backup["sha256"]),64)
        with closing(sqlite3.connect(backup["file"])) as snapshot:
            self.assertEqual(snapshot.execute("SELECT qty FROM receipts").fetchone()[0],9)
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(snapshot.execute("PRAGMA foreign_key_check").fetchall(),[])
        self.store.receipt(rid(),"TEST 3120",2,"1200.00")
        with closing(sqlite3.connect(backup["file"])) as snapshot:
            self.assertEqual(snapshot.execute("SELECT COUNT(*) FROM receipts").fetchone()[0],1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0],2)
    def test_05_wal_recovery_after_simulated_forced_process_exit(self):
        self.store.close()
        script="""
import os,sqlite3,sys
db=sys.argv[1]
conn=sqlite3.connect(db,isolation_level=None)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('BEGIN IMMEDIATE')
conn.execute('INSERT INTO products(name) VALUES(?)',('WAL RECOVERED',))
conn.execute('COMMIT')
os._exit(0)
"""
        child=subprocess.run([sys.executable,"-c",script,str(self.path)],
                             capture_output=True,timeout=10)
        self.assertEqual(child.returncode,0,child.stderr)
        self.store=Store(self.path)
        self.assertIsNotNone(self.store.db.execute(
            "SELECT name FROM products WHERE name='WAL RECOVERED'").fetchone())
        self.assertEqual(self.store.db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
    def test_06_corrupted_db_is_never_silently_replaced(self):
        self.store.close()
        bad=b"NOT_A_SQLITE_FILE"+os.urandom(512)
        self.path.write_bytes(bad)
        with self.assertRaises(sqlite3.DatabaseError):
            Store(self.path)
        self.assertEqual(self.path.read_bytes(),bad)
        self.store=Store.__new__(Store)
        self.store.close=lambda:None
    def test_07_unknown_schema_has_no_destructive_upgrade(self):
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA user_version=666")
            db.commit()
        from store import DataError
        with self.assertRaises(DataError):
            Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],666)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='products'").fetchone())
        self.store=Store.__new__(Store)
        self.store.close=lambda:None
    def test_08_sql_parameters_treat_injection_as_inert_text(self):
        name="MODEL'); DROP TABLE products; --"
        self.store.add_product(name)
        self.assertIsNotNone(self.store.db.execute("SELECT name FROM products WHERE name=?",(name,)).fetchone())
        self.assertEqual(self.store.db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
    def test_09_multi_row_snapshot_survives_reopen(self):
        with self.store.transaction():
            self.store.db.executemany("INSERT INTO products(name) VALUES(?)",
                                      [(f"synthetic-{i:04d}",) for i in range(500)])
        self.store.close()
        self.store=Store(self.path)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM products").fetchone()[0],500)
        archive=self.store.backup(self.root/"volume-test.sqlite")
        with closing(sqlite3.connect(archive["file"])) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM products").fetchone()[0],500)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
    def test_10_failed_fk_does_not_break_following_transactions(self):
        self.store.add_product("QA 1010")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction():
                self.store.db.execute(
                    "INSERT INTO shipments(contract_id,product,qty,cost) VALUES(?,?,?,?)",
                    ("missing", "QA 1010", 1, 100))
        self.store.receipt(rid(),"QA 1010",10,"1000.00")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0],1)
        self.assertEqual(self.store.verify()["foreignKeys"],0)

if __name__=="__main__":unittest.main()
