"""R1/R2 private-source audit and loss-preserving staging, all artificial data."""
from contextlib import closing
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import sqlite3
import unittest

from openpyxl import Workbook
from preserve_excel_snapshot import snapshot
from source_audit import audit,AuditError
from staging_import import import_snapshot

class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.app=self.base/"web"/"Приложение"
        (self.app/"data"/"letter-drafts").mkdir(parents=True)
        (self.app.parent/"Документы"/"demo").mkdir(parents=True)
        self.wb=Workbook()
        ws=self.wb.active;ws.title="Контракты"
        ws["A1"]="ID";ws["M1"]="Статус";ws["P1"]="check"
        ws["A2"]="00000001";ws["M2"]="заключен";ws["H2"]=1234.56
        ws["I2"]="=H2*2";ws["X2"]="0000033"
        stock=self.wb.create_sheet("Склад_журнал")
        stock["A1"]="Ledger";stock["A2"]="=2+2";stock.sheet_state="hidden"
        self.xlsx=self.app/"data"/"База_контрактов.xlsx"
        self.wb.save(self.xlsx);self.wb.close()
        for fn in ("notes.json","competitors.json"):
            (self.app/"data"/fn).write_text("{}",encoding="utf8")
        (self.app/"data"/"contract-folders.json").write_text(json.dumps(
            {"entries":[{"id":"00000001","path":"docs:demo","created":""}]}),encoding="utf8")
        (self.app.parent/"Документы"/"demo"/"акт.pdf").write_bytes(b"SYNTHETIC DOCUMENT")
        self.api={"records":[{"id":"00000001","contractNumber":"0000033","status":"заключен","amount":"1234.56"}],
                  "stock":{"events":[]},"finance":{"expenses":[],"rates":[]},"notes":{"revision":0},"owner":"QA"}
        self.snap=self.base/"frozen"
        self.db=self.base/"stage.sqlite"
        self.make_snapshot()
    def make_snapshot(self):
        with patch("preserve_excel_snapshot.urlopen",
                   return_value=BytesIO(json.dumps(self.api,ensure_ascii=False).encode())):
            snapshot(self.app,self.snap)
    def tearDown(self):
        self.temp.cleanup()
    def test_01_audit_ids_and_formulas(self):
        r=audit(self.snap)
        self.assertEqual(r["checks"]["rawContractCount"],1)
        self.assertEqual(r["checks"]["inExcelNotAPI"],0)
        self.assertEqual(r["checks"]["contractStatusMismatches"],0)
        self.assertEqual(r["checks"]["workbookFormulaCells"],2)
        self.assertEqual(r["checks"]["workbookSheets"],2)
        self.assertEqual(r["snapshotStatus"],"SOURCE_COPY_VERIFIED")
    def test_02_raw_import_including_hidden_sheet(self):
        result=import_snapshot(self.snap,self.db)
        self.assertEqual(result["status"],"RAW_STAGING_ONLY")
        self.assertEqual((result["sheetCount"],result["formulaCells"],result["contracts"]),(2,2,1))
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertEqual(db.execute("SELECT state FROM workbook_sheets WHERE name='Склад_журнал'").fetchone()[0],"hidden")
            raw=json.loads(db.execute("SELECT payload_json FROM raw_contracts").fetchone()[0])
            self.assertEqual(raw["contractNumber"],"0000033")
            formula=json.loads(db.execute("SELECT raw_json FROM workbook_cells WHERE coordinate='I2' AND sheet_index=1").fetchone()[0])
            self.assertEqual(formula["value"],"=H2*2")
            string=json.loads(db.execute("SELECT raw_json FROM workbook_cells WHERE coordinate='X2' AND sheet_index=1").fetchone()[0])
            self.assertEqual(string["value"],"0000033")
    def test_03_original_excel_is_unchanged(self):
        original=self.xlsx.read_bytes()
        import_snapshot(self.snap,self.db)
        self.assertEqual(self.xlsx.read_bytes(),original)
    def test_04_import_never_overwrites(self):
        import_snapshot(self.snap,self.db)
        original=self.db.read_bytes()
        with self.assertRaises(FileExistsError):import_snapshot(self.snap,self.db)
        self.assertEqual(self.db.read_bytes(),original)
    def test_05_detects_snapshot_corruption(self):
        path=self.snap/"source"/"База_контрактов.xlsx"
        with path.open("ab") as f:f.write(b"corrupted")
        with self.assertRaises(AuditError):import_snapshot(self.snap,self.db)
        self.assertFalse(self.db.exists())
    def test_06_detects_api_excel_status_mismatch(self):
        # All source hashes must remain valid; create a separate legitimate but disagreeing snapshot.
        self.snap.rename(self.base/"old")
        self.snap=self.base/"frozen"
        self.xlsx.unlink()
        self.wb=Workbook()
        ws=self.wb.active;ws.title="Контракты";ws["A2"]="00000001";ws["M2"]="Вручен"
        self.wb.save(self.xlsx);self.wb.close()
        self.make_snapshot()
        with self.assertRaises(ImportError):
            import_snapshot(self.snap,self.db)
        self.assertFalse(self.db.exists())
if __name__=="__main__":unittest.main()
