import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

LITE="Рутокен Lite 1010"
ECP="Рутокен ЭЦП 3.0 3120"

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def test_legacy_and_canonical_lite_merge_into_one_stock_row():
    c=fresh()
    c.execute("INSERT INTO stock_receipts(product,qty,unit_cost,receipt_date) VALUES(?,?,?,?)",
              ("Рутокен 1010",10,1,"2026-10-01"))
    c.execute("INSERT INTO stock_receipts(product,qty,unit_cost,receipt_date) VALUES(?,?,?,?)",
              (LITE,15,1,"2026-10-02"))
    c.commit()
    rows=[r for r in db.stock_summary(c) if r["product"]==LITE]
    assert len(rows)==1, rows
    assert rows[0]["on_hand"]==25
    assert not any(r["product"]=="Рутокен 1010" for r in db.stock_summary(c))
    c.close()

def test_legacy_and_canonical_ecp_merge_into_one_stock_row():
    c=fresh()
    c.execute("INSERT INTO stock_receipts(product,qty,unit_cost,receipt_date) VALUES(?,?,?,?)",
              ("Рутокен ЭЦП 3120",20,1,"2026-10-01"))
    c.execute("INSERT INTO stock_receipts(product,qty,unit_cost,receipt_date) VALUES(?,?,?,?)",
              (ECP,30,1,"2026-10-02"))
    c.commit()
    rows=[r for r in db.stock_summary(c) if r["product"]==ECP]
    assert len(rows)==1, rows
    assert rows[0]["on_hand"]==50
    assert not any(r["product"]=="Рутокен ЭЦП 3120" for r in db.stock_summary(c))
    c.close()

def test_case_and_whitespace_duplicates_merge_for_other_products():
    c=fresh()
    c.execute("INSERT INTO stock_receipts(product,qty,unit_cost,receipt_date) VALUES(?,?,?,?)",
              ("Тестовый товар",5,1,"2026-10-01"))
    c.execute("INSERT INTO stock_receipts(product,qty,unit_cost,receipt_date) VALUES(?,?,?,?)",
              ("  тестовый   товар  ",7,1,"2026-10-02"))
    c.commit()
    rows=[r for r in db.stock_summary(c) if db.normalize_product_key(r["product"])=="тестовый товар"]
    assert len(rows)==1, rows
    assert rows[0]["on_hand"]==12
    c.close()

def test_catalog_has_only_one_fixed_name_each():
    c=fresh()
    for name in ("Рутокен 1010",LITE,"Рутокен ЭЦП 3120",ECP):
        db.ensure_product(c,name)
    products=db.catalog_products(c)
    assert products.count(LITE)==1, products
    assert products.count(ECP)==1, products
    assert "Рутокен 1010" not in products
    assert "Рутокен ЭЦП 3120" not in products
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
