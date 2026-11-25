"""R5 portable transfer: positive/negative synthetic tests only."""
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

from store import Store
from transfer_package import export_lab, inspect_transfer,restore_to_empty,TransferError

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.store=Store(self.root/"lab.sqlite")
        self.store.add_product("Тест 1010")
        self.store.receipt("receipt-test-one","Тест 1010",10,"1250.01")
        self.attachments=self.root/"docs";(self.attachments/"договор").mkdir(parents=True)
        (self.attachments/"договор"/"акт 1.pdf").write_bytes(b"%PDF-1.4 synthetic\n")
        self.zip=self.root/"transfer.zip"
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def create(self):
        return export_lab(self.store.path,self.attachments,self.zip,["договор/акт 1.pdf"])
    def test_01_export_restore_and_integrity(self):
        info=self.create()
        self.assertEqual((info["valid"],info["documents"]), (True,1))
        target=self.root/"new-mac-data"
        result=restore_to_empty(self.zip,target)
        self.assertTrue(result["restoredToEmpty"])
        self.assertEqual((target/"attachments"/"договор"/"акт 1.pdf").read_bytes(),b"%PDF-1.4 synthetic\n")
        with closing(sqlite3.connect(target/"database.sqlite")) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0],1)
        self.assertEqual(inspect_transfer(self.zip)["database"]["counts"]["products"],1)
    def test_02_missing_required_document(self):
        with self.assertRaises(TransferError):
            export_lab(self.store.path,self.attachments,self.zip,["договор/нет.pdf"])
        self.assertFalse(self.zip.exists())
    def test_03_existing_archive_never_overwritten(self):
        self.create()
        before=self.zip.read_bytes()
        with self.assertRaises(FileExistsError):self.create()
        self.assertEqual(before,self.zip.read_bytes())
    def test_04_existing_restore_never_overwritten(self):
        self.create()
        target=self.root/"already-in-use";target.mkdir()
        marker=target/"keep";marker.write_text("don't delete")
        with self.assertRaises(FileExistsError):restore_to_empty(self.zip,target)
        self.assertEqual(marker.read_text(),"don't delete")
    def test_05_hash_mismatch_detected(self):
        self.create()
        bad=self.root/"bad.zip"
        with zipfile.ZipFile(self.zip) as zin,zipfile.ZipFile(bad,"w") as zout:
            for info in zin.infolist():
                b=zin.read(info.filename)
                if info.filename.startswith("attachments/"):b=b"tampered data"
                zout.writestr(info.filename,b)
        with self.assertRaises(TransferError):inspect_transfer(bad)
    def test_06_zip_slip_rejected(self):
        self.create()
        bad=self.root/"slip.zip"
        with zipfile.ZipFile(self.zip) as zin,zipfile.ZipFile(bad,"w") as zout:
            for info in zin.infolist():zout.writestr(info.filename,zin.read(info.filename))
            zout.writestr("../outside.txt","attack")
        with self.assertRaises(TransferError):inspect_transfer(bad)
    def test_07_case_collision_rejected(self):
        self.create()
        bad=self.root/"collision.zip"
        with zipfile.ZipFile(self.zip) as zin,zipfile.ZipFile(bad,"w") as zout:
            for info in zin.infolist():zout.writestr(info.filename,zin.read(info.filename))
            zout.writestr("ATTACHMENTS/ДОГОВОР/АКТ 1.PDF","case collision")
        with self.assertRaises(TransferError):inspect_transfer(bad)
    def test_08_unsupported_database_version(self):
        self.store.close()
        with closing(sqlite3.connect(self.root/"lab.sqlite")) as db:
            db.execute("PRAGMA user_version=999");db.commit()
        with self.assertRaises(TransferError):self.create()
        self.store=Store.__new__(Store)
        self.store.close=lambda:None
    def test_09_repeated_restore_does_not_duplicate(self):
        self.create()
        target=self.root/"restore"
        restore_to_empty(self.zip,target)
        with self.assertRaises(FileExistsError):restore_to_empty(self.zip,target)
        with closing(sqlite3.connect(target/"database.sqlite")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0],1)
    def test_10_symlink_rejected_if_supported(self):
        other=self.root/"outside.txt";other.write_text("private")
        try:(self.attachments/"external-link.txt").symlink_to(other)
        except (OSError,NotImplementedError):
            self.skipTest("symlink creation not allowed on this Windows instance")
        with self.assertRaises(TransferError):self.create()
if __name__=="__main__":unittest.main()
