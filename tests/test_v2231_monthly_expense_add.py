import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db, main

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def test_prepare_monthly_expense_valid():
    data,d = main.prepare_monthly_expense_input(
        "15.10.2026","12 345,67","Банк","Комиссия",
        expected_period=(2026,10),
    )
    assert data["expense_date"]=="2026-10-15"
    assert data["amount"]==12345.67
    assert data["category"]=="Банк"
    assert data["description"]=="Комиссия"
    assert (d.year,d.month,d.day)==(2026,10,15)

def test_prepare_monthly_expense_date_does_not_override_selected_period():
    data,d = main.prepare_monthly_expense_input(
        "01.11.2026","100","Прочее","",expected_period=(2026,10)
    )
    assert data["expense_date"]=="2026-11-01"
    assert data["period_year"]==2026
    assert data["period_month"]==10
    assert (d.year,d.month,d.day)==(2026,11,1)

def test_prepare_monthly_expense_zero_rejected():
    try:
        main.prepare_monthly_expense_input("01.10.2026","0","Прочее","",expected_period=(2026,10))
    except ValueError as e:
        assert "больше нуля" in str(e)
    else:
        raise AssertionError("zero amount was accepted")

def test_addition_persists_and_summary_updates():
    c=fresh()
    data,_=main.prepare_monthly_expense_input(
        "12.10.2026","2500","Банк","Обслуживание",
        expected_period=(2026,10),
    )
    eid=db.insert_monthly_expense(c,data)
    rows=list(db.fetch_monthly_expenses(c,2026,10))
    assert len(rows)==1 and rows[0]["id"]==eid
    assert float(rows[0]["amount"])==2500
    summary=db.monthly_summary(c,2026,10)
    assert len(summary)==1
    assert summary[0]["monthly_expenses"]==2500
    assert summary[0]["profit"]==-2500
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
