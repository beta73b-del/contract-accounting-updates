"""R2: loss-preserving workbook/JSON staging into a NEW SQLite file.

Reads only an already verified, immutable private preservation snapshot.
Never accesses live Excel or the operational SQLite database.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime,date,time
from decimal import Decimal
from pathlib import Path
import json
import os
import sqlite3
import sys
import uuid

from source_audit import audit,digest

class ImportErrorSafe(ValueError):pass

def encode_cell(value):
    if value is None:return {"kind":"none","value":None}
    if isinstance(value,bool):return {"kind":"bool","value":value}
    if isinstance(value,int):return {"kind":"int","value":str(value)}
    if isinstance(value,float):return {"kind":"float","value":repr(value)}
    if isinstance(value,Decimal):return {"kind":"decimal","value":str(value)}
    if isinstance(value,(datetime,date,time)):return {"kind":type(value).__name__,"value":value.isoformat()}
    if isinstance(value,str):return {"kind":"str","value":value}
    raise ImportErrorSafe("Unsupported Excel cell type")

def _j(v):
    return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def import_snapshot(snapshot_dir,target_db):
    from openpyxl import load_workbook
    root=Path(snapshot_dir).resolve(strict=True)
    destination=Path(target_db).resolve()
    if destination.exists():raise FileExistsError("Do not overwrite a previous import")
    if destination.is_relative_to(root):raise ImportErrorSafe("Do not write into original immutable snapshot")
    checked=audit(root)
    if checked["checks"]["inExcelNotAPI"] or checked["checks"]["inAPINotExcel"] or checked["checks"]["duplicateIds"] or checked["checks"]["contractStatusMismatches"]:
        raise ImportErrorSafe("Active Excel/API IDs or status mismatch; import blocked")
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=destination.with_name(destination.name+".staging-"+uuid.uuid4().hex)
    original=root/"source"/"База_контрактов.xlsx"
    raw=root/"preserved_raw.sqlite"
    sheets=0;cells=0;formula_count=0
    try:
        with closing(sqlite3.connect(stage)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE provenance(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE raw_contracts(id TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
                CREATE TABLE raw_sections(name TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
                CREATE TABLE workbook_sheets(
                    sheet_index INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,max_row INTEGER NOT NULL,max_col INTEGER NOT NULL
                );
                CREATE TABLE workbook_cells(
                    sheet_index INTEGER NOT NULL REFERENCES workbook_sheets(sheet_index),
                    row_index INTEGER NOT NULL,col_index INTEGER NOT NULL,
                    coordinate TEXT NOT NULL,data_type TEXT NOT NULL,
                    formula INTEGER NOT NULL CHECK(formula IN(0,1)),
                    raw_json TEXT NOT NULL,cached_json TEXT NOT NULL,
                    style_id INTEGER NOT NULL,number_format TEXT NOT NULL,
                    PRIMARY KEY(sheet_index,row_index,col_index)
                );
                CREATE TABLE files(path TEXT PRIMARY KEY,size INTEGER NOT NULL,sha256 TEXT NOT NULL);
                """)
            with closing(sqlite3.connect(f"file:{raw.as_posix()}?mode=ro",uri=True)) as src:
                for cid,payload in src.execute("SELECT id,payload FROM raw_contracts"):
                    db.execute("INSERT INTO raw_contracts VALUES(?,?)",(cid,payload))
                for name,payload in src.execute("SELECT name,payload FROM raw_sections"):
                    db.execute("INSERT INTO raw_sections VALUES(?,?)",(name,payload))
            workbook=load_workbook(original,read_only=False,data_only=False,keep_links=False)
            cached=load_workbook(original,read_only=False,data_only=True,keep_links=False)
            try:
                if workbook.sheetnames!=cached.sheetnames:
                    raise ImportErrorSafe("Formula and value workbook sheet mismatch")
                for index,ws in enumerate(workbook.worksheets,1):
                    cs=cached.worksheets[index-1]
                    db.execute("INSERT INTO workbook_sheets VALUES(?,?,?,?,?)",
                               (index,ws.title,ws.sheet_state,ws.max_row,ws.max_column))
                    sheets+=1
                    for row in ws.iter_rows():
                        for cell in row:
                            if cell.value is None:continue
                            val=cs.cell(cell.row,cell.column)
                            formula=cell.data_type=="f"
                            db.execute("INSERT INTO workbook_cells VALUES(?,?,?,?,?,?,?,?,?,?)",
                                       (index,cell.row,cell.column,cell.coordinate,cell.data_type,
                                        int(formula),_j(encode_cell(cell.value)),
                                        _j(encode_cell(val.value)) if formula else _j(encode_cell(cell.value)),
                                        int(cell.style_id),cell.number_format))
                            cells+=1;formula_count+=formula
            finally:
                workbook.close();cached.close()
            manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
            for e in manifest["files"]:
                db.execute("INSERT INTO files VALUES(?,?,?)",(e["path"],e["size"],e["sha256"]))
            for key,value in {
                "sourceExcelSha256":digest(original),
                "sourceManifestSha256":digest(root/"manifest.json"),
                "sourceStatus":manifest["status"],
                "importVersion":"r2-raw-v1",
                "sourceFormulaCount":str(formula_count)
            }.items():
                db.execute("INSERT INTO provenance VALUES(?,?)",(key,value))
            db.execute("PRAGMA user_version=1")
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or db.execute("PRAGMA foreign_key_check").fetchall():
                raise ImportErrorSafe("Raw staging SQLite invalid")
            if db.execute("SELECT COUNT(*) FROM workbook_cells").fetchone()[0]!=cells:
                raise ImportErrorSafe("Staging cell count differs")
            if db.execute("SELECT COUNT(*) FROM raw_contracts").fetchone()[0]!=checked["checks"]["rawContractCount"]:
                raise ImportErrorSafe("Staging API contract count differs")
        # Always close before rename on Windows.
        if destination.exists():raise FileExistsError("Destination appeared during import")
        stage.rename(destination)
        return {"status":"RAW_STAGING_ONLY","sheetCount":sheets,"cells":cells,
                "formulaCells":formula_count,
                "contracts":checked["checks"]["rawContractCount"],
                "missingLinkedFolders":checked["checks"]["missingLinkedFolders"],
                "normalization":"NOT_IMPLEMENTED",
                "businessParity":"NOT_VERIFIED"}
    except BaseException:
        stage.unlink(missing_ok=True)
        raise

if __name__=="__main__":
    if len(sys.argv)!=3:
        raise SystemExit("Usage: python staging_import.py <private-snapshot> <new-staging-db>")
    print(json.dumps(import_snapshot(sys.argv[1],sys.argv[2]),ensure_ascii=False))
