"""Financial forecast and ledger tests on synthetic data; no user files."""
from pathlib import Path
from contextlib import closing
from tempfile import TemporaryDirectory
import sqlite3
import unittest
import uuid

from store import Store
from finance_engine import forecast,FinanceError,kopecks
from finance_ledger import FinanceLedger,normalize,model

def key():return uuid.uuid4().hex
def record(year=2026,month=11,status="Вручен",tax="",amount="1000.00",
           cost="500.00",fee="20.00",logistics="30.00",other="10.00"):
    return {"year":year,"month":month,"status":status,"taxAmount":tax,
            "amount":amount,"cost":cost,"fee":fee,"logistics":logistics,"other":other,
            "check":"OK"}

class FinanceEngineTests(unittest.TestCase):
    def test_forecast_default_2026_and_no_double_cost(self):
        r=forecast([record()],2026)
        m=r["months"][10]
        # Contribution: 100000 - 50000 - 2000 - 3000 - 1000 = 44000; tax 2200
        self.assertEqual((m["contract"],m["cost"],m["autoTaxProfit"],m["tax"]),(100000,50000,44000,2200))
        self.assertEqual(m["expenses"],56000)
        self.assertEqual((m["profitBefore"],m["profitAfter"]),(44000,41800))
        self.assertEqual(m["expected"],100000)
        self.assertEqual(m["paid"],0)
        self.assertEqual(r["total"]["profitAfter"],41800)
    def test_paid_and_common_expense_once(self):
        r=forecast([record(status="Оплачен")],2026,{"rates":{},"expenses":[
            {"date":"2026-11-18","amount":"100.00","id":"test"}]})
        m=r["months"][10]
        # 440 - 100 = 340 tax profit; 5% = 17; 340 - 17 = 323
        self.assertEqual((m["common"],m["tax"],m["profitAfter"]),(10000,1700,32300))
        self.assertEqual((m["paid"],m["expected"]),(100000,0))
    def test_manual_tax_not_reassessed(self):
        r=forecast([record(tax="99.99"),record(tax="")],2026)
        m=r["months"][10]
        self.assertEqual(m["manualTax"],9999)
        self.assertEqual(m["tax"],9999+2200)
        self.assertEqual(m["autoTaxProfit"],44000)
    def test_rate_2027_unset_is_null(self):
        r=forecast([record(year=2027)],2027)
        self.assertIsNone(r["total"]["tax"])
        self.assertIsNone(r["total"]["profitAfter"])
        self.assertIsNone(r["months"][10]["tax"])
    def test_rate_2027_15pct(self):
        r=forecast([record(year=2027)],2027,{"rates":{"2027":15}})
        self.assertEqual(r["months"][10]["tax"],6600)
    def test_rounding_half_up(self):
        r=forecast([record(amount="0.01",cost="0",fee="0",logistics="0",other="0")],2026)
        self.assertEqual(r["months"][10]["tax"],0)
        # 0.10 * 5% is exactly 0.5 kopek -> 1 kopek
        r=forecast([record(amount="0.10",cost="0",fee="0",logistics="0",other="0")],2026)
        self.assertEqual(r["months"][10]["tax"],1)
    def test_skip_draft_deleted_invalid(self):
        rows=[record(),record(),record()]
        rows[0]["check"]="ДОПОЛНИТЬ"
        rows[1]["deleted"]=True
        self.assertEqual(forecast(rows,2026)["total"]["count"],1)
    def test_expenses_only_without_contract(self):
        r=forecast([],2026,{"rates":{},"expenses":[{"date":"2026-11-22","amount":"125.00"}]})
        self.assertEqual((r["months"][10]["common"],r["months"][10]["profitAfter"]),(12500,-12500))
    def test_invalid_date_amount_and_money(self):
        with self.assertRaises(FinanceError):kopecks("10.123")
        with self.assertRaises(FinanceError):
            forecast([],2026,{"expenses":[{"date":"2026-11-04","amount":"0"}]})
        self.assertEqual(kopecks("1 200,05"),120005)

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.db=Path(self.tmp.name)/"finance.sqlite"
        self.store=Store(self.db)
        self.ledger=FinanceLedger(self.store)
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def expense(self,action="create",eid=None,revision=0,amount="100.01",op=None):
        return {"action":action,"opId":op or key(),"id":eid or key(),
                "revision":revision,"date":"2026-11-26","amount":amount,"comment":"Test"}
    def test_create_edit_delete_and_persistence(self):
        a=self.expense()
        x=self.ledger.mutate(a)
        self.assertEqual((x["expenses"][0]["amount"],x["expenses"][0]["revision"]),("100.01",1))
        b=self.expense("edit",a["id"],1,"225.02")
        self.assertEqual(self.ledger.mutate(b)["expenses"][0]["amount"],"225.02")
        c={"action":"delete","opId":key(),"id":a["id"],"revision":2}
        self.assertEqual(self.ledger.mutate(c)["expenses"],[])
        self.store.close()
        self.store=Store(self.db)
        self.assertEqual(FinanceLedger(self.store).payload()["expenses"],[])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM finance_events").fetchone()[0],3)
    def test_idempotent_and_conflicting_id(self):
        e=self.expense()
        first=self.ledger.mutate(e)
        self.assertEqual(first,self.ledger.mutate(e))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM finance_events").fetchone()[0],1)
        changed=dict(e,amount="999.99")
        with self.assertRaises(FinanceError):self.ledger.mutate(changed)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM finance_events").fetchone()[0],1)
    def test_optimistic_revision_and_delete(self):
        e=self.expense()
        self.ledger.mutate(e)
        old=self.expense("edit",e["id"],5)
        with self.assertRaises(FinanceError):self.ledger.mutate(old)
        with self.assertRaises(FinanceError):
            self.ledger.mutate({"action":"delete","opId":key(),"id":e["id"],"revision":2})
        self.assertEqual(len(self.ledger.events()),1)
    def test_duplicate_expense_id_even_if_deleted(self):
        e=self.expense()
        self.ledger.mutate(e)
        self.ledger.mutate({"action":"delete","opId":key(),"id":e["id"],"revision":1})
        with self.assertRaises(FinanceError):self.ledger.mutate(self.expense(eid=e["id"]))
    def test_rate_change_previous_and_2026_not_allowed(self):
        k={"action":"rate","opId":key(),"year":2027,"rate":15,"previous":None}
        r=self.ledger.mutate(k)
        self.assertEqual(r["rates"],{"2027":15})
        stale={"action":"rate","opId":key(),"year":2027,"rate":5,"previous":None}
        with self.assertRaises(FinanceError):self.ledger.mutate(stale)
        ok={"action":"rate","opId":key(),"year":2027,"rate":5,"previous":15}
        self.assertEqual(self.ledger.mutate(ok)["rates"]["2027"],5)
        with self.assertRaises(FinanceError):
            self.ledger.mutate({"action":"rate","opId":key(),"year":2026,"rate":5,"previous":None})
    def test_date_bounds_and_invalid_amount(self):
        e=self.expense();e["date"]="2026-10-31"
        with self.assertRaises(FinanceError):self.ledger.mutate(e)
        e=self.expense();e["amount"]="1.001"
        with self.assertRaises(FinanceError):self.ledger.mutate(e)
        self.assertEqual(len(self.ledger.events()),0)
    def test_migration_v1_verified_backup(self):
        self.store.close()
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("DROP TABLE finance_events")
            c.execute("PRAGMA user_version=1")
        self.store=Store(self.db)
        self.ledger=FinanceLedger(self.store)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0],2)
        backups=list((self.db.parent/"backups").glob("before-v2-*.sqlite"))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],1)
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0],"ok")
    def test_schema_integrity(self):
        self.assertEqual(self.store.verify()["integrity"],"ok")
        self.assertEqual(self.store.db.execute("PRAGMA foreign_key_check").fetchall(),[])
if __name__=="__main__":unittest.main()
