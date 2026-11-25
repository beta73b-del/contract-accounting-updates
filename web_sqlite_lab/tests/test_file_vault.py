"""End-to-end file Trash on synthetic SQLite and temporary documents only."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import hashlib
import os
import sqlite3
import unittest
import uuid

from file_vault import FileVault,RETENTION_DAYS
from store import Store,DataError,Conflict
from transfer_package import export_lab,inspect_transfer,restore_new_install,TransferError

T0=datetime(2026,11,25,12,0,tzinfo=timezone.utc)
def rid():return uuid.uuid4().hex
class FileVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(prefix="sqlite-doc-trash-")
        self.root=Path(self.tmp.name)
        self.db=self.root/"lab.sqlite"
        self.store=Store(self.db)
        self.vault=FileVault(self.store)
        self.attachments=self.root/"attachments"
        self.attachments.mkdir()
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def add(self,name="Сертификат 3120.pdf",content=b"%PDF-1.4 synthetic",contract_id=None):
        doc_id=rid()
        item=self.vault.add_stream(rid(),doc_id,name,BytesIO(content),len(content),contract_id)
        return item
    def trash(self,item,now=T0):
        return self.vault.trash_file(rid(),item["id"],item["revision"],
                                     "Ошибочно удалён",now)
    def test_01_soft_delete_and_restore_exact_bytes(self):
        body=b"\x00\x01\xff\n%PDF-1.4 synthetic"
        item=self.add(content=body)
        self.assertEqual(self.vault.get_active(item["id"])[1].read_bytes(),body)
        trashed=self.trash(item)
        self.assertEqual((trashed["state"],trashed["revision"]),( "trashed",2))
        self.assertTrue(self.vault._blob(item["id"]).is_file())
        self.assertEqual(len(self.vault.list_files("active")),0)
        self.assertEqual(len(self.vault.list_files("trashed")),1)
        with self.assertRaises(DataError):self.vault.get_active(item["id"])
        restored=self.vault.restore_file(rid(),item["id"],2,T0+timedelta(days=29))
        self.assertEqual((restored["state"],restored["revision"]),("active",3))
        self.assertEqual(self.vault.get_active(item["id"])[1].read_bytes(),body)
        self.assertEqual(self.vault.list_files("trashed"),[])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM file_events").fetchone()[0],3)
    def test_02_exact_30_days_after_deletion_only(self):
        item=self.add()
        self.trash(item)
        early=self.vault.purge_expired(T0+timedelta(days=30)-timedelta(microseconds=1))
        self.assertEqual(early["purgedFiles"],0)
        self.assertTrue(self.vault._blob(item["id"]).exists())
        exact=self.vault.purge_expired(T0+timedelta(days=30))
        self.assertEqual(exact["purgedFiles"],1)
        self.assertFalse(self.vault._blob(item["id"]).exists())
        self.assertEqual(self.vault.list_files("trashed"),[])
        row=self.store.db.execute("SELECT state,deleted_at,purged_at FROM managed_files WHERE id=?",(item["id"],)).fetchone()
        self.assertEqual(row["state"],"purged")
        self.assertIsNotNone(row["deleted_at"])
        self.assertIsNotNone(row["purged_at"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM file_events").fetchone()[0],4)
        self.assertEqual(self.vault.purge_expired(T0+timedelta(days=35))["purgedFiles"],0)
    def test_03_expired_document_cannot_be_restored_even_before_sweep(self):
        item=self.add();self.trash(item)
        with self.assertRaises(DataError):
            self.vault.restore_file(rid(),item["id"],2,T0+timedelta(days=30))
        self.assertEqual(self.vault._row(item["id"])["state"],"trashed")
    def test_04_tampered_document_blocks_restore_and_physical_purge(self):
        item=self.add();self.trash(item)
        self.vault._blob(item["id"]).write_bytes(b"tampered!")
        with self.assertRaises(DataError):
            self.vault.restore_file(rid(),item["id"],2,T0+timedelta(days=5))
        with self.assertRaises(DataError):
            self.vault.purge_expired(T0+timedelta(days=40))
        self.assertTrue(self.vault._blob(item["id"]).exists())
        self.assertEqual(self.vault._row(item["id"])["state"],"trashed")
    def test_05_invalid_file_names_and_paths(self):
        for name in ("../escape.txt","C:\\Windows\\test.txt","CON.txt","bad/name.txt","bad\\nfile.txt",""):
            with self.subTest(name=name),self.assertRaises(DataError):
                self.vault.add_stream(rid(),rid(),name,BytesIO(b"test"),4)
        self.assertEqual(self.vault.list_files("active"),[])
    def test_06_reject_oversized_upload_before_any_file_creation(self):
        with self.assertRaises(DataError):
            self.vault.add_stream(rid(),rid(),"any.pdf",BytesIO(b"a"),256*1024*1024+1)
        self.assertEqual(list(self.vault.root.iterdir()),[])
    def test_07_mid_upload_failure_leaves_no_visible_document(self):
        class Broken:
            def read(self,n):return b""
        with self.assertRaises(DataError):
            self.vault.add_stream(rid(),rid(),"broken.pdf",Broken(),42)
        self.assertEqual(list(self.vault.root.iterdir()),[])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM managed_files").fetchone()[0],0)
    def test_08_upload_idempotence_and_conflicting_same_request_id(self):
        op=rid();fid=rid()
        first=self.vault.add_stream(op,fid,"тест.txt",BytesIO(b"ABC"),3)
        same=self.vault.add_stream(op,fid,"тест.txt",BytesIO(b"ABC"),3)
        self.assertEqual(first,same)
        with self.assertRaises(Conflict):
            self.vault.add_stream(op,fid,"тест.txt",BytesIO(b"XYZ"),3)
        self.assertEqual(len(self.vault.list_files("active")),1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM file_events").fetchone()[0],1)
    def test_09_revision_and_idempotent_delete_restore(self):
        f=self.add();op=rid()
        deleted=self.vault.trash_file(op,f["id"],1,"исправление",T0)
        self.assertEqual(self.vault.trash_file(op,f["id"],1,"исправление",T0),deleted)
        with self.assertRaises(Conflict):self.vault.restore_file(rid(),f["id"],1,T0+timedelta(days=3))
        with self.assertRaises(Conflict):self.vault.trash_file(rid(),f["id"],2,"исправление",T0)
        restored=self.vault.restore_file(rid(),f["id"],2,T0+timedelta(days=3))
        self.assertEqual(restored["revision"],3)
    def test_10_purge_resumes_after_crash_between_unlink_and_commit(self):
        f=self.add();self.trash(f)
        with self.store.transaction():
            self.store.db.execute("UPDATE managed_files SET state='purge_pending' WHERE id=?",(f["id"],))
        self.vault._blob(f["id"]).unlink()
        self.assertEqual(self.vault.purge_expired(T0+timedelta(days=31))["purgedFiles"],1)
        self.assertEqual(self.vault._row(f["id"])["state"],"purged")
    def test_11_cross_pc_archive_preserves_active_and_trashed_blobs(self):
        a=self.add("Договор поставки.docx",b"\x00docx synthetic content")
        b=self.add("акт 001.pdf",b"%PDF\nfile in trash")
        self.trash(b)
        (self.attachments/"старый.txt").write_bytes(b"legacy active attachment")
        archive=self.root/"complete.zip"
        info=export_lab(self.db,self.attachments,archive)
        self.assertEqual(info["managedBlobs"],2)
        self.assertEqual(info["documents"],3)
        target=self.root/"new-mac-appdata"
        transfer=restore_new_install(archive,target)
        self.assertTrue(transfer["appReady"])
        self.assertFalse((target/"database.sqlite").exists())
        restored=Store(target/"lab.db")
        try:
            vault=FileVault(restored)
            self.assertEqual(len(vault.list_files("active")),1)
            self.assertEqual(len(vault.list_files("trashed")),1)
            self.assertEqual(vault.get_active(a["id"])[1].read_bytes(),b"\x00docx synthetic content")
            self.assertEqual((target/"attachments"/"старый.txt").read_bytes(),
                             b"legacy active attachment")
            vault.restore_file(rid(),b["id"],2,T0+timedelta(days=5))
            self.assertEqual(vault.get_active(b["id"])[1].read_bytes(),b"%PDF\nfile in trash")
        finally:restored.close()
    def test_12_missing_trashed_blob_blocks_export(self):
        f=self.add();self.trash(f)
        self.vault._blob(f["id"]).unlink()
        archive=self.root/"must-not-create.zip"
        with self.assertRaises(TransferError):
            export_lab(self.db,self.attachments,archive)
        self.assertFalse(archive.exists())
    def test_13_extra_blob_is_not_silently_dropped_from_archive(self):
        f=self.add()
        (self.vault.root/"untracked-file.bin").write_bytes(b"precious document")
        archive=self.root/"no-silent-loss.zip"
        with self.assertRaises(TransferError):
            export_lab(self.db,self.attachments,archive)
        self.assertFalse(archive.exists())
    def test_14_existing_destination_is_never_replaced(self):
        f=self.add()
        archive=self.root/"transfer.zip"
        export_lab(self.db,self.attachments,archive)
        target=self.root/"dest";target.mkdir()
        (target/"keep").write_bytes(b"ORIGINAL USER DATA")
        with self.assertRaises(FileExistsError):
            restore_new_install(archive,target)
        self.assertEqual((target/"keep").read_bytes(),b"ORIGINAL USER DATA")
    def test_15_linked_contract_must_be_restored_before_file(self):
        self.store.add_product("Тест Рутокен 1010")
        c=self.store.save_contract(rid(),{
          "id":rid(),"revision":0,"status":"формирование","customer":"QA",
          "platform":"Test","contractNumber":"9001","contractDate":"",
          "signBy":"","fulfillBy":"","handoverDate":"","paymentDue":"",
          "items":[{"product":"Тест Рутокен 1010","quantity":"1","unitPrice":"100.00"}]})
        f=self.add(contract_id=c["id"]);self.trash(f)
        self.store.trash_contract(rid(),c["id"],c["revision"])
        with self.assertRaises(DataError):
            self.vault.restore_file(rid(),f["id"],2,T0+timedelta(days=1))
        self.store.restore_contract(rid(),c["id"],2)
        restored=self.vault.restore_file(rid(),f["id"],2,T0+timedelta(days=1))
        self.assertEqual(restored["state"],"active")
    def test_16_legacy_attachment_deleted_only_after_verified_trash_copy(self):
        sub=self.attachments/"сертификаты"
        sub.mkdir()
        doc=sub/"Номер 1010.pdf";body=b"IMPORTANT PDF CONTENT"
        doc.write_bytes(body)
        r=self.vault.trash_legacy_attachment(rid(),"сертификаты/Номер 1010.pdf",self.attachments)
        self.assertFalse(doc.exists())
        self.assertEqual(r["state"],"trashed")
        self.assertEqual(self.vault._blob(r["id"]).read_bytes(),body)
        restored=self.vault.restore_file(rid(),r["id"],2,T0+timedelta(days=1))
        self.assertEqual(self.vault.get_active(restored["id"])[1].read_bytes(),body)
    def test_16b_legacy_repeat_delete_does_not_duplicate_backup(self):
        folder=self.attachments/"nested docs"
        folder.mkdir()
        rel="nested docs/подпись.jpg"
        (folder/"подпись.jpg").write_bytes(b"synthetic digital image")
        op=rid()
        first=self.vault.trash_legacy_attachment(op,rel,self.attachments)
        self.assertEqual(first["name"],rel)
        second=self.vault.trash_legacy_attachment(op,rel,self.attachments)
        self.assertEqual(second,first)
        self.assertEqual(self.store.db.execute(
             "SELECT COUNT(*) FROM managed_files").fetchone()[0],1)
        self.assertEqual(self.store.db.execute(
             "SELECT COUNT(*) FROM file_events").fetchone()[0],2)
    def test_17_symlink_not_followed_when_deleting_legacy_file(self):
        other=self.root/"outside-secret";other.write_bytes(b"SECRET")
        link=self.attachments/"outside"
        try:link.symlink_to(other)
        except (OSError,NotImplementedError):
            self.skipTest("This OS does not permit symlinks")
        with self.assertRaises(DataError):
            self.vault.trash_legacy_attachment(rid(),"outside",self.attachments)
        self.assertEqual(other.read_bytes(),b"SECRET")
        self.assertTrue(link.is_symlink())
    def test_18_expiry_retains_accounting_database_and_file_audit(self):
        f=self.add("ключ.pem",b"synthetic private-key NOT a real key")
        self.trash(f)
        self.vault.purge_expired(T0+timedelta(days=31))
        self.assertEqual(self.store.verify()["foreignKeys"],0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM managed_files").fetchone()[0],1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM file_events").fetchone()[0],4)
        self.assertFalse(self.vault._blob(f["id"]).exists())
    def test_18b_migrate_existing_v3_with_trashed_contract_before_v4_backup(self):
        self.store.add_product("Тест Рутокен")
        cid=rid()
        original=self.store.save_contract(rid(),{
           "id":cid,"revision":0,"status":"формирование",
           "customer":"QA","platform":"Тест ЭТП","contractNumber":"old-v3",
           "contractDate":"","signBy":"","fulfillBy":"",
           "handoverDate":"","paymentDue":"",
           "items":[{"product":"Тест Рутокен","quantity":"1","unitPrice":"100.00"}]
        })
        self.store.trash_contract(rid(),cid,original["revision"])
        self.store.close()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("DROP TABLE managed_files")
            db.execute("DROP TABLE file_events")
            db.execute("PRAGMA user_version=3")
            db.commit()
        self.store=Store(self.db)
        self.vault=FileVault(self.store)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0],4)
        self.assertEqual(len(self.store.list_trashed()),1)
        self.assertEqual(self.store.list_trashed()[0]["contract_number"],"old-v3")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM contract_lifecycle").fetchone()[0],1)
        backups=list((self.root/"backups").glob("before-v4-*.sqlite"))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],3)
            self.assertIsNone(db.execute(
                "SELECT name FROM sqlite_master WHERE name='managed_files'").fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contract_trash").fetchone()[0],1)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")

    def test_19_invalid_timezone_rejected(self):
        f=self.add()
        with self.assertRaises(DataError):self.vault.trash_file(rid(),f["id"],1,now="2026-11-25T12:00:00")
    def test_20_portable_after_expiry_excludes_purged_bytes(self):
        f=self.add("Больше не нужен.pdf",b"PURGE THIS")
        self.trash(f)
        self.vault.purge_expired(T0+timedelta(days=31))
        archive=self.root/"post-30days.zip"
        data=export_lab(self.db,self.attachments,archive)
        self.assertEqual(data["managedBlobs"],0)
        dest=self.root/"new-install"
        restore_new_install(archive,dest)
        target=Store(dest/"lab.db")
        try:
            v=FileVault(target)
            self.assertEqual(v.list_files("active"),[])
            self.assertEqual(v.list_files("trashed"),[])
            self.assertEqual(target.db.execute("SELECT state FROM managed_files WHERE id=?",
                            (f["id"],)).fetchone()[0],"purged")
        finally:target.close()
if __name__=="__main__":unittest.main()
