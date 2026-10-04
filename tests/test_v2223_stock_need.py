import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def header(no="1"):
    return dict(customer="T",contract_no=no,contract_date="2026-10-01",created_at="2026-10-01 10:00:00",
                contract_sum=1000,contract_status="Заключен",exec_status="В процессе",
                payment_status="Не оплачено",deadline="2026-10-20")

def item(qty,stock_qty,mode="Со склада"):
    return {"product":"X","qty":qty,"supply_mode":mode,"stock_qty":stock_qty,
            "procurement_reminder_days":30,"procurement_status":"Не начата"}

def row(c):
    return next(x for x in db.stock_summary(c) if x["product"]=="X")

def test_shortage_when_contract_reserve_exceeds_stock():
    c=fresh()
    db.insert_receipt(c,{"product":"X","qty":40,"unit_cost":1,"receipt_date":"2026-10-01"})
    db.insert_purchase(c,header(),[item(70,70,"Со склада")])
    r=row(c)
    assert r["on_hand"]==40
    assert r["auto_reserved"]==70
    assert r["future_demand"]==0
    assert r["need_to_buy"]==30
    c.close()

def test_planned_procurement_is_need_to_buy():
    c=fresh()
    db.insert_receipt(c,{"product":"X","qty":40,"unit_cost":1,"receipt_date":"2026-10-01"})
    db.insert_purchase(c,header(),[item(70,40,"Требуется закупка")])
    r=row(c)
    assert r["future_demand"]==30
    assert r["need_to_buy"]==30
    c.close()

def test_combined_shortage_and_planned_procurement():
    c=fresh()
    db.insert_receipt(c,{"product":"X","qty":40,"unit_cost":1,"receipt_date":"2026-10-01"})
    db.insert_purchase(c,header("A"),[item(50,50,"Со склада")])
    db.insert_purchase(c,header("B"),[item(20,0,"Требуется закупка")])
    r=row(c)
    assert r["auto_reserved"]==50
    assert r["future_demand"]==20
    assert r["need_to_buy"]==30
    c.close()

def test_manual_reservation_does_not_inflate_contract_purchase_need():
    c=fresh()
    db.insert_receipt(c,{"product":"X","qty":100,"unit_cost":1,"receipt_date":"2026-10-01"})
    db.insert_purchase(c,header(),[item(80,80,"Со склада")])
    db.insert_manual_reservation(c,{"product":"X","qty":50,"organization":"Потенциальный клиент","reserved_date":"2026-10-01","note":None})
    r=row(c)
    assert r["available"]==-30
    assert r["need_to_buy"]==0
    c.close()

def test_sent_contract_no_longer_needs_purchase():
    c=fresh()
    db.insert_receipt(c,{"product":"X","qty":40,"unit_cost":1,"receipt_date":"2026-10-01"})
    pid=db.insert_purchase(c,header(),[item(40,40,"Со склада")])
    h=db.fetch_by_id(c,pid); h["exec_status"]="Отправлено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    r=row(c)
    assert r["auto_reserved"]==0
    assert r["future_demand"]==0
    assert r["need_to_buy"]==0
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
