import os,sys,tempfile,pathlib
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db

ECP="Рутокен ЭЦП 3.0 3120"

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def test_ecp_3120_aliases_are_one_product():
    variants=[
        "Рутокен ЭЦП 3.0 3120",
        "Рутокен ЭЦП 3120",
        "  Рутокен   ЭЦП   3120  ",
        "Рутокен ЭЦП 3.0/3120",
    ]
    for name in variants:
        assert db.canonical_product_name(name)==ECP, name

def test_stock_merges_ecp_3120_variants():
    c=fresh()
    c.execute("INSERT INTO stock_receipts(product,qty,unit_cost,receipt_date) VALUES(?,?,?,?)",
              ("Рутокен ЭЦП 3120",40,1,"2026-10-01"))
    c.execute("INSERT INTO stock_receipts(product,qty,unit_cost,receipt_date) VALUES(?,?,?,?)",
              (ECP,60,1,"2026-10-02"))
    c.commit()
    rows=[r for r in db.stock_summary(c) if r["product"]==ECP]
    assert len(rows)==1, rows
    assert rows[0]["on_hand"]==100
    assert not any(r["product"]=="Рутокен ЭЦП 3120" for r in db.stock_summary(c))
    c.close()

def test_competitor_tab_uses_resizable_panes_and_scrollbars():
    source=pathlib.Path(os.path.join(os.path.dirname(__file__),"..","app","main.py")).read_text(encoding="utf-8")
    start=source.index("    def _build_competitors_tab(self):")
    end=source.index("    def _selected_competitor_id(self):",start)
    block=source[start:end]
    assert "ttk.Panedwindow" in block
    assert 'orient=tk.VERTICAL' in block
    assert block.count('ttk.Scrollbar')>=3
    assert 'panes.add(stats_panel, weight=3)' in block
    assert 'height=6' in block

if __name__=="__main__":
    for t in [test_ecp_3120_aliases_are_one_product,test_stock_merges_ecp_3120_variants,test_competitor_tab_uses_resizable_panes_and_scrollbars]:
        t(); print("PASS",t.__name__)
