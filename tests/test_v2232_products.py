import os,sys,tempfile,sqlite3
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

LITE="Рутокен Lite 1010"
ECP="Рутокен ЭЦП 3.0 3120"

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def header(no, created="2026-10-01 10:00:00", status="В процессе"):
    return dict(
        customer="T",contract_no=no,contract_date="2026-10-01",created_at=created,
        contract_sum=1000,contract_status="Заключен",exec_status=status,
        payment_status="Не оплачено",deadline="2026-10-20"
    )

def item(product,qty,stock=None):
    if stock is None: stock=qty
    return {"product":product,"qty":qty,"supply_mode":"Со склада","stock_qty":stock,
            "procurement_reminder_days":30,"procurement_status":"Не начата"}

def row_map(c):
    return {r["product"]:r for r in db.stock_summary(c)}

def test_fixed_products_exist_on_empty_warehouse():
    c=fresh()
    products=db.catalog_products(c)
    assert products[:2]==[LITE,ECP], products
    rows=row_map(c)
    assert LITE in rows and ECP in rows
    assert rows[LITE]["on_hand"]==0 and rows[ECP]["on_hand"]==0
    c.close()

def test_other_product_can_be_added():
    c=fresh()
    db.ensure_product(c,"Другой товар")
    rows=row_map(c)
    assert "Другой товар" in rows
    c.close()

def test_fixed_products_cannot_be_renamed():
    c=fresh()
    try:
        db.rename_product(c,LITE,"Новое название")
    except ValueError as e:
        assert "закреп" in str(e).casefold()
    else:
        raise AssertionError("fixed product was renamed")
    c.close()

def test_known_legacy_aliases_are_canonicalized():
    c=fresh()
    pid=db.insert_purchase(c,header("A"),[
        item("Рутокен Lite",10),
        item("Рутокен ЭЦП 3.0",20),
    ])
    its=db.fetch_items(c,pid)
    names=[x["product"] for x in its]
    assert names==[LITE,ECP], names
    c.close()

def test_month_product_breakdown_matches_contract_lines():
    c=fresh()
    db.insert_purchase(c,header("A"),[item(LITE,100),item(ECP,200)])
    db.insert_purchase(c,header("B"),[item(LITE,50),item(ECP,300)])
    sm=db.monthly_summary(c,2026,10)[0]
    assert sm["product_quantities"]=={LITE:150.0,ECP:500.0}, sm["product_quantities"]
    assert sm["contract_qty_total"]==650.0
    # Оба контракта пока «В процессе»: в реализованное они не входят.
    assert sm["qty_total"]==0
    rows=db.summary_contracts(c,2026,10)
    manual={}
    for r in rows:
        for it in r["items"]:
            manual[it["product"]]=manual.get(it["product"],0)+float(it["qty"] or 0)
    assert manual==sm["product_quantities"]
    c.close()

def test_product_breakdown_and_realized_are_separate():
    c=fresh()
    p1=db.insert_purchase(c,header("A"),[item(ECP,500)])
    db.insert_purchase(c,header("B"),[item(LITE,200)])
    h=db.fetch_by_id(c,p1); h["exec_status"]="Отправлено"
    db.update_purchase(c,p1,h,[dict(x) for x in h["items"]])
    sm=db.monthly_summary(c,2026,10)[0]
    assert sm["product_quantities"]=={LITE:200.0,ECP:500.0}
    assert sm["contract_qty_total"]==700
    assert sm["qty_total"]==500
    c.close()

def test_reopen_migrates_legacy_table_values():
    p=os.path.join(tempfile.mkdtemp(),"old.db")
    c=db.get_connection(p)
    c.execute("INSERT INTO stock_receipts(product,qty,unit_cost,receipt_date) VALUES('Рутокен 3120',7,1,'2026-10-01')")
    c.commit(); c.close()
    c=db.get_connection(p)
    row=c.execute("SELECT product FROM stock_receipts WHERE qty=7").fetchone()
    assert row["product"]==ECP
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
