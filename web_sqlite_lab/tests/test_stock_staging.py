"""Tests for read-only stock-event migration into a fresh staging SQLite file."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import copy
import json
import sqlite3
import unittest
from stock_projection import project
from stock_staging import build_stock_staging,StockStageError

class StockStagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.raw=self.root/'raw-source.sqlite'
        self.dest=self.root/'new-stock-staging.sqlite'
        self.events=[
            {'id':'r1','kind':'receipt','date':'2026-11-25','lines':[{'product':'Рутокен 1010','quantity':10,'cents':10000}], 'needs':[],'contractId':'','phase':''},
            {'id':'rv1','kind':'reserve','date':'2026-11-25','lines':[],'needs':[{'product':'Рутокен 1010','quantity':13}], 'contractId':'fake-contract','phase':'reserved'}
        ]
        self.stock={}
        self.write_src()
    def tearDown(self): self.tmp.cleanup()
    def write_src(self):
        p=project(self.events)
        self.stock={'events':copy.deepcopy(self.events),'items':list(p['inventory'].values()),'phases':{k:v['phase'] for k,v in p['contracts'].items()}}
        self.save()
    def save(self):
        with closing(sqlite3.connect(self.raw)) as con:
            con.execute('CREATE TABLE IF NOT EXISTS raw_sections(name TEXT PRIMARY KEY,payload_json TEXT NOT NULL)')
            con.execute('INSERT OR REPLACE INTO raw_sections VALUES (?,?)',('stock',json.dumps(self.stock,ensure_ascii=False)))
            con.commit()
    def test_exact_import_and_preserves_raw(self):
        before=self.raw.read_bytes()
        result=build_stock_staging(self.raw,self.dest)
        self.assertEqual(result['parity'],'PASS')
        self.assertEqual(result['events'],2)
        self.assertEqual(before,self.raw.read_bytes())
        with closing(sqlite3.connect(self.dest)) as con:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM stock_event_archive').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT reserved_qty FROM stock_projected_item WHERE product=?',('Рутокен 1010',)).fetchone()[0],13)
            self.assertEqual(con.execute('SELECT shortage_qty FROM stock_projected_item WHERE product=?',('Рутокен 1010',)).fetchone()[0],3)
    def test_receipt_revaluation_and_source_json_preservation(self):
        self.events = [
            {'id':'r1','kind':'receipt','date':'2026-11-25',
             'lines':[{'product':'Рутокен 1010','quantity':10,'cents':10000}],
             'needs':[],'contractId':'','phase':''},
            {'id':'s1','kind':'ship','date':'2026-11-25',
             'lines':[{'product':'Рутокен 1010','quantity':-3,'cents':-3000}],
             'needs':[{'product':'Рутокен 1010','quantity':3}],
             'contractId':'synthetic-contract','phase':'shipped'},
            {'id':'fix','kind':'editReceipt','date':'2026-11-26',
             'lines':[{'product':'Рутокен 1010','quantity':0,'cents':0}],
             'target':'r1','correctedQuantity':5,'correctedCents':7500,
             'needs':[],'contractId':'','phase':''},
        ]
        self.write_src()
        build_stock_staging(self.raw,self.dest)
        with closing(sqlite3.connect(self.dest)) as con:
            item=con.execute('SELECT physical_qty,inventory_cost_kopecks FROM stock_projected_item WHERE product=?',('Рутокен 1010',)).fetchone()
            self.assertEqual(item,(2,3000))
            original=json.loads(con.execute("SELECT payload_json FROM raw_sections WHERE name='stock'").fetchone()[0])
            self.assertEqual(original['events'],self.events)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM stock_event_archive').fetchone()[0],3)
    def test_rejected_balance_mismatch_no_destination(self):
        self.stock['items'][0]['quantity']+=1
        self.save()
        with self.assertRaises(StockStageError):build_stock_staging(self.raw,self.dest)
        self.assertFalse(self.dest.exists())
    def test_never_overwrites_existing_staging(self):
        self.dest.write_bytes(b'KEEP')
        with self.assertRaises(FileExistsError):build_stock_staging(self.raw,self.dest)
        self.assertEqual(self.dest.read_bytes(),b'KEEP')
    def test_missing_stock_section(self):
        with closing(sqlite3.connect(self.raw)) as con:
            con.execute('DELETE FROM raw_sections')
            con.commit()
        with self.assertRaises(StockStageError):build_stock_staging(self.raw,self.dest)
        self.assertFalse(self.dest.exists())
    def test_orphan_correction_is_rejected(self):
        self.stock['events'].append({'id':'fix','kind':'editReceipt','target':'missing','correctedQuantity':1,'correctedCents':100,'lines':[]})
        self.save()
        with self.assertRaises(StockStageError):build_stock_staging(self.raw,self.dest)
        self.assertFalse(self.dest.exists())
    def test_invalid_source_db_rejected(self):
        self.raw.unlink()
        self.raw.write_bytes(b'DAMAGED SQLITE')
        with self.assertRaises(sqlite3.DatabaseError):build_stock_staging(self.raw,self.dest)
    def test_duplicate_product_in_reference_rejected(self):
        self.stock['items'].append(copy.deepcopy(self.stock['items'][0]))
        self.save()
        with self.assertRaises(StockStageError):build_stock_staging(self.raw,self.dest)
    def test_no_source_changes_during_failure(self):
        self.stock['phases']['fake-contract']='shipped'
        self.save()
        before=self.raw.read_bytes()
        with self.assertRaises(StockStageError):build_stock_staging(self.raw,self.dest)
        self.assertEqual(self.raw.read_bytes(),before)
if __name__=='__main__':unittest.main()
