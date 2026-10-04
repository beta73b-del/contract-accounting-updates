import os,sys,tempfile,sqlite3
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db, reminder_worker

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def header(no="1", created="2026-10-01 10:00:00", contract_sum=100000):
    return dict(
        customer="T",contract_no=no,contract_date="2026-10-01",created_at=created,
        contract_sum=contract_sum,purchase_cost=50000,logistics=5000,commission=3000,
        other_costs=2000,guarantee=1000,contract_status="Заключен",
        exec_status="В процессе",payment_status="Не оплачено",
        deadline="2026-10-20",payment_deadline="2026-11-10",handover_date=None
    )

def item(q=10, stock=10, mode="Со склада"):
    return {"product":"X","qty":q,"supply_mode":mode,"stock_qty":stock,
            "procurement_reminder_days":30,"procurement_status":"Не начата"}

def test_delivered_is_not_executed_until_paid():
    c=fresh()
    pid=db.insert_purchase(c,header(),[item()])
    h=db.fetch_by_id(c,pid)
    h["exec_status"]="Вручен"; h["handover_date"]="2026-10-05"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    r=db.fetch_by_id(c,pid)
    assert r["exec_status"]=="Вручен"
    assert r["payment_status"]=="Не оплачено"

    h=db.fetch_by_id(c,pid); h["payment_status"]="Оплачено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    r=db.fetch_by_id(c,pid)
    assert r["exec_status"]=="Исполнено"
    c.close()

def test_cannot_force_executed_while_unpaid():
    c=fresh()
    pid=db.insert_purchase(c,header(),[item()])
    h=db.fetch_by_id(c,pid)
    h["handover_date"]="2026-10-05"; h["exec_status"]="Исполнено"; h["payment_status"]="Не оплачено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    r=db.fetch_by_id(c,pid)
    assert r["exec_status"]=="Вручен"
    c.close()

def test_paid_before_delivery_does_not_execute():
    c=fresh()
    pid=db.insert_purchase(c,header(),[item()])
    h=db.fetch_by_id(c,pid)
    h["payment_status"]="Оплачено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    r=db.fetch_by_id(c,pid)
    assert r["exec_status"]=="В процессе"
    c.close()

def test_dashboard_reserve_equals_stock_reserve():
    c=fresh()
    db.insert_receipt(c,{"product":"X","qty":100,"unit_cost":1,"receipt_date":"2026-10-01"})
    db.insert_purchase(c,header("A"),[item(q=30,stock=30)])
    db.insert_manual_reservation(c,{"product":"X","qty":12,"organization":"Lead","reserved_date":"2026-10-02","note":None})
    stock_total=sum(float(r["reserved"] or 0) for r in db.stock_summary(c))
    kpi=db.dashboard_kpis(c)
    assert stock_total==42
    assert kpi["reserve_qty"]==stock_total
    c.close()

def test_financial_end_to_end_normal_contract():
    c=fresh()
    db.insert_receipt(c,{"product":"X","qty":100,"unit_cost":1,"receipt_date":"2026-10-01"})
    pid=db.insert_purchase(c,header("FIN"),[item(q=20,stock=20)])
    # Before shipping: in reserve, not realized.
    s=db.stock_summary(c)[0]
    assert s["reserved"]==20 and s["on_hand"]==100 and s["available"]==80
    m=db.monthly_summary(c,2026,10)[0]
    assert m["qty_total"]==0

    # Ship: reserve removed, stock written off, realized appears.
    h=db.fetch_by_id(c,pid); h["exec_status"]="Отправлено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    s=db.stock_summary(c)[0]
    assert s["reserved"]==0 and s["on_hand"]==80 and s["available"]==80
    m=db.monthly_summary(c,2026,10)[0]
    assert m["qty_total"]==20

    # Deliver: payment attention starts, still not executed.
    h=db.fetch_by_id(c,pid); h["exec_status"]="Вручен"; h["handover_date"]="2026-10-10"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    r=db.fetch_by_id(c,pid)
    assert r["exec_status"]=="Вручен"
    att=reminder_worker.attention_items(c, today=__import__("datetime").date(2026,11,5))
    assert any(x["kind"]=="payment" and x["purchase_id"]==pid for x in att)

    # Extra monthly cost and 15% income-minus-expenses tax.
    db.insert_monthly_expense(c,{"expense_date":"2026-10-15","category":"Банк","amount":10000,"description":"Комиссия банка"})
    db.set_tax_profile(c,"2026-10-01","Доходы минус расходы",15)
    m=db.monthly_summary(c,2026,10)[0]
    # deductible=50k+5k+3k+2k+1k+10k=71k; base=29k; tax=4350
    assert m["tax_base"]==29000
    assert m["tax"]==4350
    assert m["total_expenses"]==75350
    assert m["profit"]==24650
    assert m["qty_total"]==20

    # Payment closes execution and removes payment attention.
    h=db.fetch_by_id(c,pid); h["payment_status"]="Оплачено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    r=db.fetch_by_id(c,pid)
    assert r["exec_status"]=="Исполнено"
    att=reminder_worker.attention_items(c, today=__import__("datetime").date(2026,11,5))
    assert not any(x["kind"]=="payment" and x["purchase_id"]==pid for x in att)
    c.close()

def test_legacy_unpaid_executed_migrates_back():
    p=os.path.join(tempfile.mkdtemp(),"old.db")
    c=db.get_connection(p)
    pid=db.insert_purchase(c,header("OLD"),[item()])
    c.execute("UPDATE purchases SET exec_status='Исполнено', payment_status='Не оплачено', handover_date='2026-10-05', stock_written_off=1 WHERE id=?",(pid,))
    c.commit(); c.close()
    c=db.get_connection(p)
    r=db.fetch_by_id(c,pid)
    assert r["exec_status"]=="Вручен"
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
