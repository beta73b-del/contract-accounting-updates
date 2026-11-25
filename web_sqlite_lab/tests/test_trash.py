"""Recycle bin on strictly synthetic SQLite databases; no production Excel."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from store import Store,DataError,Conflict

def rec(cid,status="формирование",revision=0,qty=3):
    return {"id":cid,"revision":revision,"status":status,
            "customer":"Тестовый заказчик","platform":"Тест",
            "contractNumber":"0001","contractDate":"","signBy":"",
            "fulfillBy":"","handoverDate":"","paymentDue":"",
            "items":[{"product":"Рутокен Lite 1010","quantity":str(qty),"unitPrice":"1300.00"}]}

class TrashTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix="sqlite-trash-synthetic-")
        self.path=Path(self.temp.name)/"isolated.sqlite"
        self.store=Store(self.path)
        self.store.add_product("Рутокен Lite 1010")
        self.store.receipt("receipt-trash-test","Рутокен Lite 1010",10,"1000.00")
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def save(self,cid,status="формирование",revision=0):
        return self.store.save_contract("save-"+cid+str(revision),rec(cid,status,revision))
    def stock(self):
        return next(x for x in self.store.stock_summary() if x["product"]=="Рутокен Lite 1010")
    def test_01_move_and_restore_same_id_items_and_no_shipment(self):
        x=self.save("contract001")
        initial=self.stock()["physical"]
        out=self.store.trash_contract("trash-test-001","contract001",x["revision"],"Ошибка ввода")
        self.assertEqual(out["revision"],2)
        self.assertEqual(self.store.list_contracts(),[])
        self.assertEqual(len(self.store.list_trashed()),1)
        self.assertEqual(self.store.list_trashed()[0]["items"][0]["quantity"],3)
        self.assertEqual(self.stock()["physical"],initial)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM shipments").fetchone()[0],0)
        restored=self.store.restore_contract("restore-test-001","contract001",2)
        self.assertEqual(restored["revision"],3)
        self.assertEqual(self.store.list_contracts()[0]["id"],"contract001")
        self.assertEqual(self.store.get_contract("contract001")["items"][0]["unitPrice"],"1300.00")
        self.assertEqual(self.store.list_trashed(),[])
        self.assertEqual(self.stock()["physical"],initial)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM contract_lifecycle").fetchone()[0],2)
    def test_02_trash_reserved_contract_releases_reservation(self):
        x=self.save("reserved001")
        y=self.store.save_contract("signed-001",rec("reserved001","заключен",x["revision"],qty=7))
        self.assertEqual(self.stock()["reserved"],7)
        self.store.trash_contract("trash-test-002","reserved001",y["revision"])
        self.assertEqual((self.stock()["physical"],self.stock()["reserved"],self.stock()["available"]),(10,0,10))
        self.store.restore_contract("restore-test-002","reserved001",3)
        self.assertEqual((self.stock()["physical"],self.stock()["reserved"],self.stock()["available"]),(10,7,3))
    def test_03_prevent_delete_after_dispatch(self):
        x=self.save("shipped001")
        signed=self.store.save_contract("signed-003",rec("shipped001","заключен",x["revision"]))
        sent=self.store.save_contract("sent-003",rec("shipped001","Отправлен",signed["revision"]))
        before=dict(self.stock())
        with self.assertRaises(DataError):
            self.store.trash_contract("trash-test-003","shipped001",sent["revision"])
        self.assertEqual(self.stock(),before)
        self.assertEqual(self.store.list_trashed(),[])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM shipments").fetchone()[0],1)
    def test_04_reject_wrong_revision_and_replay_idempotent(self):
        x=self.save("version001")
        out=self.store.trash_contract("trash-test-004","version001",x["revision"])
        self.assertEqual(out,self.store.trash_contract("trash-test-004","version001",x["revision"]))
        with self.assertRaises(Conflict):
            self.store.trash_contract("trash-test-005","version001",1)
        with self.assertRaises(Conflict):
            self.store.restore_contract("restore-test-bad","version001",1)
        self.assertEqual(len(self.store.list_trashed()),1)
        restored=self.store.restore_contract("restore-test-good","version001",2)
        self.assertEqual(restored,self.store.restore_contract("restore-test-good","version001",2))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM contract_lifecycle").fetchone()[0],2)
    def test_05_cannot_edit_trashed_contract(self):
        x=self.save("edittrash01")
        self.store.trash_contract("trash-test-006","edittrash01",x["revision"])
        with self.assertRaises(Conflict):
            self.store.save_contract("changed-data-007",rec("edittrash01",revision=2))
        self.assertEqual(self.store.list_contracts(),[])
    def test_06_persist_reopen_and_exact_backup(self):
        x=self.save("reopen0001")
        self.store.trash_contract("trash-test-008","reopen0001",x["revision"])
        before=self.store.backup(self.path.parent/"before-restart.sqlite")
        self.assertTrue(Path(before["file"]).is_file())
        self.store.close()
        self.store=Store(self.path)
        self.assertEqual(len(self.store.list_trashed()),1)
        self.assertEqual(self.store.verify()["trashedContracts"],1)
        with closing(sqlite3.connect(before["file"])) as copy:
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM contract_trash").fetchone()[0],1)
        self.store.restore_contract("restore-after-reopen","reopen0001",2)
        self.assertEqual(len(self.store.list_contracts()),1)
    def test_07_refuse_duplicate_ids_and_unknown_operations(self):
        x=self.save("dupe00001")
        self.store.trash_contract("trash-test-009","dupe00001",x["revision"])
        with self.assertRaises(Conflict):
            self.store.trash_contract("trash-test-010","dupe00001",2)
        with self.assertRaises(DataError):
            self.store.restore_contract("restore-unknown","unknown-contract",1)
    def test_08_migrate_v2_backup_before_schema(self):
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TABLE contract_trash")
            db.execute("DROP TABLE contract_lifecycle")
            db.execute("PRAGMA user_version=2")
            db.commit()
        self.store=Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],3)
        backups=list((self.path.parent/"backups").glob("before-v3-*.sqlite"))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],2)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='contract_trash'").fetchone())
    def test_09_no_permanent_delete_method_and_history_remains(self):
        x=self.save("protected01")
        self.store.trash_contract("trash-test-011","protected01",x["revision"])
        self.assertFalse(hasattr(self.store,"purge_contract"))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0],1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM contract_lines").fetchone()[0],1)
    def test_10_persistence_of_unshipped_reservation_after_restart(self):
        x=self.save("stockreserve")
        s=self.store.save_contract("signed-stock-reserve",rec("stockreserve","заключен",x["revision"],qty=9))
        self.assertEqual(self.stock()["reserved"],9)
        self.store.trash_contract("trash-stock-reserve","stockreserve",s["revision"])
        self.store.close();self.store=Store(self.path)
        self.assertEqual(self.stock()["reserved"],0)
        self.store.restore_contract("restore-stock-reserve","stockreserve",3)
        self.assertEqual(self.stock()["reserved"],9)
if __name__=="__main__":unittest.main()
