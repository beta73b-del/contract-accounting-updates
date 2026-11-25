"""GUI startup must never create an empty DB over orphaned documents."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from launcher import startup_data_state

class StartupDataGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(prefix="sqlite-missing-db-")
        self.base=Path(self.tmp.name)
        self.root=self.base/"data"
    def tearDown(self):
        self.tmp.cleanup()
    def test_01_missing_folder_is_fresh(self):
        self.assertEqual(startup_data_state(self.root),"FRESH")
    def test_02_empty_folder_is_fresh(self):
        self.root.mkdir()
        self.assertEqual(startup_data_state(self.root),"FRESH")
    def test_03_existing_db_is_not_overwritten(self):
        self.root.mkdir()
        db=self.root/"lab.db"
        db.write_bytes(b"still my database")
        self.assertEqual(startup_data_state(self.root),"EXISTING")
        self.assertEqual(db.read_bytes(),b"still my database")
    def test_04_only_documents_remain_must_not_create_new_db(self):
        folder=self.root/"attachments"
        folder.mkdir(parents=True)
        doc=folder/"контракт.pdf"
        doc.write_bytes(b"important PDF")
        self.assertEqual(startup_data_state(self.root),"ORPHANED")
        self.assertEqual(doc.read_bytes(),b"important PDF")
        self.assertFalse((self.root/"lab.db").exists())
    def test_05_only_backup_remains_must_not_create_new_db(self):
        folder=self.root/"backups"/"automatic"
        folder.mkdir(parents=True)
        (folder/"old.zip").write_bytes(b"kept")
        self.assertEqual(startup_data_state(self.root),"ORPHANED")
    def test_06_wrong_type_is_unsafe(self):
        self.root.write_bytes(b"not a directory")
        self.assertEqual(startup_data_state(self.root),"UNSAFE")
    def test_07_symlink_db_is_unsafe_if_supported(self):
        self.root.mkdir()
        old=self.base/"outside.sqlite";old.write_bytes(b"OTHER")
        try:(self.root/"lab.db").symlink_to(old)
        except (OSError,NotImplementedError):self.skipTest("symlinks unavailable")
        self.assertEqual(startup_data_state(self.root),"UNSAFE")
        self.assertEqual(old.read_bytes(),b"OTHER")
    def test_08_symlink_directory_is_unsafe_if_supported(self):
        another=self.base/"outside"
        another.mkdir()
        try:self.root.symlink_to(another,target_is_directory=True)
        except (OSError,NotImplementedError):self.skipTest("symlinks unavailable")
        self.assertEqual(startup_data_state(self.root),"UNSAFE")
if __name__=="__main__":unittest.main()
