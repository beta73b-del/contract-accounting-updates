import os,sys,tempfile,sqlite3
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def header():
    return dict(customer="Заказчик",contract_no="H-1",contract_date="2026-10-01",
                created_at="2026-10-01 10:00:00",contract_sum=1000,
                contract_status="Заключен",exec_status="В процессе",
                payment_status="Не оплачено",deadline="2026-10-20",
                payment_deadline="2026-11-01")

def item(stock=5,status="Не начата",mode="Со склада",qty=5):
    return {"product":"X","qty":qty,"supply_mode":mode,"stock_qty":stock,
            "procurement_reminder_days":30,"procurement_status":status}

def actions(c,pid):
    return [r["action"] for r in db.fetch_audit(c,pid)]

def stock_actions(c,product="X"):
    return [r["action"] for r in db.fetch_stock_audit(c,product)]

def test_create_records_reserve_history():
    c=fresh()
    pid=db.insert_purchase(c,header(),[item()])
    assert "Создан контракт" in actions(c,pid)
    assert "Резерв склада" in actions(c,pid)
    assert "Резерв под контракт" in stock_actions(c)
    c.close()

def test_writeoff_logged_once_even_status_toggled():
    c=fresh(); db.insert_receipt(c,{"product":"X","qty":20,"unit_cost":1,"receipt_date":"2026-10-01"})
    pid=db.insert_purchase(c,header(),[item()])
    h=db.fetch_by_id(c,pid); h["exec_status"]="Отправлено"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    h=db.fetch_by_id(c,pid); h["exec_status"]="В процессе"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    h=db.fetch_by_id(c,pid); h["exec_status"]="Отправлено"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    assert actions(c,pid).count("Списание со склада")==1
    assert stock_actions(c).count("Списание по контракту")==1
    c.close()

def test_delivery_execution_payment_history():
    c=fresh()
    pid=db.insert_purchase(c,header(),[item()])
    h=db.fetch_by_id(c,pid); h["exec_status"]="Вручен"; h["handover_date"]="2026-10-05"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    h=db.fetch_by_id(c,pid); h["exec_status"]="Исполнено"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    h=db.fetch_by_id(c,pid); h["payment_status"]="Оплачено"; db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    a=actions(c,pid)
    assert "Товар вручен" in a
    assert "Контракт исполнен" in a
    assert "Оплата получена" in a
    c.close()

def test_procurement_order_history():
    c=fresh()
    pid=db.insert_purchase(c,header(),[item(stock=0,status="Не начата",mode="Требуется закупка",qty=10)])
    h=db.fetch_by_id(c,pid)
    arr=[dict(x) for x in h["items"]]
    arr[0]["procurement_status"]="Заказано"
    db.update_purchase(c,pid,h,arr)
    assert "Закупка заказана" in actions(c,pid)
    assert "Ожидается поступление" in stock_actions(c)
    c.close()

def test_receipt_history_survives_delete():
    c=fresh()
    rid=db.insert_receipt(c,{"product":"X","qty":10,"unit_cost":5,"receipt_date":"2026-10-01","supplier":"Поставщик"})
    db.update_receipt(c,rid,{"product":"X","qty":12,"unit_cost":5,"receipt_date":"2026-10-01","supplier":"Поставщик","note":None})
    db.delete_receipt(c,rid)
    a=stock_actions(c)
    assert "Приход товара" in a and "Изменён приход" in a and "Удалён приход" in a
    assert db.fetch_receipt_by_id(c,rid) is None
    c.close()

def test_manual_reservation_history_survives_delete():
    c=fresh()
    rid=db.insert_manual_reservation(c,{"product":"X","qty":3,"organization":"Клиент","reserved_date":"2026-10-01","note":None})
    db.update_manual_reservation(c,rid,{"product":"X","qty":4,"organization":"Клиент","reserved_date":"2026-10-01","note":None})
    db.delete_manual_reservation(c,rid)
    a=stock_actions(c)
    assert "Ручной резерв создан" in a and "Ручной резерв изменён" in a and "Ручной резерв удалён" in a
    assert db.fetch_manual_reservation_by_id(c,rid) is None
    c.close()

def test_stock_audit_table_created_for_old_db():
    p=os.path.join(tempfile.mkdtemp(),"old.db")
    c=sqlite3.connect(p)
    c.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT)")
    c.commit(); c.close()
    c=db.get_connection(p)
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "stock_audit" in tables
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts: t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
