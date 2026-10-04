import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def test_expense_amount_persists_and_filters_by_month():
    c=fresh()
    a=db.insert_monthly_expense(c,{"expense_date":"2026-10-05","category":"Банк","amount":12345.67,"description":"Комиссия"})
    b=db.insert_monthly_expense(c,{"expense_date":"2026-11-01","category":"Прочее","amount":222,"description":"Ноябрь"})
    rows=list(db.fetch_monthly_expenses(c,2026,10))
    assert len(rows)==1
    assert rows[0]["id"]==a
    assert float(rows[0]["amount"])==12345.67
    assert rows[0]["description"]=="Комиссия"
    assert all(r["id"]!=b for r in rows)
    c.close()

def test_monthly_summary_shows_expense_amount_without_contracts():
    c=fresh()
    db.insert_monthly_expense(c,{"expense_date":"2026-10-05","category":"Банк","amount":1500,"description":"Обслуживание"})
    rows=db.monthly_summary(c,2026,10)
    assert len(rows)==1
    r=rows[0]
    assert r["monthly_expenses"]==1500
    assert r["total_expenses"]==1500
    assert r["profit"]==-1500
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
