import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db, main

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def test_selected_period_controls_month_not_expense_date():
    c=fresh()
    data,_=main.prepare_monthly_expense_input(
        "15.11.2026","2500","Банк","Комиссия",
        expected_period=(2026,10),
    )
    eid=db.insert_monthly_expense(c,data)

    oct_rows=list(db.fetch_monthly_expenses(c,2026,10))
    nov_rows=list(db.fetch_monthly_expenses(c,2026,11))
    assert len(oct_rows)==1 and oct_rows[0]["id"]==eid
    assert len(nov_rows)==0
    assert oct_rows[0]["expense_date"]=="2026-11-15"
    assert int(oct_rows[0]["period_year"])==2026
    assert int(oct_rows[0]["period_month"])==10

    oct_summary=db.monthly_summary(c,2026,10)
    nov_summary=db.monthly_summary(c,2026,11)
    assert len(oct_summary)==1
    assert oct_summary[0]["monthly_expenses"]==2500
    assert oct_summary[0]["profit"]==-2500
    assert nov_summary==[]
    c.close()

def test_different_dates_same_selected_month_are_aggregated():
    c=fresh()
    for dt,amt in [("01.01.2025",100),("31.12.2027",200),("15.06.2026",300)]:
        data,_=main.prepare_monthly_expense_input(
            dt,str(amt),"Прочее","",
            expected_period=(2026,10),
        )
        db.insert_monthly_expense(c,data)
    rows=list(db.fetch_monthly_expenses(c,2026,10))
    assert len(rows)==3
    sm=db.monthly_summary(c,2026,10)
    assert len(sm)==1 and sm[0]["monthly_expenses"]==600
    c.close()

def test_old_expense_rows_migrate_period_from_date():
    p=os.path.join(tempfile.mkdtemp(),"old.db")
    c=db.get_connection(p)
    # Simulate an old row: no explicit period.
    c.execute(
        "INSERT INTO monthly_expenses(expense_date,period_year,period_month,category,amount,description) VALUES(?,?,?,?,?,?)",
        ("2026-09-12",None,None,"Прочее",777,"old"),
    )
    c.commit(); c.close()
    c=db.get_connection(p)
    row=c.execute("SELECT * FROM monthly_expenses WHERE amount=777").fetchone()
    assert int(row["period_year"])==2026
    assert int(row["period_month"])==9
    assert len(db.fetch_monthly_expenses(c,2026,9))==1
    c.close()

def test_amount_must_be_positive():
    try:
        main.prepare_monthly_expense_input(
            "01.10.2026","0","Прочее","",
            expected_period=(2026,10),
        )
    except ValueError as exc:
        assert "больше нуля" in str(exc)
    else:
        raise AssertionError("zero amount accepted")

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
