"""Append-only financial event ledger compatible with Web Finance-Model semantics.

Requires finance_events table (SQLite schema version 2). All operations are
inside a BEGIN IMMEDIATE transaction, with uniqueness and revision checks.
"""
from __future__ import annotations
import json
import re
from finance_engine import kopecks,validate_finance_expense_date,FinanceError

HEXID=re.compile(r"^[a-f0-9]{32}$")
def _id(value,label):
    if not isinstance(value,str) or not HEXID.fullmatch(value):
        raise FinanceError("Неверный "+label)
    return value

def normalize(body):
    if not isinstance(body,dict):raise FinanceError("Invalid finance payload")
    action=body.get("action")
    op=_id(body.get("opId"),"ID операции")
    if action=="rate":
        try:year=int(body.get("year",""))
        except (ValueError,TypeError):raise FinanceError("Incorrect rate year") from None
        rate=body.get("rate")
        previous=body.get("previous")
        try:rate=int(rate)
        except (TypeError,ValueError):raise FinanceError("Incorrect rate") from None
        if not 2027<=year<=2100 or rate not in (5,15) or (
            previous is not None and (type(previous)!=int or previous not in (5,15))):
            raise FinanceError("Выберите 5% или 15% для года 2027–2100")
        return {"action":"rate","opId":op,"year":year,"rate":rate,"previous":previous}
    if action not in ("create","edit","delete"):raise FinanceError("Unknown finance action")
    eid=_id(body.get("id"),"ID расхода")
    revision=body.get("revision",0)
    if type(revision)!=int or not (0<=revision<=999999999):
        raise FinanceError("Invalid expense revision")
    if action=="create" and revision!=0:raise FinanceError("Creation revision must be zero")
    if action!="create" and revision<1:raise FinanceError("Missing expense revision")
    if action=="delete":
        return {"action":action,"opId":op,"id":eid,"revision":revision}
    d=validate_finance_expense_date(body.get("date"))
    amount=kopecks(body.get("amount"))
    if not (0<amount<=90_000_000_000_000):
        raise FinanceError("Положительная сумма обязательна")
    comment=body.get("comment","")
    if not isinstance(comment,str) or len(comment)>500:
        raise FinanceError("Комментарий не более 500 символов")
    return {"action":action,"opId":op,"id":eid,"revision":revision,
            "date":d,"amount":f"{amount//100}.{amount%100:02d}","comment":comment}

def model(events):
    expenses={};rates={};ops={};created_ids=set()
    for item in events:
        e=normalize(item)
        op=e["opId"]
        if op in ops:raise FinanceError("Дублированная операция финансового журнала")
        ops[op]=e
        if e["action"]=="rate":
            key=str(e["year"])
            if rates.get(key)!=e["previous"]:
                raise FinanceError("Ставка уже изменена; обновите страницу")
            rates[key]=e["rate"];continue
        eid=e["id"]
        if e["action"]=="create":
            if eid in created_ids:raise FinanceError("Повторное создание расхода")
            created_ids.add(eid)
        else:
            if eid not in expenses or expenses[eid]["revision"]!=e["revision"]:
                raise FinanceError("Расход уже изменён или удалён; обновите страницу")
        if e["action"]=="delete":
            del expenses[eid]
            continue
        expenses[eid]={"id":eid,"revision":e["revision"]+1,"date":e["date"],
                       "amount":e["amount"],"comment":e["comment"]}
    return {"expenses":sorted(expenses.values(),key=lambda x:(x["date"],x["id"])),
            "rates":rates,"operations":ops}

class FinanceLedger:
    def __init__(self,store):
        self.store=store
        rows=store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finance_events'").fetchone()
        if not rows:raise FinanceError("Finance schema v2 is required")
    def events(self):
        return [json.loads(row[0]) for row in self.store.db.execute(
            "SELECT payload_json FROM finance_events ORDER BY sequence")]
    def payload(self):
        result=model(self.events())
        return {"expenses":result["expenses"],"rates":result["rates"]}
    def mutate(self,raw):
        e=normalize(raw)
        compact=json.dumps(e,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        with self.store.transaction():
            before=self.events()
            existing=model(before)
            old=existing["operations"].get(e["opId"])
            if old is not None:
                if old!=e:raise FinanceError("Операция с таким ID уже существует")
                return {"expenses":existing["expenses"],"rates":existing["rates"]}
            after=model(before+[e])
            self.store.db.execute(
                "INSERT INTO finance_events(op_id,payload_json) VALUES(?,?)",
                (e["opId"],compact))
            return {"expenses":after["expenses"],"rates":after["rates"]}
