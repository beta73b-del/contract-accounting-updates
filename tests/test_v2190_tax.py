import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def h():
    return dict(customer="T",contract_no="1",contract_date="2026-10-01",created_at="2026-10-01 10:00:00",
                contract_sum=100000,purchase_cost=50000,logistics=5000,commission=3000,other_costs=2000,
                guarantee=0,contract_status="Заключен",exec_status="В процессе",payment_status="Не оплачено")

def it():
    return [{"product":"X","qty":1,"supply_mode":"Со склада","stock_qty":1,"procurement_reminder_days":30}]

def test_default_gross_7():
    c=fresh(); db.insert_purchase(c,h(),it())
    r=db.monthly_summary(c,2026,10)[0]
    assert r["tax_regime"]=="С доходов" and r["tax_rate"]==7.0 and r["tax"]==7000
    c.close()

def test_income_minus_expenses():
    c=fresh(); db.insert_purchase(c,h(),it())
    db.insert_monthly_expense(c,{"expense_date":"2026-10-15","category":"Прочее","amount":10000})
    db.set_tax_profile(c,"2026-10-01","Доходы минус расходы",15)
    r=db.monthly_summary(c,2026,10)[0]
    # base 100000 - 50000 - 5000 - 3000 - 2000 - 10000 = 30000
    assert r["tax_base"]==30000 and r["tax"]==4500
    assert r["profit"]==25500
    c.close()

def test_period_profile_does_not_rewrite_past():
    c=fresh()
    a=h(); a["created_at"]="2026-09-01 10:00:00"; a["contract_no"]="SEP"
    b=h(); b["created_at"]="2026-10-01 10:00:00"; b["contract_no"]="OCT"
    db.insert_purchase(c,a,it()); db.insert_purchase(c,b,it())
    db.set_tax_profile(c,"2026-10-01","Доходы минус расходы",15)
    sep=db.monthly_summary(c,2026,9)[0]
    octo=db.monthly_summary(c,2026,10)[0]
    assert sep["tax_regime"]=="С доходов" and sep["tax"]==7000
    assert octo["tax_regime"]=="Доходы минус расходы" and octo["tax"]==6000
    c.close()

def test_newer_profile_applies_forward():
    c=fresh()
    db.set_tax_profile(c,"2026-10-01","Доходы минус расходы",15)
    assert db.get_tax_profile(c,2026,11)["rate"]==15
    db.set_tax_profile(c,"2026-12-01","С доходов",6)
    assert db.get_tax_profile(c,2026,11)["rate"]==15
    assert db.get_tax_profile(c,2026,12)["rate"]==6
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for t in ts: t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
