"""Actual synthetic XLSX + source API snapshot -> active v4 SQLite trial."""
import json,uuid,unittest,zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from io import BytesIO
from unittest.mock import patch
from openpyxl import Workbook
from preserve_excel_snapshot import snapshot
from excel_import import build_trial_archive,MigrationError
from stock_projection import project
from recovery_center import restore_first_install
from store import Store
from file_vault import FileVault
from finance_ledger import FinanceLedger
from transfer_package import inspect_transfer

class ExcelImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.app=self.root/"Excel"/"Приложение";self.data=self.app/"data"
        (self.data/"letter-drafts").mkdir(parents=True)
        (self.data/"letter-drafts"/"draft.json").write_text("{}",encoding="utf8")
        self.cid="00000001";self.ship="00000002";self.product="Рутокен 1010"
        self.book=self.data/"База_контрактов.xlsx"
        wb=Workbook();ws=wb.active;ws.title="Контракты"
        ws["A1"]="ID";ws["M1"]="Статус";ws["A2"]=self.cid;ws["M2"]="заключен"
        ws["A3"]=self.ship;ws["M3"]="Оплачен";ws["X2"]="0000042"
        wb.save(self.book);wb.close()
        for f in ("notes.json","competitors.json"):(self.data/f).write_text("{}",encoding="utf8")
        docs=self.app.parent/"Документы"/"demo";docs.mkdir(parents=True);(docs/"Акт.txt").write_bytes(b"proof")
        (self.data/"contract-folders.json").write_text(json.dumps({"entries":[{"id":self.cid,"path":"docs:demo"}]}),encoding="utf8")
        def record(cid,status,q):
            return {"id":cid,"status":status,"customer":"Synthetic","platform":"Test","contractNumber":"0000042",
                "contractDate":"2026-11-25","signBy":"","fulfillBy":"","handoverDate":"2026-11-25" if status=="Оплачен" else "",
                "paymentDue":"","items":[{"product":self.product,"quantity":q,"unitPrice":"1500.00"}],"amount":str(q*1500)}
        ev=[{"id":"receipt1","kind":"receipt","date":"2026-11-25","lines":[{"product":self.product,"quantity":20,"cents":2500000}],"needs":[]},
            {"id":"ship1","kind":"ship","date":"2026-11-25","lines":[{"product":self.product,"quantity":-3,"cents":-375000}],"contractId":self.ship,"phase":"shipped","needs":[{"product":self.product,"quantity":3}]},
            {"id":"reserve1","kind":"reserve","date":"2026-11-25","lines":[],"contractId":self.cid,"phase":"reserved","needs":[{"product":self.product,"quantity":7}]}]
        projection=project(ev)
        self.payload={"records":[record(self.cid,"заключен",7),record(self.ship,"Оплачен",3)],
            "stock":{"events":ev,"items":list(projection["inventory"].values()),"phases":{k:v["phase"] for k,v in projection["contracts"].items()}},
            "finance":{"rates":{"2027":15},"expenses":[{"id":"a"*32,"date":"2026-11-25","amount":"100.40","comment":"Test","revision":2}],"deletedExpenses":[]},"notes":{}}
        self.snap=self.root/"snapshot";self.archive=self.root/"trial.zip"
    def capture(self):
        with patch("preserve_excel_snapshot.urlopen",return_value=BytesIO(json.dumps(self.payload).encode())):
            snapshot(self.app,self.snap)
    def test_actual_xlsx_to_running_schema_and_no_double_shipment(self):
        before=self.book.read_bytes();self.capture()
        r=build_trial_archive(self.snap,self.archive)
        self.assertEqual(r["activeContracts"],2);self.assertEqual(r["documents"],1)
        self.assertEqual(r["checks"]["stockBalancesReservesCost"],"PASS")
        self.assertEqual(before,self.book.read_bytes())
        target=self.root/"new";restore_first_install(self.archive,target)
        db=Store(target/"lab.db")
        try:
            self.assertEqual(db.get_contract(self.cid)["contract_number"],"0000042")
            x=next(i for i in db.stock_summary() if i["product"]==self.product)
            self.assertEqual((x["physical"],x["reserved"],x["available"],x["inventoryCost"]),(17,7,10,"21250.00"))
            self.assertEqual(db.db.execute("SELECT qty,cost FROM shipments").fetchone()[0],3)
            self.assertEqual(db.db.execute("SELECT COUNT(*) FROM excel_import_stock_events").fetchone()[0],3)
            self.assertEqual(FinanceLedger(db).payload()["expenses"][0]["amount"],"100.40")
            files=FileVault(db);meta=files.list_files()[0]
            self.assertEqual(files.get_active(meta["id"])[1].read_bytes(),b"proof")
            self.assertEqual((target/"attachments"/"Excel-source"/"source"/"База_контрактов.xlsx").read_bytes(),before)
            self.assertTrue((target/"attachments"/"excel-import-report.json").is_file())
        finally:db.close()
    def test_inconsistent_source_stock_blocks_archive(self):
        self.payload["stock"]["items"][0]["quantity"]+=1
        self.capture()
        with self.assertRaises(ValueError):build_trial_archive(self.snap,self.archive)
        self.assertFalse(self.archive.exists())
    def test_source_balance_preserved_when_replay_differs(self):
        item=self.payload["stock"]["items"][0]
        item["quantity"]+=2;item["cents"]+=250000;item["available"]+=2
        self.capture()
        report=build_trial_archive(self.snap,self.archive)
        self.assertEqual(report["checks"]["stockBalancesReservesCost"],"PASS")
        self.assertEqual(report["checks"]["stockHistoryReplay"],"WARN")
        self.assertFalse(report["stockImportLocked"])
        self.assertTrue(any(x.get("field")=="quantity" for x in report["stockWarnings"]))
        target=self.root/"opening";restore_first_install(self.archive,target)
        db=Store(target/"lab.db")
        try:
            x=next(i for i in db.stock_summary() if i["product"]==self.product)
            self.assertEqual((x["physical"],x["reserved"],x["available"],x["inventoryCost"]),(19,7,12,"23750.00"))
            self.assertFalse(db.stock_import_locked())
            self.assertEqual(db.db.execute("SELECT COUNT(*) FROM shipments").fetchone()[0],0)
            db.receipt("receipt-after-import",self.product,1,"1250")
            x=next(i for i in db.stock_summary() if i["product"]==self.product)
            self.assertEqual((x["physical"],x["reserved"],x["inventoryCost"]),(20,7,"25000.00"))
        finally:db.close()
    def test_reservation_from_excel_not_status(self):
        # Journal is coherent, but a source reserve can belong to a different status.
        self.payload["records"][0]["status"]="на подписи"
        wb=__import__("openpyxl").load_workbook(self.book)
        wb["Контракты"]["M2"]="на подписи";wb.save(self.book);wb.close()
        self.capture()
        report=build_trial_archive(self.snap,self.archive)
        self.assertEqual(report["checks"]["stockHistoryReplay"],"PASS")
        self.assertEqual(report["checks"]["reservationAllocation"],"WARN")
        self.assertTrue(report["stockImportLocked"])
        target=self.root/"reserve";restore_first_install(self.archive,target)
        db=Store(target/"lab.db")
        try:
            x=next(i for i in db.stock_summary() if i["product"]==self.product)
            self.assertEqual(x["reserved"],7)
            self.assertEqual(x["available"],10)
            from store import DataError
            with self.assertRaises(DataError):db.receipt("receipt-guard",self.product,1,"100")
            with self.assertRaises(DataError):db.save_contract("save-guard-01",dict(self.payload["records"][0],revision=1))
            with self.assertRaises(DataError):db.trash_contract("trash-guard-01",self.cid,1)
        finally:db.close()
    def test_history_mismatch_without_reservations_allows_new_receipts(self):
        self.payload["records"][0]["status"]="формирование"
        wb=__import__("openpyxl").load_workbook(self.book)
        wb["Контракты"]["M2"]="формирование";wb.save(self.book);wb.close()
        stock=self.payload["stock"];stock["events"].pop();stock["phases"].pop(self.cid)
        proj=project(stock["events"]);stock["items"]=list(proj["inventory"].values())
        item=stock["items"][0];item["quantity"]+=2;item["cents"]+=250000;item["available"]+=2
        self.capture()
        report=build_trial_archive(self.snap,self.archive)
        self.assertFalse(report["stockImportLocked"])
        self.assertEqual(report["checks"]["stockHistoryReplay"],"WARN")
        target=self.root/"unreserved";restore_first_install(self.archive,target)
        db=Store(target/"lab.db")
        try:
            db.receipt("receipt-new-01",self.product,1,"1250")
            x=next(i for i in db.stock_summary() if i["product"]==self.product)
            self.assertEqual((x["physical"],x["reserved"],x["inventoryCost"]),(20,0,"25000.00"))
        finally:db.close()
    def test_inconsistent_contract_total_preserved(self):
        self.payload["records"][0]["amount"]="1.00";self.capture()
        report=build_trial_archive(self.snap,self.archive)
        self.assertEqual(report["checks"]["financialArithmetic"],"WARN")
        self.assertEqual(report["financialWarnings"][0]["sourceAmount"],"1.00")
        self.assertEqual(report["financialWarnings"][0]["calculatedAmount"],"10500.00")
        target=self.root/"different";restore_first_install(self.archive,target)
        db=Store(target/"lab.db")
        try:
            self.assertEqual(db.get_contract(self.cid)["importFinancial"]["preservedAmount"],"1.00")
            from store import DataError
            raw=dict(self.payload["records"][0],revision=1)
            with self.assertRaisesRegex(DataError,"финансовыми замечаниями"):
                db.save_contract("test-edit-001",raw)
        finally:db.close()
    def test_no_overwrite(self):
        self.capture();self.archive.write_bytes(b"keep")
        with self.assertRaises(MigrationError):build_trial_archive(self.snap,self.archive)
        self.assertEqual(self.archive.read_bytes(),b"keep")
    def test_corrupted_source_blocks(self):
        self.capture();(self.snap/"source_api.json").write_text("{}",encoding="utf8")
        with self.assertRaises(ValueError):build_trial_archive(self.snap,self.archive)
        self.assertFalse(self.archive.exists())

    def test_blank_price_preserved_not_zero(self):
        self.payload["records"][0]["items"][0]["unitPrice"]=""
        self.payload["records"][0]["amount"]=""
        self.capture()
        report=build_trial_archive(self.snap,self.archive)
        self.assertFalse(report["contractKopecksComplete"])
        self.assertEqual(report["missingPricePositions"],[{"id":self.cid,"positions":[1]}])
        target=self.root/"missing";restore_first_install(self.archive,target)
        db=Store(target/"lab.db")
        try:
            item=db.get_contract(self.cid)["items"][0]
            self.assertEqual(item["unitPrice"],"")
            self.assertTrue(item["priceMissing"])
        finally:db.close()
    def test_blank_price_exact_from_total(self):
        self.payload["records"][0]["items"][0]["unitPrice"]=""
        self.capture()
        report=build_trial_archive(self.snap,self.archive)
        self.assertEqual(report["derivedPricePositions"],[{"id":self.cid,"positions":[1]}])
    def test_blank_price_fraction_preserved(self):
        self.payload["records"][0]["items"][0]["unitPrice"]=""
        self.payload["records"][0]["amount"]="10500.01"
        self.capture()
        report=build_trial_archive(self.snap,self.archive)
        self.assertEqual(report["financialWarnings"][0]["preservedAmount"],"10500.01")
        self.assertTrue(report["contractKopecksComplete"])
        target=self.root/"fraction";restore_first_install(self.archive,target)
        db=Store(target/"lab.db")
        try:
            item=db.get_contract(self.cid)
            self.assertEqual(item["items"][0]["unitPrice"],"")
            self.assertEqual(item["importFinancial"]["preservedAmount"],"10500.01")
            self.assertEqual(len(db.list_contracts()),2)
        finally:db.close()
    def test_invalid_price_and_conflicting_aliases_do_not_abort(self):
        self.payload["records"][0]["items"][0].update({"unitPrice":"1.2345","price":"1500.00","lineTotal":"10500.01"})
        self.capture()
        report=build_trial_archive(self.snap,self.archive)
        self.assertEqual(report["activeContracts"],2)
        self.assertEqual(report["checks"]["financialArithmetic"],"WARN")
    def test_malformed_amount_preserved_in_original(self):
        self.payload["records"][0]["amount"]="ошибка Excel"
        self.capture()
        report=build_trial_archive(self.snap,self.archive)
        self.assertEqual(report["activeContracts"],2)
        self.assertEqual(report["checks"]["financialArithmetic"],"WARN")
    def test_zero_price_is_not_missing(self):
        self.payload["records"][0]["items"][0]["unitPrice"]="0"
        self.payload["records"][0]["amount"]="0"
        self.capture()
        report=build_trial_archive(self.snap,self.archive)
        self.assertTrue(report["contractKopecksComplete"])
        target=self.root/"zero";restore_first_install(self.archive,target)
        db=Store(target/"lab.db")
        try:
            item=db.get_contract(self.cid)["items"][0]
            self.assertEqual(item["unitPrice"],"0.00")
            self.assertNotIn("priceMissing",item)
        finally:db.close()
