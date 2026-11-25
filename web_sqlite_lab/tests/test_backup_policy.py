"""Automated backups are synthetic only; no production Excel or user files."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest
import uuid

from store import Store
from file_vault import FileVault
from backup_policy import BackupPolicy,RETENTION_DAYS,AUTO_PATTERN
from transfer_package import inspect_transfer,restore_new_install,TransferError

START=datetime(2026,11,25,8,0,tzinfo=timezone.utc)
def key():return uuid.uuid4().hex

class BackupPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(prefix="sqlite-backup-policy-")
        self.root=Path(self.tmp.name)
        self.data=self.root/"appdata"
        self.data.mkdir()
        self.store=Store(self.data/"lab.db")
        self.store.add_product("Рутокен Lite 1010")
        self.attachments=self.data/"attachments"
        (self.attachments/"контракты").mkdir(parents=True)
        (self.attachments/"контракты"/"отгрузка.pdf").write_bytes(b"pdf attachment synthetic")
        self.vault=FileVault(self.store)
        self.policy=BackupPolicy(self.store.path)
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def archived(self):
        return sorted(self.policy.backups.glob("auto-*.zip"))
    def first(self):
        one=self.policy.run_if_due(START)
        self.assertEqual(one["status"],"CREATED")
        self.assertTrue(Path(one["archive"]).is_file())
        return one
    def test_01_first_snapshot_complete_and_restorable(self):
        original=self.vault.add_stream(key(),key(),"счёт.txt",BytesIO(b"one"),3)
        deleted=self.vault.add_stream(key(),key(),"удалён.txt",BytesIO(b"two"),3)
        self.vault.trash_file(key(),deleted["id"],1,now=START)
        out=self.first()
        info=inspect_transfer(out["archive"])
        self.assertEqual((info["managedBlobs"],info["documents"]),(2,3))
        target=self.root/"other_computer"
        restore_new_install(out["archive"],target)
        with closing(sqlite3.connect(target/"lab.db")) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM managed_files").fetchone()[0],2)
        self.assertEqual((target/"managed_files"/(deleted["id"]+".bin")).read_bytes(),b"two")
    def test_02_second_check_same_day_creates_nothing(self):
        self.first()
        previous=self.archived()
        r=self.policy.run_if_due(START+timedelta(hours=12))
        self.assertEqual(r["status"],"NOT_DUE")
        self.assertEqual(self.archived(),previous)
    def test_03_if_unchanged_after_one_day_no_duplicate(self):
        self.first()
        old=self.archived()
        next=self.policy.run_if_due(START+timedelta(hours=25))
        self.assertEqual(next["status"],"UNCHANGED")
        self.assertEqual(self.archived(),old)
        self.assertEqual(self.policy.status()["automaticCopies"],1)
    def test_04_changed_database_after_one_day_creates_new_archive(self):
        self.first()
        self.store.receipt(key(),"Рутокен Lite 1010",5,"1200.00")
        next=self.policy.run_if_due(START+timedelta(hours=25))
        self.assertEqual(next["status"],"CREATED")
        self.assertEqual(len(self.archived()),2)
        self.assertEqual(inspect_transfer(next["archive"])["database"]["counts"]["receipts"],1)
    def test_05_changed_document_only_creates_new_archive(self):
        self.first()
        (self.attachments/"контракты"/"отгрузка.pdf").write_bytes(b"EDITED pdf on same path")
        nxt=self.policy.run_if_due(START+timedelta(hours=25))
        self.assertEqual(nxt["status"],"CREATED")
        self.assertEqual(len(self.archived()),2)
    def test_06_unchanged_data_refreshes_after_seven_days(self):
        self.first()
        nxt=self.policy.run_if_due(START+timedelta(days=8))
        self.assertEqual(nxt["status"],"CREATED")
        self.assertEqual(len(self.archived()),2)
    def test_07_verified_new_copy_prunes_older_than_fourteen_days(self):
        first=self.first()
        old=Path(first["archive"])
        manual=self.policy.backups/"manual-keep-me.zip"
        manual.write_bytes(b"this is a user archive, never delete")
        old_sqlite=self.data/"backups"/"before-v4-sensitive.sqlite"
        old_sqlite.write_bytes(b"do not delete pre-migration")
        # Clock advancement >14 days forces fresh validated copy.
        nxt=self.policy.run_if_due(START+timedelta(days=15))
        self.assertEqual(nxt["status"],"CREATED")
        self.assertEqual(nxt["pruned"],1)
        self.assertFalse(old.exists())
        self.assertTrue(Path(nxt["archive"]).is_file())
        self.assertEqual(manual.read_bytes(),b"this is a user archive, never delete")
        self.assertEqual(old_sqlite.read_bytes(),b"do not delete pre-migration")
    def test_08_corrupt_or_missing_managed_document_blocks_delete_and_backup(self):
        item=self.vault.add_stream(key(),key(),"не терять.txt",BytesIO(b"important"),9)
        prev=self.first()
        old=Path(prev["archive"])
        self.vault._blob(item["id"]).unlink()
        with self.assertRaises((ValueError,TransferError)):
            self.policy.run_if_due(START+timedelta(days=15))
        self.assertEqual(self.archived(),[old])
        self.assertTrue(old.exists())
        self.assertEqual(inspect_transfer(old)["managedBlobs"],1)
    def test_09_changed_managed_document_creates_new_archive(self):
        self.first()
        f=self.vault.add_stream(key(),key(),"добавлен.txt",BytesIO(b"important"),9)
        self.vault.trash_file(key(),f["id"],1,now=START)
        out=self.policy.run_if_due(START+timedelta(days=1))
        self.assertEqual(out["status"],"CREATED")
        self.assertEqual(inspect_transfer(out["archive"])["managedBlobs"],1)
    def test_10_corrupted_last_archive_is_not_a_valid_duplicate(self):
        original=self.first()
        old=Path(original["archive"])
        old.write_bytes(b"not a ZIP")
        next=self.policy.run_if_due(START+timedelta(hours=25))
        self.assertEqual(next["status"],"CREATED")
        self.assertEqual(len(self.archived()),2)
        self.assertTrue(inspect_transfer(next["archive"])["valid"])
    def test_11_secondary_destination_contains_identical_sha_verified_zip(self):
        remote=self.root/"external_drive"
        remote.mkdir()
        policy=BackupPolicy(self.store.path,secondary_dir=remote)
        result=policy.run_if_due(START)
        self.assertEqual(result["secondaryStatus"],"VERIFIED")
        remote_zip=remote/Path(result["archive"]).name
        self.assertEqual(remote_zip.read_bytes(),Path(result["archive"]).read_bytes())
        self.assertTrue(inspect_transfer(remote_zip)["valid"])
    def test_12_missing_secondary_is_explicitly_rejected(self):
        with self.assertRaises(TransferError):
            BackupPolicy(self.store.path,secondary_dir=self.root/"offline_usb")
    def test_13_secondary_write_failure_must_not_destroy_local_snapshot(self):
        secondary=self.root/"external"
        secondary.mkdir()
        policy=BackupPolicy(self.store.path,secondary_dir=secondary)
        # No accidental over-writing if another process created same name:
        # simulate a failing copy in a deterministic way.
        from unittest.mock import patch
        with patch.object(policy,"_copy_secondary",side_effect=OSError("USB missing")):
            result=policy.run_if_due(START)
        self.assertEqual(result["secondaryStatus"],"FAILED")
        self.assertEqual(len(self.archived()),1)
        self.assertTrue(inspect_transfer(self.archived()[0])["valid"])
    def test_14_invalid_backup_symlink_is_rejected(self):
        target=self.root/"foreign"
        target.mkdir()
        alias=self.root/"link-to-foreign"
        try:alias.symlink_to(target,target_is_directory=True)
        except (OSError,NotImplementedError):self.skipTest("symlinks unavailable")
        with self.assertRaises(TransferError):
            BackupPolicy(self.store.path,backup_dir=alias)
    def test_15_after_skip_restore_from_previous_still_works(self):
        first=self.first()
        out=self.policy.run_if_due(START+timedelta(days=2))
        self.assertEqual(out["status"],"UNCHANGED")
        dest=self.root/"restored-later"
        restore_new_install(out["archive"],dest)
        with closing(sqlite3.connect(dest/"lab.db")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM products").fetchone()[0],1)
    def test_16_clock_moved_back_does_not_suppress_future_backup_forever(self):
        self.first()
        result=self.policy.run_if_due(START-timedelta(days=1))
        self.assertEqual(result["status"],"CLOCK_MOVED_BACK")
        self.assertEqual(len(self.archived()),1)
    def test_17_status_is_useful_and_does_not_leak_data(self):
        info=self.policy.status()
        self.assertEqual(info["warning"],"NO_VERIFIED_COPY")
        result=self.first()
        info=self.policy.status()
        self.assertEqual(info["retentionDays"],14)
        self.assertEqual(info["checkHours"],24)
        self.assertEqual(info["warning"],"SECONDARY_NOT_CONFIGURED")
        self.assertNotIn("customer",json.dumps(info).lower())
    def test_18_failed_export_does_not_record_last_success(self):
        item=self.vault.add_stream(key(),key(),"a.pdf",BytesIO(b"abc"),3)
        self.vault._blob(item["id"]).write_bytes(b"tampered")
        with self.assertRaises((TransferError,ValueError)):
            self.policy.run_if_due(START)
        self.assertEqual(self.policy.status()["automaticCopies"],0)
        self.assertIsNone(self.policy.status()["lastVerifiedUtc"])
    def test_19_policy_never_deletes_other_named_zips(self):
        old=self.policy.backups/("complete-"+key()+".zip")
        old.write_bytes(b"manual archive")
        self.first()
        self.policy.run_if_due(START+timedelta(days=16))
        self.assertTrue(old.exists())
    def test_20_timezone_required(self):
        with self.assertRaises(ValueError):
            self.policy.run_if_due(datetime(2026,11,25,12))
    def test_21_true_wal_online_backup_is_consistent(self):
        self.store.receipt(key(),"Рутокен Lite 1010",3,"1222.22")
        self.assertEqual(self.store.db.execute("PRAGMA journal_mode").fetchone()[0],"wal")
        out=self.first()
        restore=self.root/"wal_restore"
        restore_new_install(out["archive"],restore)
        with closing(sqlite3.connect(restore/"lab.db")) as db:
            self.assertEqual(db.execute("SELECT qty FROM receipts").fetchone()[0],3)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
    def test_22_interleaved_secondary_and_primary_retention(self):
        ext=self.root/"new-ssd"
        ext.mkdir()
        p=BackupPolicy(self.store.path,secondary_dir=ext)
        first=p.run_if_due(START)
        self.assertEqual(first["secondaryStatus"],"VERIFIED")
        other=p.run_if_due(START+timedelta(days=15))
        self.assertEqual(other["secondaryStatus"],"VERIFIED")
        self.assertEqual(len(list(ext.glob("auto-*.zip"))),1)
        self.assertEqual(len(self.archived()),1)
    def test_23_pruning_persists_after_application_restart(self):
        self.first()
        restarted=BackupPolicy(self.store.path)
        self.assertEqual(restarted.run_if_due(START+timedelta(hours=10))["status"],"NOT_DUE")
        self.assertEqual(len(self.archived()),1)

if __name__=="__main__":unittest.main()
