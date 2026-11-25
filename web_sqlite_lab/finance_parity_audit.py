"""R3 private numerical parity: current Web JS vs Python, using preserved local API JSON.

Never reads the live workbook, publishes source data, or writes source files.
Writes ONLY aggregate PASS/FAIL fields; no customer names, contract IDs,
documents, or individual amounts. Requires Node and access to original logic.mjs.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import subprocess
import sys
from finance_engine import forecast

def compare(source_snapshot,original_js,output_file=None):
    root=Path(source_snapshot).resolve(strict=True)
    js=Path(original_js).resolve(strict=True)
    raw=json.loads((root/"source_api.json").read_text(encoding="utf-8"))
    records=raw["records"];finance=raw["finance"]
    if not isinstance(records,list) or not isinstance(finance,dict):raise ValueError("Malformed preservation snapshot")
    years=sorted({2026,2027,2028}|{int(r["year"]) for r in records if str(r.get("year","")).isdigit()}
                 |{int(x["date"][:4]) for x in finance.get("expenses",[]) if isinstance(x.get("date"),str) and x["date"][:4].isdigit()})
    cases=[{"year":year,"records":records,"finance":finance} for year in years if 2026<=year<=2100]
    oracle=Path(__file__).parent/"tests"/"finance_oracle.mjs"
    proc=subprocess.run(["node",str(oracle),str(js)],input=json.dumps(cases,ensure_ascii=True),
                        text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    if proc.returncode!=0:raise RuntimeError("Current Web JS financial oracle failed (details withheld)")
    expected=json.loads(proc.stdout)
    mismatches=[]
    metrics=("count","contract","paid","expected","cost","fee","logistics","other",
             "common","expenses","profitBefore","manualTax","autoTaxProfit","tax",
             "operatingExpenses","profitAfter")
    comparisons=0
    for case,original in zip(cases,expected):
        actual=forecast(case["records"],case["year"],case["finance"])
        for period in range(13):
            old=original["total"] if period==12 else original["months"][period]
            new=actual["total"] if period==12 else actual["months"][period]
            for key in metrics:
                comparisons+=1
                if old.get(key)!=new.get(key):
                    mismatches.append({"year":case["year"],
                                       "month":period+1 if period<12 else "TOTAL",
                                       "metric":key})
    report={"schema":"contracts-ip-finance-numeric-parity-v1",
            "checkedUtc":datetime.now(timezone.utc).isoformat(),
            "source":"immutable-private-API-snapshot",
            "records":len(records),"years":len(cases),
            "fieldComparisons":comparisons,"mismatchCount":len(mismatches),
            "result":"PASS" if not mismatches else "FAIL",
            "mismatches":mismatches,
            "limitations":["Source was a previous preserved snapshot, not live Excel",
                           "This checks Web/SQLite management forecasts, not tax-law correctness",
                           "Workbook formulas, hidden ledgers, actual documents and cash recognition not verified"]}
    if output_file:
        dest=Path(output_file)
        if dest.exists():raise FileExistsError("No report overwrite")
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report

if __name__=="__main__":
    if len(sys.argv) not in (3,4):
        raise SystemExit("Usage: python finance_parity_audit.py <private-snapshot> <source-logic.mjs> [new-report.json]")
    print(json.dumps(compare(sys.argv[1],sys.argv[2],sys.argv[3] if len(sys.argv)==4 else None),ensure_ascii=False))
