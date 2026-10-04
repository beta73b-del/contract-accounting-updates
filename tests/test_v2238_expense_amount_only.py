import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db, main

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def test_selected_month_expense_requires_only_amount():
    data=main.prepare_selected_month_expense("1 500,50",2026,9)
    assert data["expense_date"]=="2026-09-01"
    assert data["period_year"]==2026
    assert data["period_month"]==9
    assert data["category"]=="Прочее"
    assert data["amount"]==1500.50

def test_expense_recalculates_month_profit():
    c=fresh()
    h=dict(customer="T",contract_no="1",contract_date="2026-09-01",created_at="2026-09-01 10:00:00",
           contract_sum=10000,contract_status="Заключен",exec_status="В процессе",
           payment_status="Не оплачено",deadline="2026-09-30")
    db.insert_purchase(c,h,[])
    before=db.monthly_summary(c,2026,9)[0]
    data=main.prepare_selected_month_expense("2000",2026,9)
    db.insert_monthly_expense(c,data)
    after=db.monthly_summary(c,2026,9)[0]
    assert after["monthly_expenses"]==before["monthly_expenses"]+2000
    assert after["total_expenses"]==before["total_expenses"]+2000
    assert after["profit"]==before["profit"]-2000
    c.close()

def test_zero_rejected():
    try:
        main.prepare_selected_month_expense("0",2026,9)
    except ValueError:
        pass
    else:
        raise AssertionError("zero amount accepted")

if __name__=="__main__":
    for t in [test_selected_month_expense_requires_only_amount,test_expense_recalculates_month_profit,test_zero_rejected]:
        t(); print("PASS",t.__name__)
