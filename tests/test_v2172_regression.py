import os, sys, tempfile, sqlite3
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db, reminder_worker

def newdb():
    p=os.path.join(tempfile.mkdtemp(),"t.db")
    return db.get_connection(p)

def header(**kw):
    h=dict(
        customer="Тест", contract_no="T-1", contract_date="2026-10-01",
        contract_sum=1000, contract_status="Заключен", exec_status="В процессе",
        payment_status="Не оплачено", deadline="2026-10-20",
        payment_deadline="2026-10-08", handover_date=None, sign_deadline=None,
    )
    h.update(kw); return h

def item(qty=100, mode="Со склада", stock=100, days=30, product="Товар"):
    return {"product":product,"qty":qty,"supply_mode":mode,"stock_qty":stock,"procurement_reminder_days":days}

def row(summary, product="Товар"):
    return next(x for x in summary if x["product"]==product)

def test_status_chain_and_idempotent_writeoff():
    c=newdb(); db.insert_receipt(c,{"product":"Товар","qty":100,"unit_cost":10,"receipt_date":"2026-10-01"})
    pid=db.insert_purchase(c,header(deadline="2026-12-01"),[item(qty=30,stock=30)])
    r=row(db.stock_summary(c)); assert r["on_hand"]==100 and r["reserved"]==30 and r["available"]==70
    h=db.fetch_by_id(c,pid); h["exec_status"]="Отправлено"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    r=row(db.stock_summary(c)); assert r["on_hand"]==70 and r["reserved"]==0 and r["available"]==70
    h=db.fetch_by_id(c,pid); h["exec_status"]="В процессе"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    h=db.fetch_by_id(c,pid); h["exec_status"]="Отправлено"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    r=row(db.stock_summary(c)); assert r["on_hand"]==70, r
    h=db.fetch_by_id(c,pid); h["exec_status"]="Вручен"; h["handover_date"]="2026-10-03"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    assert db.fetch_by_id(c,pid)["stock_written_off"]==1
    h=db.fetch_by_id(c,pid); h["exec_status"]="Исполнено"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    assert db.fetch_by_id(c,pid)["stock_written_off"]==1
    c.close()

def test_partial_stock_and_future_demand():
    c=newdb(); db.insert_receipt(c,{"product":"Товар","qty":500,"unit_cost":1,"receipt_date":"2026-10-01"})
    db.insert_purchase(c,header(contract_no="P"),[item(qty=500,mode="Отложенная закупка",stock=150,days=30)])
    r=row(db.stock_summary(c)); assert r["reserved"]==150 and r["future_demand"]==350 and r["available"]==350, r
    c.close()

def test_procurement_attention_modes():
    c=newdb(); today=date(2026,10,4)
    p1=db.insert_purchase(c,header(contract_no="NOW",deadline="2026-12-31"),[item(qty=10,mode="Требуется закупка",stock=0,days=30,product="A")])
    p2=db.insert_purchase(c,header(contract_no="LATER",deadline="2026-12-31"),[item(qty=10,mode="Отложенная закупка",stock=0,days=30,product="B")])
    p3=db.insert_purchase(c,header(contract_no="DUE",deadline="2026-10-20"),[item(qty=10,mode="Отложенная закупка",stock=2,days=30,product="C")])
    items=reminder_worker.attention_items(c,today=today)
    proc=[x for x in items if x["kind"]=="procurement"]
    ids={x["purchase_id"] for x in proc}
    assert p1 in ids and p3 in ids and p2 not in ids, proc
    due=next(x for x in proc if x["purchase_id"]==p3); assert due["need_qty"]==8
    c.close()

def test_payment_only_after_handover_and_execution_stops_when_delivered():
    c=newdb(); today=date(2026,10,4)
    pid=db.insert_purchase(c,header(contract_no="PAY",deadline="2026-10-06",payment_deadline="2026-10-08"),[])
    items=reminder_worker.attention_items(c,today=today)
    assert any(x["kind"]=="execution" and x["purchase_id"]==pid for x in items)
    assert not any(x["kind"]=="payment" and x["purchase_id"]==pid for x in items)
    h=db.fetch_by_id(c,pid); h["exec_status"]="Вручен"; h["handover_date"]="2026-10-03"; db.update_purchase(c,pid,h,[])
    items=reminder_worker.attention_items(c,today=today)
    assert not any(x["kind"]=="execution" and x["purchase_id"]==pid for x in items)
    assert any(x["kind"]=="payment" and x["purchase_id"]==pid for x in items)
    h=db.fetch_by_id(c,pid); h["payment_status"]="Оплачено"; db.update_purchase(c,pid,h,[])
    items=reminder_worker.attention_items(c,today=today)
    assert not any(x["kind"]=="payment" and x["purchase_id"]==pid for x in items)
    c.close()

def test_old_status_migration():
    p=os.path.join(tempfile.mkdtemp(),"old.db"); c=db.get_connection(p)
    a=db.insert_purchase(c,header(contract_no="A"),[item(qty=5,stock=5)])
    b=db.insert_purchase(c,header(contract_no="B"),[item(qty=7,stock=7)])
    c.execute("UPDATE purchases SET exec_status='Приемка Заказчиком', stock_written_off=0 WHERE id=?",(a,))
    c.execute("UPDATE purchases SET exec_status='Подписан в ЕИС', stock_written_off=0 WHERE id=?",(b,)); c.commit(); c.close()
    c=db.get_connection(p)
    ra=db.fetch_by_id(c,a); rb=db.fetch_by_id(c,b)
    assert ra["exec_status"]=="Вручен" and ra["stock_written_off"]==1
    # v2.23: без оплаты исторический финальный статус больше не считается исполнением.
    assert rb["exec_status"]=="Отправлено" and rb["stock_written_off"]==1
    c.close()

def test_deadline_rows_delivered_unpaid():
    c=newdb()
    pid=db.insert_purchase(c,header(exec_status="Вручен",handover_date="2026-10-03",deadline="2026-10-05",payment_deadline="2026-10-08"),[])
    rows=db.deadline_rows(c)
    r=next(x for x in rows if x["id"]==pid)
    assert r["exec_status"]=="Вручен" and r["payment_status"]=="Не оплачено"
    c.close()

def test_stock_movement_realization_once():
    c=newdb(); db.insert_receipt(c,{"product":"Товар","qty":50,"unit_cost":2,"receipt_date":"2026-10-01"})
    pid=db.insert_purchase(c,header(),[item(qty=20,stock=20)])
    h=db.fetch_by_id(c,pid); h["exec_status"]="Отправлено"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    events=db.stock_product_movement(c,"Товар")
    rel=[e for e in events if e["type"]=="Реализация"]
    assert len(rel)==1 and rel[0]["qty"]==-20, rel
    c.close()

def test_attention_counts_sections():
    items=[
      {"kind":"signing","severity":"critical"},
      {"kind":"execution","severity":"overdue"},
      {"kind":"procurement","severity":"warning"},
      {"kind":"payment","severity":"critical"},
      {"kind":"stock","severity":"warning"},
    ]
    x=reminder_worker.attention_counts(items)
    assert x=={"total":5,"overdue":1,"signing":1,"execution":1,"procurement":1,"payment":1,"stock":1}

if __name__=="__main__":
    tests=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t(); print("PASS",t.__name__)
    print("ALL",len(tests),"TESTS PASSED")
