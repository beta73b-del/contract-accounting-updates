"""Stock parity audit uses strictly synthetic data and redacted result fields."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from stock_projection import project,StockError
from stock_parity_audit import audit

class StockAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.root=Path(self.tmp.name)
        events=[
            {"id":"receipt-test","kind":"receipt","lines":[
                {"product":"Synthetic model","quantity":10,"cents":10000}],
             "needs":[],"contractId":"","phase":"","date":"2026-11-25"},
            {"id":"reserve-test","kind":"reserve","lines":[],
             "needs":[{"product":"Synthetic model","quantity":13}],
             "contractId":"synthetic-contract","phase":"reserved","date":"2026-11-25"}
        ]
        model=project(events)
        self.data={
            "stock":{
                "events":events,
                "items":list(model["inventory"].values()),
                "phases":{k:v["phase"] for k,v in model["contracts"].items()},
            }
        }
        self.write()
    def tearDown(self):self.tmp.cleanup()
    def write(self):
        (self.root/"source_api.json").write_text(
            json.dumps(self.data,ensure_ascii=False),encoding="utf-8")
    def test_pass_and_no_sensitive_identifiers_in_report(self):
        report=audit(self.root,self.root/"result.json")
        self.assertEqual(report["result"],"PASS")
        self.assertEqual(report["fieldMismatchCounts"],{})
        self.assertNotIn("synthetic-contract",(self.root/"result.json").read_text())
        self.assertNotIn("Synthetic model",(self.root/"result.json").read_text())
    def test_changed_stock_balance_detected(self):
        self.data["stock"]["items"][-1]["cents"]+=1
        self.write()
        r=audit(self.root)
        self.assertEqual(r["result"],"FAIL")
        self.assertEqual(r["fieldMismatchCounts"],{"cents":1})
    def test_missing_phases_detected(self):
        self.data["stock"]["phases"]["synthetic-contract"]="shipped"
        self.write()
        self.assertEqual(audit(self.root)["phaseMismatches"],1)
    def test_duplicate_reference_product_rejected(self):
        self.data["stock"]["items"].append(dict(self.data["stock"]["items"][0]))
        self.write()
        with self.assertRaises(StockError):audit(self.root)
    def test_orphan_receipt_correction_rejected(self):
        self.data["stock"]["events"].append({
            "id":"correction-not-linked","kind":"editReceipt", "target":"missing-receipt",
            "correctedQuantity":4,"correctedCents":4000,"lines":[],"needs":[],"contractId":"",
            "date":"2026-11-25","phase":""
        })
        self.write()
        with self.assertRaises(StockError):audit(self.root)
    def test_type_mismatch_is_not_hidden_by_python_boolean_equality(self):
        self.data["stock"]["items"][0]["quantity"]=True
        self.write()
        r=audit(self.root)
        self.assertEqual(r["result"],"FAIL")
        self.assertEqual(r["fieldMismatchCounts"],{"quantity":1})
    def test_report_never_overwritten(self):
        audit(self.root,self.root/"report.json")
        with self.assertRaises(FileExistsError):
            audit(self.root,self.root/"report.json")
if __name__=="__main__":unittest.main()
