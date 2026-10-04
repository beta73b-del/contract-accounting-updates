import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

PRODUCT="Рутокен Lite 1010"

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def header(no, created, amount, done=False):
    h=dict(
        customer="QA",contract_no=no,contract_date=created[:10],created_at=created,
        contract_sum=amount,contract_status="Заключен",
        exec_status="В процессе",payment_status="Не оплачено",
        deadline=created[:10]
    )
    if done:
        h["handover_date"]=created[:10]
        h["payment_status"]="Оплачено"
        h["payment_date"]=created[:10]
        h["exec_status"]="Исполнено"
    return h

def item(q=1):
    return {"product":PRODUCT,"qty":q,"supply_mode":"Со склада","stock_qty":q,
            "procurement_reminder_days":30,"procurement_status":"Не начата"}

def test_dashboard_kpis_follow_selected_month():
    c=fresh()
    db.insert_receipt(c,{"product":PRODUCT,"qty":100,"unit_cost":1,"receipt_date":"2026-01-01"})

    # January: 3 contracts, 2 still in work, 1 completed.
    db.insert_purchase(c,header("JAN-1","2026-01-05 10:00:00",1000),[item(2)])
    db.insert_purchase(c,header("JAN-2","2026-01-10 10:00:00",2000),[item(3)])
    db.insert_purchase(c,header("JAN-3","2026-01-15 10:00:00",3000,done=True),[item(4)])

    # February: 2 contracts, only 1 in work.
    db.insert_purchase(c,header("FEB-1","2026-02-05 10:00:00",4000),[item(5)])
    db.insert_purchase(c,header("FEB-2","2026-02-10 10:00:00",5000,done=True),[item(6)])

    jan=db.dashboard_kpis(c,year=2026,month=1)
    feb=db.dashboard_kpis(c,year=2026,month=2)

    assert jan["total_count"]==3, jan
    assert jan["work_count"]==2, jan
    assert jan["total_sum"]==6000, jan
    assert jan["awaiting"]==3000, jan

    assert feb["total_count"]==2, feb
    assert feb["work_count"]==1, feb
    assert feb["total_sum"]==9000, feb
    assert feb["awaiting"]==4000, feb

    # Reserve is current warehouse reserve and therefore identical for both period views.
    assert jan["reserve_qty"]==feb["reserve_qty"]
    c.close()

def test_dashboard_year_and_all_filters():
    c=fresh()
    db.insert_purchase(c,header("A","2025-12-01 10:00:00",1000),[item()])
    db.insert_purchase(c,header("B","2026-01-01 10:00:00",2000),[item()])
    db.insert_purchase(c,header("C","2026-02-01 10:00:00",3000),[item()])

    y2026=db.dashboard_kpis(c,year=2026,month=None)
    all_rows=db.dashboard_kpis(c,year=None,month=None)

    assert y2026["total_count"]==2 and y2026["total_sum"]==5000
    assert all_rows["total_count"]==3 and all_rows["total_sum"]==6000
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
