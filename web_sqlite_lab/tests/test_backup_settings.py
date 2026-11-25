import errno
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from backup_settings import BackupSettings
from backup_policy import BackupPolicy
from store import Store
from transfer_package import TransferError,inspect_transfer

class BackupSettingsTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.data=self.root/"data";self.data.mkdir()
        self.cloud=self.root/"Google Drive"/"Мой диск";self.cloud.mkdir(parents=True)
        self.cfg=BackupSettings(self.data)
        self.store=Store(self.data/"lab.db")
        self.addCleanup(self.store.close)

    def test_unicode_and_restart(self):
        d=self.cfg.save(str(self.cloud))
        again=BackupSettings(self.data)
        self.assertEqual(again.read(),d)
        self.assertEqual(again.destination(d).parent,self.cloud.resolve())

    def test_reject_bad_paths_without_losing_setting(self):
        old=self.cfg.save(str(self.cloud))
        for p in ["relative",str(self.data),str(self.root/"missing"),123]:
            with self.assertRaises(TransferError):self.cfg.save(p)
            self.assertEqual(self.cfg.read(),old)

    def test_disconnected_does_not_recreate(self):
        d=self.cfg.save(str(self.cloud));dest=self.cfg.destination(d);dest.rmdir()
        with self.assertRaises(TransferError):self.cfg.destination(d)
        self.assertFalse(dest.exists())
        self.assertEqual(self.cfg.read(),d)
        dest.mkdir()
        self.assertEqual(self.cfg.destination(d),dest)

    def test_clear_keeps_files_and_overrides_environment(self):
        d=self.cfg.save(str(self.cloud))
        marker=self.cfg.destination(d)/"manual.zip";marker.write_bytes(b"keep")
        with patch.dict(os.environ,{"CONTRACTS_SQLITE_LAB_SECONDARY_BACKUP_DIR":str(self.cloud)}):
            self.assertIsNone(self.cfg.destination(self.cfg.save(None)))
        self.assertEqual(marker.read_bytes(),b"keep")

    def test_retry_and_virtual_disk_without_hardlinks(self):
        d=self.cfg.save(str(self.cloud));dest=self.cfg.destination(d)
        now=datetime(2026,11,25,10,tzinfo=timezone.utc)
        BackupPolicy(self.store.path).run_if_due(now)
        policy=BackupPolicy(self.store.path,secondary_dir=dest)
        with patch.object(policy,"_copy_secondary",side_effect=OSError("offline")):
            failed=policy.run_if_due(now+timedelta(minutes=1))
        self.assertEqual(failed["secondaryStatus"],"FAILED")
        original=os.link
        def no_cloud_link(src,dst,*args,**kwargs):
            if Path(dst).parent==dest:raise OSError(errno.EOPNOTSUPP,"virtual filesystem")
            return original(src,dst,*args,**kwargs)
        with patch("backup_policy.os.link",side_effect=no_cloud_link):
            result=policy.run_if_due(now+timedelta(minutes=2))
        self.assertEqual(result["secondaryStatus"],"VERIFIED")
        remote=dest/Path(result["archive"]).name
        self.assertTrue(inspect_transfer(remote)["valid"])
        self.assertEqual(remote.read_bytes(),Path(result["archive"]).read_bytes())

    def test_new_folder_not_verified_by_previous_path(self):
        d=self.cfg.save(str(self.cloud))
        BackupPolicy(self.store.path,secondary_dir=self.cfg.destination(d)).run_if_due(
            datetime(2026,11,25,10,tzinfo=timezone.utc))
        other=self.root/"other";other.mkdir()
        d=self.cfg.save(str(other))
        self.assertEqual(BackupPolicy(self.store.path,secondary_dir=self.cfg.destination(d)).status()["lastSecondaryStatus"],"PENDING")
