"""Disaster recovery test on a simulated mounted disk, not actual Google Drive."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from datetime import datetime,timezone
from io import BytesIO
import json
import os
import uuid
import zipfile

from backup_policy import BackupPolicy
from backup_settings import BackupSettings
from recovery_center import (scan_archives,review_archive,restore_first_install,
    restore_separate,activate_recovered,active_data_root,installation_lock,restore_archive)
from transfer_package import TransferError,export_lab
from store import Store
from file_vault import FileVault
from finance_ledger import FinanceLedger

def uid():return uuid.uuid4().hex

class RecoveryTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/"Old computer";self.source.mkdir()
        (self.source/"attachments").mkdir()
        (self.source/"attachments"/"Контракт.txt").write_bytes(b"legacy file")
        self.store=Store(self.source/"lab.db")
        self.addCleanup(self.store.close)
        self.store.add_product("Тест 1010")
        self.store.receipt(uid(),"Тест 1010",20,"1250.37")
        record={"id":uid(),"revision":0,"status":"формирование","customer":"Synthetic",
                "contractNumber":"42/2026","platform":"Test","contractDate":"","signBy":"",
                "fulfillBy":"","handoverDate":"","paymentDue":"",
                "items":[{"product":"Тест 1010","quantity":"7","unitPrice":"1450.10"}]}
        self.store.save_contract(uid(),record)
        vault=FileVault(self.store)
        blob=vault.add_stream(uid(),uid(),"Документ.txt",BytesIO(b"active"),6,record["id"])
        trash=vault.add_stream(uid(),uid(),"Корзина.txt",BytesIO(b"trash"),5)
        vault.trash_file(uid(),trash["id"],1,"test")
        FinanceLedger(self.store).mutate({"action":"create","opId":uid(),"id":uid(),
            "date":"2026-11-25","amount":"100.40","comment":"Synthetic","revision":0})
        self.cloud=self.root/"Google Drive";self.cloud.mkdir()
        cfg=BackupSettings(self.source)
        self.dest=cfg.destination(cfg.save(str(self.cloud)))
        policy=BackupPolicy(self.store.path,secondary_dir=self.dest)
        result=policy.run_if_due(datetime(2026,11,25,10,tzinfo=timezone.utc))
        self.assertEqual(result["secondaryStatus"],"VERIFIED")
        self.archive=self.dest/Path(result["archive"]).name

    def assert_restored(self,target):
        self.assertEqual((target/"attachments"/"Контракт.txt").read_bytes(),b"legacy file")
        db=Store(target/"lab.db")
        try:
            self.assertEqual(db.verify()["integrity"],"ok")
            self.assertEqual(db.verify()["foreignKeys"],0)
            self.assertEqual(len(db.list_contracts()),1)
            self.assertEqual(db.list_contracts()[0]["contract_number"],"42/2026")
            self.assertEqual(db.stock_summary()[0]["physical"],20)
            self.assertEqual(FinanceLedger(db).payload()["expenses"][0]["amount"],"100.40")
            v=FileVault(db)
            self.assertEqual(len(v.list_files("active")),1)
            self.assertEqual(len(v.list_files("trashed")),1)
            a=v.list_files("active")[0]
            self.assertEqual(v.get_active(a["id"])[1].read_bytes(),b"active")
            t=v.list_files("trashed")[0]
            restored=v.restore_file(uid(),t["id"],t["revision"])
            self.assertEqual(v.get_active(restored["id"])[1].read_bytes(),b"trash")
        finally:db.close()

    def test_disaster_new_computer_only_cloud_archive(self):
        # Simulate total loss of original PC: remove local source before restore.
        self.store.close()
        import shutil
        shutil.rmtree(self.source)
        result=scan_archives(self.cloud)
        self.assertEqual(result["recommended"]["path"],str(self.archive))
        target=self.root/"New computer"
        self.assertTrue(restore_first_install(self.archive,target)["appReady"])
        self.assert_restored(target)

    def test_corrupt_newest_does_not_block_good_copy(self):
        bad=self.dest/("auto-20991231T235959Z-"+uid()+".zip")
        bad.write_bytes(b"truncated")
        result=scan_archives(self.cloud)
        self.assertEqual(len(result["valid"]),1)
        self.assertEqual(len(result["invalid"]),1)
        self.assertEqual(result["recommended"]["path"],str(self.archive))

    def test_multiple_computers_and_manifest_order_not_mtime(self):
        other=self.cloud/("ContractsIP-backup-"+uid());other.mkdir()
        second=other/("complete-"+uid()+".zip")
        # Re-export to a second computer folder, alter only the manifest date
        # in a synthetic fixture; review must sort by the manifest, not mtime.
        with zipfile.ZipFile(self.archive) as inp,zipfile.ZipFile(second,"w") as out:
            for member in inp.infolist():
                data=inp.read(member.filename)
                if member.filename=="manifest.json":
                    man=json.loads(data);man["createdUtc"]="2099-01-01T00:00:00+00:00";data=json.dumps(man).encode()
                out.writestr(member.filename,data)
        os.utime(second,(1,1))
        result=scan_archives(self.cloud)
        self.assertEqual(len(result["valid"]),2)
        self.assertEqual(result["recommended"]["path"],str(second))

    def test_existing_database_not_overwritten_and_activation(self):
        base=self.root/"Installed";base.mkdir()
        old=Store(base/"lab.db");old.add_product("Preserve me");old.close()
        before=(base/"lab.db").read_bytes()
        with installation_lock(base):
            target,result=restore_separate(self.archive,base)
            self.assertEqual(active_data_root(base),base)
            self.assertEqual((base/"lab.db").read_bytes(),before)
            activate_recovered(base,target)
        self.assertEqual(active_data_root(base),target)
        self.assertEqual((base/"lab.db").read_bytes(),before)
        self.assert_restored(target)

    def test_failed_restore_leaves_existing_data(self):
        bad=self.root/"bad.zip";bad.write_bytes(b"not a zip")
        target=self.root/"Installed";target.mkdir()
        (target/"keep").write_bytes(b"original")
        with self.assertRaises(Exception):restore_first_install(bad,target)
        with self.assertRaises(Exception):restore_separate(bad,target)
        self.assertEqual((target/"keep").read_bytes(),b"original")
        self.assertEqual(active_data_root(target),target)
        self.assertEqual(list(self.root.glob("Installed-Recovered-*")),[])

    def test_wrong_active_pointer_never_silently_creates_empty_db(self):
        base=self.root/"App";base.mkdir()
        control=base.with_name(base.name+".control");control.mkdir()
        (control/"active-install.json").write_text('{"active":"../outside"}',encoding="utf-8")
        with self.assertRaises(TransferError):active_data_root(base)
        self.assertFalse((base/"lab.db").exists())

    def test_disconnected_and_missing_archive(self):
        with self.assertRaises(TransferError):scan_archives(self.root/"offline")
        with self.assertRaises(TransferError):review_archive(self.root/"missing.zip")
        self.assertFalse((self.root/"New computer").exists())

    def test_recheck_after_selection_refuses_modified_zip(self):
        self.assertTrue(review_archive(self.archive)["valid"])
        self.archive.write_bytes(b"broken after selection")
        target=self.root/"new"
        with self.assertRaises(Exception):restore_archive(self.archive,target)
        self.assertFalse(target.exists())

    def test_reject_unexpected_manifest_path(self):
        bad=self.root/"bad.zip"
        with zipfile.ZipFile(self.archive) as inp,zipfile.ZipFile(bad,"w") as out:
            for member in inp.infolist():
                data=inp.read(member.filename)
                if member.filename=="manifest.json":
                    from hashlib import sha256
                    man=json.loads(data)
                    man["files"].append({"path":"backup-settings.json","size":1,"sha256":sha256(b"x").hexdigest()})
                    data=json.dumps(man).encode()
                out.writestr(member.filename,data)
            out.writestr("backup-settings.json",b"x")
        with self.assertRaises(TransferError):review_archive(bad)
