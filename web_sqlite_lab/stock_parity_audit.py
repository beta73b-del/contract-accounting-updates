"""R3 read-only warehouse parity on a PRIVATE preservation snapshot.

Does not touch production Excel or publish product/customer data.
The stock.events returned by Web API are a projected journal; comparison
treats this as a snapshot reference, not proof of historical completeness.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
import json
import sys
from stock_projection import project,StockError

FIELDS=("quantity","cents","reserved","available","shortage","deleted")

def audit(snapshot_dir,report_file=None):
    root=Path(snapshot_dir).resolve(strict=True)
    api=json.loads((root/"source_api.json").read_text(encoding="utf-8"))
    stock=api.get("stock")
    if not isinstance(stock,dict):raise ValueError("Source missing stock")
    events=stock.get("events")
    items=stock.get("items")
    phases=stock.get("phases")
    if not isinstance(events,list) or not isinstance(items,list) or not isinstance(phases,dict):
        raise ValueError("Incomplete source stock payload")
    actual=project(events)
    reference={r["product"]:r for r in items}
    changed_fields={}
    comparisons=0
    missing=0
    for product,r in reference.items():
        projection=actual["inventory"].get(product)
        if projection is None:
            missing+=1
            continue
        for key in FIELDS:
            comparisons+=1
            if r.get(key)!=projection.get(key):
                changed_fields[key]=changed_fields.get(key,0)+1
    unexpected=len(set(actual["inventory"])-set(reference))
    phase_mismatch=0
    for contract_id,phase in phases.items():
        x=actual["contracts"].get(contract_id)
        if x is None or x["phase"]!=phase:phase_mismatch+=1
    result="PASS" if not (changed_fields or missing or unexpected or phase_mismatch) else "FAIL"
    report={
        "format":"contracts-ip-stock-parity-v1",
        "checkedUtc":datetime.now(timezone.utc).isoformat(),
        "source":"private-previous-API-snapshot",
        "eventCount":len(events),"referenceProducts":len(reference),
        "projectedProducts":len(actual["inventory"]),
        "fieldComparisons":comparisons,
        "fieldMismatchCounts":changed_fields,
        "missingReferenceProducts":missing,
        "unexpectedProjectionProducts":unexpected,
        "referencePhases":len(phases),"phaseMismatches":phase_mismatch,
        "result":result,
        "limitations":[
            "Does not prove equivalence to the source Excel workbook raw history",
            "Current Web JSON stock.events may be a projected journal",
            "Corrections, return costs and migration into active SQLite tables need separate validation",
            "Original live Excel must not be replaced"
        ],
    }
    if report_file:
        path=Path(report_file).resolve()
        if path.exists():raise FileExistsError("Report path already exists")
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report

if __name__=="__main__":
    if len(sys.argv) not in (2,3):
        raise SystemExit("Usage: python stock_parity_audit.py <private-snapshot> [new-report.json]")
    print(json.dumps(audit(sys.argv[1],sys.argv[2] if len(sys.argv)==3 else None),
                     ensure_ascii=False))
