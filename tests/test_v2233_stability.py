import os,sys,tempfile
from datetime import date
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

PRODUCT="Рутокен ЭЦП 3.0 3120"

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def header(no="1", created="2026-10-01 10:00:00"):
    return dict(
        customer="QA",contract_no=no,contract_date="2026-10-01",created_at=created,
        contract_sum=1000,contract_status="Заключен",exec_status="В процессе",
        payment_status="Не оплачено",deadline="2026-10-20",handover_date=None
    )

def item(qty=10,stock=10,mode="Со склада"):
    return {"product":PRODUCT,"qty":qty,"supply_mode":mode,"stock_qty":stock,
            "procurement_reminder_days":30,"procurement_status":"Не начата"}

def stock_row(c):
    return next(r for r in db.stock_summary(c) if r["product"]==PRODUCT)

def test_direct_handover_writes_off_once_and_clears_reserve():
    c=fresh()
    db.insert_receipt(c,{"product":PRODUCT,"qty":100,"unit_cost":1,"receipt_date":"2026-10-01"})
    pid=db.insert_purchase(c,header("A"),[item()])
    before=stock_row(c)
    assert before["on_hand"]==100 and before["reserved"]==10

    h=db.fetch_by_id(c,pid)
    h["handover_date"]="2026-10-10"
    h["exec_status"]="Вручен"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])

    p=db.fetch_by_id(c,pid)
    after=stock_row(c)
    assert p["stock_written_off"]==1
    assert after["on_hand"]==90
    assert after["reserved"]==0

    # Повторное сохранение не списывает повторно.
    h=db.fetch_by_id(c,pid)
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    again=stock_row(c)
    assert again["on_hand"]==90 and again["reserved"]==0
    c.close()

def test_paid_without_payment_date_gets_today():
    c=fresh()
    pid=db.insert_purchase(c,header("B"),[item()])
    h=db.fetch_by_id(c,pid)
    h["handover_date"]="2026-10-03"
    h["exec_status"]="Вручен"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])

    h=db.fetch_by_id(c,pid)
    h["payment_status"]="Оплачено"
    h["payment_date"]=None
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    p=db.fetch_by_id(c,pid)
    assert p["payment_date"]==date.today().isoformat()
    assert p["exec_status"]=="Исполнено"
    c.close()

def test_unpaid_clears_payment_date_and_downgrades_execution():
    c=fresh()
    pid=db.insert_purchase(c,header("C"),[item()])
    h=db.fetch_by_id(c,pid)
    h["handover_date"]="2026-10-03"
    h["payment_status"]="Оплачено"
    h["payment_date"]="2026-10-05"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    assert db.fetch_by_id(c,pid)["exec_status"]=="Исполнено"

    h=db.fetch_by_id(c,pid)
    h["payment_status"]="Не оплачено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    p=db.fetch_by_id(c,pid)
    assert p["payment_date"] is None
    assert p["exec_status"]=="Вручен"
    c.close()

def test_deferred_contract_moves_to_payment_month():
    c=fresh()
    pid=db.insert_purchase(
        c,header("D",created="2026-09-01 10:00:00"),
        [item(qty=50,stock=0,mode="Отложенная закупка")]
    )
    h=db.fetch_by_id(c,pid)
    h["handover_date"]="2026-10-20"
    h["exec_status"]="Вручен"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    assert db.monthly_summary(c,2026,9)==[]
    assert db.monthly_summary(c,2026,10)==[]

    h=db.fetch_by_id(c,pid)
    h["payment_status"]="Оплачено"
    h["payment_date"]="2026-11-05"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])

    assert db.monthly_summary(c,2026,10)==[]
    nov=db.monthly_summary(c,2026,11)
    assert len(nov)==1
    assert nov[0]["contract_sum"]==1000
    assert nov[0]["product_quantities"]=={PRODUCT:50.0}
    rows=db.summary_contracts(c,2026,11)
    assert len(rows)==1 and rows[0]["id"]==pid
    assert rows[0]["summary_period"]=="2026-11-05"
    c.close()

def test_legacy_paid_record_falls_back_to_handover_date():
    c=fresh()
    pid=db.insert_purchase(c,header("E"),[item()])
    c.execute(
        "UPDATE purchases SET payment_status='Оплачено', payment_date=NULL, handover_date='2026-10-07', exec_status='Исполнено' WHERE id=?",
        (pid,)
    )
    c.commit()
    # Reopen triggers migration fallback.
    path=c.execute("PRAGMA database_list").fetchone()[2]
    c.close()
    c=db.get_connection(path)
    p=db.fetch_by_id(c,pid)
    assert p["payment_date"]=="2026-10-07"
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
