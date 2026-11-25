"""Synthetic-only SQLite lab regression. Never imports or opens the working Excel file."""
import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from store import Store, DataError, Conflict, amount, money

def contract(cid, status="формирование", qty=3, product="Рутокен Lite 1010", revision=0, when=""):
    return {
        "id":cid, "revision":revision, "status":status,
        "customer":"Вымышленный заказчик", "platform":"Тестовая площадка",
        "contractNumber":"0000123", "contractDate":when, "signBy":"",
        "fulfillBy":"", "handoverDate":"", "paymentDue":"",
        "items":[{"product":product,"quantity":str(qty),"unitPrice":"1500.25"}],
    }

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"synthetic.sqlite"
        self.s=Store(self.path)
        self.s.add_product("Рутокен Lite 1010")
        self.s.add_product("Рутокен ЭЦП 3.0 3120")

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def test_01_exact_kopecks(self):
        self.assertEqual(money("1 250,08"),125008)
        self.assertEqual(amount(125008),"1250.08")
        for bad in ("1.001","-2","1e5","abc"):
            with self.subTest(bad=bad),self.assertRaises(DataError): money(bad)
        with self.assertRaises(DataError):money(1.2)

    def test_02_receipt_and_cost(self):
        self.s.receipt("rcpt-0001","Рутокен Lite 1010",10,"1000.01")
        row=self.s.stock_summary()[1] if self.s.stock_summary()[1]["product"]=="Рутокен Lite 1010" else self.s.stock_summary()[0]
        self.assertEqual((row["physical"],row["inventoryCost"]),(10,"10000.10"))

    def test_03_idempotent_receipt(self):
        a=self.s.receipt("rcpt-0002","Рутокен Lite 1010",10,"1000")
        b=self.s.receipt("rcpt-0002","Рутокен Lite 1010",10,"1000")
        self.assertEqual(a,b)
        self.assertEqual(self.s._stock("Рутокен Lite 1010")["physical_qty"],10)

    def test_04_conflicting_op_rejected(self):
        self.s.receipt("rcpt-0003","Рутокен Lite 1010",10,"1000")
        with self.assertRaises(Conflict):
            self.s.receipt("rcpt-0003","Рутокен Lite 1010",11,"1000")

    def test_05_initial_draft(self):
        x=self.s.save_contract("save-0001",contract("abcde"))
        self.assertEqual((x["revision"],x["status"],x["contract_date"]),(1,"формирование",""))
        self.assertEqual(x["contract_number"],"0000123")

    def test_06_conclusion_date_and_manual_edit(self):
        d=self.s.save_contract("save-0002",contract("conclude"))
        r=contract("conclude","заключен",revision=d["revision"])
        x=self.s.save_contract("save-0003",r)
        self.assertRegex(x["contract_date"],r"^\d{4}-\d{2}-\d{2}$")
        r["revision"]=x["revision"];r["contractDate"]="2026-11-18"
        y=self.s.save_contract("save-0004",r)
        self.assertEqual(y["contract_date"],"2026-11-18")

    def test_date_not_lost_when_changing_signed_contract_to_shipped(self):
        self.s.receipt("receipt-date-preserve","Рутокен Lite 1010",10,"1000.00")
        draft=self.s.save_contract("save-date-draft",contract("datepreserve"))
        signed=self.s.save_contract("save-date-signed",contract("datepreserve","заключен",revision=draft["revision"]))
        self.assertTrue(signed["contract_date"])
        # Simulate a stale UI that does not send the previous signed date.
        shipped=self.s.save_contract("save-date-shipped",contract("datepreserve","Отправлен",revision=signed["revision"]))
        self.assertEqual(shipped["contract_date"],signed["contract_date"])
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM shipments").fetchone()[0],1)

    def test_07_reserve_and_shortage(self):
        self.s.receipt("rcpt-0004","Рутокен Lite 1010",2,"1200")
        self.s.save_contract("save-0005",contract("reserve","заключен",qty=5))
        r=next(x for x in self.s.stock_summary() if x["product"]=="Рутокен Lite 1010")
        self.assertEqual((r["physical"],r["reserved"],r["available"],r["shortage"]),(2,5,-3,3))
        self.s.receipt("rcpt-0005","Рутокен Lite 1010",4,"1200")
        r=next(x for x in self.s.stock_summary() if x["product"]=="Рутокен Lite 1010")
        self.assertEqual((r["physical"],r["reserved"],r["available"],r["shortage"]),(6,5,1,0))

    def test_08_ship_exactly_once(self):
        self.s.receipt("rcpt-0006","Рутокен Lite 1010",10,"1000")
        a=self.s.save_contract("save-0006",contract("ship1","заключен"))
        r=contract("ship1","Отправлен",revision=a["revision"])
        b=self.s.save_contract("save-0007",r)
        self.assertEqual(self.s._stock("Рутокен Lite 1010")["physical_qty"],7)
        r["revision"]=b["revision"];r["status"]="Вручен";r["handoverDate"]="2026-11-20"
        self.s.save_contract("save-0008",r)
        self.assertEqual(self.s._stock("Рутокен Lite 1010")["physical_qty"],7)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM shipments").fetchone()[0],1)

    def test_09_replayed_save_without_second_shipment(self):
        self.s.receipt("rcpt-0007","Рутокен Lite 1010",10,"1000")
        a=self.s.save_contract("save-0009",contract("ship2","заключен"))
        r=contract("ship2","Отправлен",revision=a["revision"])
        first=self.s.save_contract("save-0010",r)
        same=self.s.save_contract("save-0010",r)
        self.assertEqual(first,same)
        self.assertEqual(self.s._stock("Рутокен Lite 1010")["physical_qty"],7)

    def test_10_optimistic_revision_conflict(self):
        a=self.s.save_contract("save-0011",contract("conf1"))
        r=contract("conf1","заключен",revision=a["revision"])
        self.s.save_contract("save-0012",r)
        with self.assertRaises(Conflict):self.s.save_contract("save-0013",r)

    def test_11_no_physical_shortage(self):
        self.s.receipt("rcpt-0008","Рутокен Lite 1010",2,"1200")
        a=self.s.save_contract("save-0014",contract("shrt1","заключен",qty=3))
        r=contract("shrt1","Отправлен",qty=3,revision=a["revision"])
        with self.assertRaises(DataError):self.s.save_contract("save-0015",r)
        self.assertEqual(self.s._stock("Рутокен Lite 1010")["physical_qty"],2)
        self.assertEqual(self.s.get_contract("shrt1")["status"],"заключен")

    def test_12_other_contract_reserved_stock_protected(self):
        self.s.receipt("rcpt-0009","Рутокен Lite 1010",10,"1100")
        self.s.save_contract("save-0016",contract("other1","заключен",qty=8))
        a=self.s.save_contract("save-0017",contract("other2","заключен",qty=3))
        with self.assertRaises(DataError):
            self.s.save_contract("save-0018",contract("other2","Отправлен",qty=3,revision=a["revision"]))
        self.assertEqual(self.s._stock("Рутокен Lite 1010")["physical_qty"],10)

    def test_13_multi_item_atomic_rollback(self):
        self.s.receipt("rcpt-0010","Рутокен Lite 1010",5,"1000")
        self.s.receipt("rcpt-0011","Рутокен ЭЦП 3.0 3120",1,"1800")
        a=contract("multi","заключен",qty=2)
        a["items"].append({"product":"Рутокен ЭЦП 3.0 3120","quantity":"2","unitPrice":"2000"})
        self.s.save_contract("save-0019",a)
        a["status"]="Отправлен";a["revision"]=1
        with self.assertRaises(DataError):self.s.save_contract("save-0020",a)
        self.assertEqual(self.s._stock("Рутокен Lite 1010")["physical_qty"],5)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM shipments").fetchone()[0],0)

    def test_14_post_shipment_changes_rejected(self):
        self.s.receipt("rcpt-0012","Рутокен Lite 1010",10,"1000")
        a=self.s.save_contract("save-0021",contract("locked","заключен"))
        b=self.s.save_contract("save-0022",contract("locked","Отправлен",revision=a["revision"]))
        with self.assertRaises(DataError):
            self.s.save_contract("save-0023",contract("locked","Отправлен",qty=5,revision=b["revision"]))
        with self.assertRaises(DataError):
            self.s.save_contract("save-0024",contract("locked","заключен",revision=b["revision"]))

    def test_15_integrity_and_verified_backup(self):
        self.s.save_contract("save-0025",contract("backup1"))
        target=Path(self.temp.name)/"backup"/"copy.sqlite"
        proof=self.s.backup(target)
        self.assertEqual(len(proof["sha256"]),64)
        self.assertEqual(self.s.verify()["foreignKeys"],0)
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0],1)
        with self.assertRaises(DataError):self.s.backup(target)

    def test_16_no_unknown_products(self):
        with self.assertRaises(DataError):
            self.s.save_contract("save-0026",contract("ghost1",product="НЕСУЩЕСТВУЮЩИЙ"))
        self.assertEqual(self.s.verify()["contracts"],0)

    def test_17_bad_dates(self):
        r=contract("bad-date",when="2026-02-30")
        with self.assertRaises(DataError):self.s.save_contract("save-0027",r)
        self.assertEqual(self.s.verify()["contracts"],0)

if __name__=="__main__":
    unittest.main(verbosity=2)
