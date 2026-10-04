import os,sys,tempfile,sqlite3
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def rec(name,inn,price=100,product="Товар"):
    return {"competitor":name,"competitor_inn":inn,"product":product,"trade_type":"44-ФЗ","qty":1,"unit_price":price,"purchase_date":"2026-10-01"}

def test_same_inn_merges_names():
    c=fresh()
    a=db.insert_competitor_record(c,rec('ООО "Альфа"',"1234567890",100))
    b=db.insert_competitor_record(c,rec("Альфа ООО","1234567890",120))
    r1=db.fetch_competitor_record_by_id(c,a); r2=db.fetch_competitor_record_by_id(c,b)
    assert r1["competitor"]==r2["competitor"]
    stats=db.competitor_stats(c)
    assert len(stats)==1 and stats[0]["wins"]==2 and stats[0]["competitor_inn"]=="1234567890"
    c.close()

def test_different_inn_stay_separate_even_same_name():
    c=fresh()
    db.insert_competitor_record(c,rec("ООО Альфа","1234567890",100))
    db.insert_competitor_record(c,rec("ООО Альфа","0987654321",200))
    stats=db.competitor_stats(c)
    assert len(stats)==2
    assert {x["competitor_inn"] for x in stats}=={"1234567890","0987654321"}
    c.close()

def test_search_by_inn():
    c=fresh()
    db.insert_competitor_record(c,rec("ООО Альфа","1234567890"))
    rows=db.fetch_competitor_records(c,search="567890")
    assert len(rows)==1 and rows[0]["competitor_inn"]=="1234567890"
    c.close()

def test_inn_normalization():
    c=fresh()
    rid=db.insert_competitor_record(c,rec("ИП Тест","+7 123-456-789-012"))
    r=db.fetch_competitor_record_by_id(c,rid)
    assert r["competitor_inn"]=="7123456789012"
    c.close()

def test_old_schema_migrates_inn_column():
    p=os.path.join(tempfile.mkdtemp(),"old.db")
    c=sqlite3.connect(p)
    c.execute("CREATE TABLE competitor_records (id INTEGER PRIMARY KEY AUTOINCREMENT, competitor TEXT, product TEXT, trade_type TEXT, qty REAL, unit_price REAL, purchase_date TEXT)")
    c.execute("INSERT INTO competitor_records(competitor,product,unit_price) VALUES('ООО Старое','X',10)")
    c.commit(); c.close()
    c=db.get_connection(p)
    cols={r[1] for r in c.execute("PRAGMA table_info(competitor_records)").fetchall()}
    assert "competitor_inn" in cols
    row=c.execute("SELECT * FROM competitor_records LIMIT 1").fetchone()
    assert row["competitor"]=="ООО Старое" and row["competitor_inn"] is None
    c.close()

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts: t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
