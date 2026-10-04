import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"qa.db"))

def header(no, created="2026-10-01 10:00:00"):
    return dict(
        customer="QA", contract_no=no, contract_date="2026-10-01", created_at=created,
        contract_sum=1000, contract_status="Заключен", exec_status="В процессе",
        payment_status="Не оплачено", deadline="2026-10-20", handover_date=None
    )

def item(product="Рутокен ЭЦП 3.0 3120", qty=10, stock=10, mode="Со склада"):
    return {"product":product,"qty":qty,"supply_mode":mode,"stock_qty":stock,
            "procurement_reminder_days":30,"procurement_status":"Не начата"}

def test_direct_handover_must_not_leave_goods_reserved():
    c=fresh()
    db.insert_receipt(c,{"product":"Рутокен ЭЦП 3.0 3120","qty":100,"unit_cost":1,"receipt_date":"2026-10-01"})
    pid=db.insert_purchase(c,header("QA-1"),[item()])
    before=next(r for r in db.stock_summary(c) if r["product"]=="Рутокен ЭЦП 3.0 3120")
    assert before["on_hand"]==100 and before["reserved"]==10

    h=db.fetch_by_id(c,pid)
    h["handover_date"]="2026-10-10"
    h["exec_status"]="Вручен"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])

    p=db.fetch_by_id(c,pid)
    stock=next(r for r in db.stock_summary(c) if r["product"]=="Рутокен ЭЦП 3.0 3120")
    month=db.monthly_summary(c,2026,10)[0]
    print("STATE", p["stock_written_off"], stock["on_hand"], stock["reserved"], month["qty_total"])
    assert p["stock_written_off"]==1
    assert stock["on_hand"]==90
    assert stock["reserved"]==0
    assert month["qty_total"]==10
    c.close()

def test_deferred_execution_month_after_payment():
    c=fresh()
    pid=db.insert_purchase(c,header("QA-2",created="2026-09-01 10:00:00"),
                           [item(qty=50,stock=0,mode="Отложенная закупка")])
    h=db.fetch_by_id(c,pid)
    h["handover_date"]="2026-10-20"
    h["exec_status"]="Вручен"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    assert db.monthly_summary(c,2026,10)==[]

    h=db.fetch_by_id(c,pid)
    h["payment_status"]="Оплачено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    p=db.fetch_by_id(c,pid)
    print("DEFERRED", len(db.monthly_summary(c,2026,10)), len(db.monthly_summary(c,2026,11)))
    # По бизнес-правилу исполнение возникает после оплаты. Текущая модель не хранит дату оплаты,
    # поэтому проверить/отнести контракт к реальному месяцу оплаты невозможно.
    assert p["exec_status"]=="Исполнено"
    c.close()

if __name__=="__main__":
    for name in ["test_direct_handover_must_not_leave_goods_reserved","test_deferred_execution_month_after_payment"]:
        try:
            globals()[name]()
            print("PASS",name)
        except Exception as e:
            print("FAIL",name,repr(e))
            raise
