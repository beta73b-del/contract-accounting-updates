import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db

def newdb():
    return db.get_connection(os.path.join(tempfile.mkdtemp(), "finance.db"))

def header(**kw):
    h = dict(
        customer="Тест", contract_no="T-1", contract_date="2026-09-15",
        created_at="2026-09-15 10:00:00", contract_sum=100000,
        purchase_cost=50000, logistics=5000, commission=3000,
        other_costs=2000, guarantee=1000,
        contract_status="Заключен", exec_status="В процессе",
        payment_status="Не оплачено", deadline="2026-10-20",
        handover_date=None, payment_deadline="2026-11-01",
    )
    h.update(kw)
    return h

def item(mode="Со склада", qty=10, stock=10):
    return {
        "product":"Товар", "qty":qty, "supply_mode":mode,
        "stock_qty":stock, "procurement_reminder_days":30,
    }

def test_regular_contract_stays_in_creation_month():
    c=newdb()
    pid=db.insert_purchase(c, header(contract_no="REG"), [item()])
    h=db.fetch_by_id(c,pid)
    h["exec_status"]="Вручен"
    h["handover_date"]="2026-10-10"
    h["payment_status"]="Оплачено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    sep=db.monthly_summary(c,year=2026,month=9)
    octo=db.monthly_summary(c,year=2026,month=10)
    assert len(sep)==1 and sep[0]["contract_sum"]==100000
    assert not octo
    c.close()

def test_deferred_excluded_until_executed():
    c=newdb()
    pid=db.insert_purchase(c, header(contract_no="DEF"), [item(mode="Отложенная закупка",stock=0)])
    assert db.monthly_summary(c,year=2026,month=9)==[]
    assert db.monthly_summary(c,year=2026,month=10)==[]
    h=db.fetch_by_id(c,pid)
    h["exec_status"]="Вручен"
    h["handover_date"]="2026-10-10"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    assert db.monthly_summary(c,year=2026,month=10)==[]
    c.close()

def test_deferred_moves_whole_contract_to_execution_month():
    c=newdb()
    pid=db.insert_purchase(c, header(contract_no="DEF2"), [item(mode="Отложенная закупка",stock=0)])
    h=db.fetch_by_id(c,pid)
    h["exec_status"]="Вручен"
    h["handover_date"]="2026-10-10"
    h["payment_status"]="Оплачено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    assert db.monthly_summary(c,year=2026,month=9)==[]
    octo=db.monthly_summary(c,year=2026,month=10)
    assert len(octo)==1
    r=octo[0]
    assert r["contract_sum"]==100000
    assert r["contracts_count"]==1
    rows=db.summary_contracts(c,2026,10)
    assert len(rows)==1 and rows[0]["id"]==pid and rows[0]["deferred_purchase"] is True
    assert rows[0]["summary_period"]=="2026-10-10"
    c.close()

def test_contract_expenses_reduce_profit():
    c=newdb()
    db.insert_purchase(c, header(contract_no="COST"), [item()])
    r=db.monthly_summary(c,year=2026,month=9)[0]
    # tax 7% = 7000; total expenses = 50k+5k+3k+2k+1k+7k = 68k
    assert r["tax"]==7000
    assert r["total_expenses"]==68000
    assert r["profit"]==32000
    c.close()

def test_monthly_expense_reduces_profit_and_can_exist_without_contract():
    c=newdb()
    db.insert_purchase(c, header(contract_no="MEXP"), [item()])
    eid=db.insert_monthly_expense(c,{
        "expense_date":"2026-09-20","category":"Проценты по кредиту",
        "amount":10000,"description":"Кредит"
    })
    r=db.monthly_summary(c,year=2026,month=9)[0]
    assert r["monthly_expenses"]==10000
    assert r["total_expenses"]==78000
    assert r["profit"]==22000
    ex=db.fetch_monthly_expenses(c,2026,9)
    assert len(ex)==1 and ex[0]["id"]==eid
    db.delete_monthly_expense(c,eid)
    assert db.fetch_monthly_expenses(c,2026,9)==[]
    db.insert_monthly_expense(c,{
        "expense_date":"2026-10-05","category":"Банк",
        "amount":2500,"description":"Обслуживание"
    })
    octo=db.monthly_summary(c,year=2026,month=10)
    assert len(octo)==1 and octo[0]["contract_sum"]==0
    assert octo[0]["monthly_expenses"]==2500 and octo[0]["profit"]==-2500
    c.close()

def test_year_filter_includes_expense_and_handover_years():
    c=newdb()
    pid=db.insert_purchase(c, header(contract_no="Y",created_at="2026-12-01 10:00:00"), [item(mode="Отложенная закупка",stock=0)])
    h=db.fetch_by_id(c,pid)
    h["exec_status"]="Вручен"; h["handover_date"]="2027-01-10"; h["payment_status"]="Оплачено"
    db.update_purchase(c,pid,h,[dict(x) for x in h["items"]])
    db.insert_monthly_expense(c,{"expense_date":"2028-02-01","category":"Прочее","amount":1})
    years=db.distinct_years(c)
    assert 2026 in years and 2027 in years and 2028 in years
    c.close()

if __name__=="__main__":
    tests=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("ALL", len(tests), "TESTS PASSED")
