r"""Differential test against current Web source app/logic.mjs, synthetic only.

To enable locally:
  set CONTRACTS_EXCEL_WEB_LOGIC=D:\...\Приложение\app\logic.mjs
  python -m unittest tests.test_finance_js_parity -v

Without that explicit variable: skip, never mistake skip for proof of parity.
"""
from pathlib import Path
import json
import os
import random
import shutil
import subprocess
import unittest

from finance_engine import forecast

class JsParity(unittest.TestCase):
    def test_synthetic_fuzz_matches_original_web_js(self):
        logic=os.environ.get("CONTRACTS_EXCEL_WEB_LOGIC")
        if not logic or not Path(logic).is_file():
            self.skipTest("Explicit original Web logic path not provided")
        if shutil.which("node") is None:
            self.skipTest("Node.js unavailable")
        rng=random.Random(20261125)
        cases=[]
        for i in range(180):
            year=rng.choice([2026,2027,2028,2030])
            rates={str(year):rng.choice([5,15])} if year>2026 and rng.random()<.6 else {}
            records=[]
            for _ in range(rng.randrange(0,15)):
                month=rng.randint(1,12)
                money=lambda: f"{rng.randint(0,200000)}.{rng.randint(0,99):02d}"
                records.append({
                    "id":f"fake-{i}-{_}","year":year,"month":month,
                    "status":rng.choice(["формирование","заключен","Вручен","Оплачен"]),
                    "amount":money(),"cost":money(),"fee":money(),
                    "logistics":money(),"other":money(),
                    "taxAmount":money() if rng.random()<.25 else "",
                    "check":rng.choice(["OK","OK","OK","ДОПОЛНИТЬ","Удалён"])
                })
            expenses=[{"id":f"artificial-{j}",
                       "date":f"{year}-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
                       "amount":f"{rng.randint(1,100000)}.{rng.randint(0,99):02d}"} 
                      for j in range(rng.randint(0,5))]
            cases.append({"records":records,"year":year,"finance":{"rates":rates,"expenses":expenses}})
        runner=Path(__file__).parent/"finance_oracle.mjs"
        node=subprocess.run(["node",str(runner),str(Path(logic).resolve())],
                            input=json.dumps(cases,ensure_ascii=False),text=True,
                            capture_output=True,timeout=30)
        self.assertEqual(node.returncode,0,"Original JS oracle failed (stderr omitted)")
        original=json.loads(node.stdout)
        self.assertEqual(len(original),len(cases))
        for index,case in enumerate(cases):
            candidate=forecast(case["records"],case["year"],case["finance"])
            if candidate != original[index]:
                print("PARITY_DIFF index=",index,
                      "statuses=",[(r["status"],r["check"],r["month"]) for r in case["records"]],
                      "candidate_counts=",[(m["count"],m["paid"],m["expected"]) for m in candidate["months"]],
                      "oracle_counts=",[(m["count"],m["paid"],m["expected"]) for m in original[index]["months"]])
            self.assertEqual(candidate,original[index],"Excel Web/SQLite forecast mismatch on synthetic case "+str(index))
if __name__=="__main__":
    unittest.main()
