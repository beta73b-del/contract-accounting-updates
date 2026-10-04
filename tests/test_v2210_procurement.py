import os,sys,tempfile,sqlite3
from datetime import date
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db, reminder_worker

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def h():
    return dict(customer="T",contract_no="1",contract_date="2026-10-01",created_at="2026-10-01 10:00:00",
                contract_sum=1000,contract_status="Заключен",exec_status="В процессе",
                payment_status="Не оплачено",deadline="2026-10-20")

def item(status="Не начата",mode="Требуется закупка",stock=0,qty=10):
    return {"product":"X","qty":qty,"supply_mode":mode,"stock_qty":stock,
            "procurement_reminder_days":30,"procurement_status":status}

def test_ordered_state_persists():
    c=fresh()
    pid=db.insert_purchase(c,h(),[item(status="Заказано")])
    it=db.fetch_items(c,pid)[0]
    assert it["procurement_status"]=="Заказано"
    c.close()

def test_ordered_hides_procurement_attention():
    c=fresh(); today=date(2026,10,4)
    pid=db.insert_purchase(c,h(),[item(status="Не начата")])
    items=reminder_worker.attention_items(c,today=today)
    assert any(x["kind"]=="procurement" and x["purchase_id"]==pid for x in items)
    hd=db.fetch_by_id(c,pid)
    arr=[dict(x) for x in hd["items"]]; arr[0]["procurement_status"]="Заказано"
    db.update_purchase(c,pid,hd,arr)
    items=reminder_worker.attention_items(c,today=today)
    assert not any(x["kind"]=="procurement" and x["purchase_id"]==pid for x in items)
    c.close()

def test_ordered_does_not_change_stock_or_future_demand():
    c=fresh()
    db.insert_receipt(c,{"product":"X","qty":100,"unit_cost":1,"receipt_date":"2026-10-01"})
    pid=db.insert_purchase(c,h(),[item(status="Не начата",stock=20,qty=50)])
    before=next(x for x in db.stock_summary(c) if x["product"]=="X")
    hd=db.fetch_by_id(c,pid); arr=[dict(x) for x in hd["items"]]; arr[0]["procurement_status"]="Заказано"
    db.update_purchase(c,pid,hd,arr)
    after=next(x for x in db.stock_summary(c) if x["product"]=="X")
    assert before["on_hand"]==after["on_hand"]==100
    assert before["reserved"]==after["reserved"]==20
    assert before["future_demand"]==after["future_demand"]==30
    c.close()

def test_stock_mode_forces_not_started():
    c=fresh()
    pid=db.insert_purchase(c,h(),[item(status="Заказано",mode="Со склада",stock=10,qty=10)])
    it=db.fetch_items(c,pid)[0]
    assert it["procurement_status"]=="Не начата"
    c.close()

def test_old_schema_migrates_procurement_status():
    p=os.path.join(tempfile.mkdtemp(),"old.db")
    c=sqlite3.connect(p)
    c.execute("CREATE TABLE purchase_items (id INTEGER PRIMARY KEY AUTOINCREMENT,purchase_id INTEGER NOT NULL,product TEXT,qty REAL,supply_mode TEXT NOT NULL DEFAULT 'Со склада',stock_qty REAL NOT NULL DEFAULT 0,procurement_reminder_days INTEGER NOT NULL DEFAULT 30)")
    c.execute("INSERT INTO purchase_items(purchase_id,product,qty,supply_mode,stock_qty) VALUES(1,'X',10,'Требуется закупка',0)")
    c.commit(); c.close()
    c=db.get_connection(p)
    cols={r[1] for r in c.execute("PRAGMA table_info(purchase_items)").fetchall()}
    assert "procurement_status" in cols
    row=c.execute("SELECT procurement_status FROM purchase_items LIMIT 1").fetchone()
    assert row["procurement_status"]=="Не начата"
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts: t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
