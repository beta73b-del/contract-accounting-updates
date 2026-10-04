import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def header(no,status,handover=None,created="2026-09-10 10:00:00"):
    return dict(customer="T",contract_no=no,contract_date="2026-09-10",created_at=created,
                contract_sum=1000,contract_status="Заключен",exec_status=status,
                payment_status="Не оплачено",handover_date=handover,deadline="2026-10-01")

def item(q):
    return {"product":"X","qty":q,"supply_mode":"Со склада","stock_qty":q,
            "procurement_reminder_days":30,"procurement_status":"Не начата"}

def test_realized_counts_sent_and_later_only():
    c=fresh()
    db.insert_purchase(c,header("A","В процессе"),[item(10)])
    db.insert_purchase(c,header("B","Отправлено"),[item(20)])
    db.insert_purchase(c,header("C","Вручен",handover="2026-10-02"),[item(30)])
    db.insert_purchase(c,header("D","Исполнено",handover="2026-10-03"),[item(40)])
    r=db.monthly_summary(c,2026,9)[0]
    assert r["qty_total"]==90, r
    c.close()

def test_executed_without_handover_still_counts_if_old_data():
    c=fresh()
    pid=db.insert_purchase(c,header("E","В процессе"),[item(25)])
    h=db.fetch_by_id(c,pid)
    h["exec_status"]="Исполнено"
    # emulate old inconsistent record: final status but no handover date
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    r=db.monthly_summary(c,2026,9)[0]
    assert r["qty_total"]==25, r
    c.close()

def test_unsent_contract_not_realized():
    c=fresh()
    db.insert_purchase(c,header("A","В процессе"),[item(15)])
    r=db.monthly_summary(c,2026,9)[0]
    assert r["qty_total"]==0, r
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
