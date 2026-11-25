"""R1: read-only audit of a PRIVATE preservation snapshot, never a live workbook.

No source data contents, names, IDs, customer names or document paths in reports.
Missing related folders and formula/value disagreements remain blocking warnings.
"""
from __future__ import annotations
from collections import Counter
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from datetime import datetime,timezone
import json
import sqlite3
import sys

def digest(path):
    h=sha256()
    with open(path,"rb") as fp:
        for part in iter(lambda:fp.read(1024*1024),b""):
            h.update(part)
    return h.hexdigest()

class AuditError(ValueError): pass

def audit(snapshot_dir, report_path=None):
    try:from openpyxl import load_workbook
    except ImportError:raise AuditError("Install openpyxl for the read-only Excel source audit") from None
    root=Path(snapshot_dir).resolve(strict=True)
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format")!="contracts-ip-preservation-v1":
        raise AuditError("Unexpected source snapshot format")
    declared=manifest.get("files")
    if not isinstance(declared,list) or not declared:raise AuditError("Empty manifest")
    seen=set();missing=[];changed=[]
    for item in declared:
        relative=item.get("path","")
        if not isinstance(relative,str) or not relative or relative.startswith("/") or "\\" in relative:
            raise AuditError("Invalid manifest path")
        path=(root/relative).resolve()
        if not path.is_relative_to(root) or relative in seen:
            raise AuditError("Duplicate/traversal in manifest")
        seen.add(relative)
        if not path.is_file():missing.append(relative);continue
        if path.stat().st_size!=item["size"] or digest(path)!=item["sha256"]:
            changed.append(relative)
    if missing or changed:raise AuditError("Snapshot missing/corrupted files; refused comparison")
    rawdb=root/"preserved_raw.sqlite"
    if digest(rawdb)!=next(item["sha256"] for item in declared if item["path"]=="preserved_raw.sqlite"):
        raise AuditError("Raw DB changed")
    with closing(sqlite3.connect(f"file:{rawdb.as_posix()}?mode=ro",uri=True)) as db:
        integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity!="ok":raise AuditError("Raw staging integrity error")
        rawrows=db.execute("SELECT id,payload FROM raw_contracts").fetchall()
        api_records=[json.loads(r[1]) for r in rawrows]
        api_ids=[str(r["id"]) for r in api_records]
        if len(api_ids)!=len(set(api_ids)):raise AuditError("Repeated source API IDs")
        sections={r[0] for r in db.execute("SELECT name FROM raw_sections")}
    xlsx=root/"source"/"База_контрактов.xlsx"
    wb=load_workbook(xlsx,read_only=False,data_only=False,keep_links=False)
    formula_cells=0; hidden_rows=0; nonempty_rows={}; sheet_info={}
    try:
        if "Контракты" not in wb.sheetnames:
            raise AuditError("Contracts sheet missing")
        source_ids=[];source_deleted=[];nonempty_sheet_rows=0; duplicate_ids=[]
        for ws in wb.worksheets:
            rows=0
            for row in ws.iter_rows():
                if any(cell.value is not None for cell in row):rows+=1
                formula_cells+=sum(cell.data_type=="f" for cell in row)
            hidden_rows+=sum(bool(x.hidden) for x in ws.row_dimensions.values())
            sheet_info[ws.title]={"nonemptyRows":rows,"state":ws.sheet_state}
        ws=wb["Контракты"]
        for i in range(2,ws.max_row+1):
            value=ws.cell(i,1).value
            if value is None or str(value).strip()=="":continue
            nonempty_sheet_rows+=1
            cid=str(value).strip()
            if cid in source_ids:duplicate_ids.append(cid)
            source_ids.append(cid)
            if str(ws.cell(i,16).value or "").strip()=="Удалён":
                source_deleted.append(cid)
        active_xlsx_ids=set(source_ids)-set(source_deleted)
        active_api_ids=set(api_ids)
        data_diff={"inExcelNotAPI":len(active_xlsx_ids-active_api_ids),
                   "inAPINotExcel":len(active_api_ids-active_xlsx_ids),
                   "duplicateIds":len(duplicate_ids),
                   "deletedRowsInExcel":len(source_deleted)}
        status_by_id={str(r["id"]):str(r.get("status","")) for r in api_records}
        status_mismatch=0
        for i in range(2,ws.max_row+1):
            cid=str(ws.cell(i,1).value or "").strip()
            if cid in status_by_id:
                if str(ws.cell(i,13).value or "").strip()!=status_by_id[cid]:
                    status_mismatch+=1
    finally:
        wb.close()
    missing_links=len(manifest.get("warnings",[]))
    warning_codes=Counter(x.get("error","UNKNOWN") for x in manifest.get("warnings",[]))
    checks={
        "manifestFiles":len(declared),
        "sha256Verified":len(declared),
        "rawSQLiteIntegrity":integrity,
        "rawContractCount":len(api_records),
        "rawSectionCount":len(sections),
        "workbookSheets":len(sheet_info),
        "workbookNonemptyContractRows":nonempty_sheet_rows,
        "workbookFormulaCells":formula_cells,
        "hiddenWorksheetRows":hidden_rows,
        "contractStatusMismatches":status_mismatch,
        "documentFilesCopied":manifest.get("documentsCopied"),
        "missingLinkedFolders":missing_links,
        **data_diff
    }
    source_match=not any((data_diff["inExcelNotAPI"],data_diff["inAPINotExcel"],
                          data_diff["duplicateIds"],status_mismatch))
    # This is not complete parity even when all compared active IDs match.
    report={
        "schema":"contracts-ip-r1-audit-v1",
        "auditUtc":datetime.now(timezone.utc).isoformat(),
        "snapshotStatus":manifest.get("status"),
        "result":"PARTIAL" if missing_links or not source_match else "SOURCE_IDS_MATCHED_ONLY",
        "checks":checks,
        "missingFoldersByCode":dict(warning_codes),
        "sheetStats":[{"sheetIndex":i+1,**stat} for i,stat in enumerate(sheet_info.values())],
        "unverified":[
          "Live Excel COM and copied workbook transaction-time consistency",
          "All financial formulas and cached calculation values",
          "Stock and finance journal equivalence",
          "Deleted/archive/hidden record interpretation",
          "Every document reference and business association",
          "Exact amounts, dates, item-by-item and monthly/yearly calculation parity",
          "All app buttons and macOS behavior"
        ],
    }
    if report_path:
        p=Path(report_path).resolve()
        if p.exists():raise FileExistsError("Audit reports are immutable; choose a new name")
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report

if __name__=="__main__":
    if len(sys.argv) not in (2,3):
        raise SystemExit("Usage: python source_audit.py <private-snapshot-dir> [new-report.json]")
    print(json.dumps(audit(sys.argv[1],sys.argv[2] if len(sys.argv)==3 else None),ensure_ascii=False))
