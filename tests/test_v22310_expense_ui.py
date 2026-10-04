import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import db, main

class FakeApp:
    def __init__(self, conn):
        self.conn=conn
        self.refresh_count=0
        self.selected_period=None
    def _selected_summary_period(self, require_selection=False):
        return (2026,9)
    def refresh_summary(self):
        self.refresh_count += 1
    def _select_summary_period(self, year, month):
        self.selected_period=(year,month)
        return True

def fresh():
    return db.get_connection(os.path.join(tempfile.mkdtemp(),"t.db"))

def test_add_monthly_expense_ui_flow_saves_and_recalculates():
    c=fresh()
    h=dict(customer="T",contract_no="1",contract_date="2026-09-01",created_at="2026-09-01 10:00:00",
           contract_sum=10000,contract_status="Заключен",exec_status="В процессе",
           payment_status="Не оплачено",deadline="2026-09-30")
    db.insert_purchase(c,h,[])
    before=db.monthly_summary(c,2026,9)[0]
    fake=FakeApp(c)

    old_ask=main.simpledialog.askstring
    old_info=main.messagebox.showinfo
    old_error=main.messagebox.showerror
    try:
        main.simpledialog.askstring=lambda *a,**k:"2 000"
        main.messagebox.showinfo=lambda *a,**k:None
        main.messagebox.showerror=lambda *a,**k: (_ for _ in ()).throw(AssertionError("unexpected error"))
        main.App._add_monthly_expense(fake)
    finally:
        main.simpledialog.askstring=old_ask
        main.messagebox.showinfo=old_info
        main.messagebox.showerror=old_error

    after=db.monthly_summary(c,2026,9)[0]
    assert after["monthly_expenses"]==before["monthly_expenses"]+2000
    assert after["total_expenses"]==before["total_expenses"]+2000
    assert after["profit"]==before["profit"]-2000
    assert fake.refresh_count==1
    assert fake.selected_period==(2026,9)
    c.close()

def test_cancel_does_not_save():
    c=fresh()
    fake=FakeApp(c)
    old_ask=main.simpledialog.askstring
    old_info=main.messagebox.showinfo
    try:
        main.simpledialog.askstring=lambda *a,**k:None
        main.messagebox.showinfo=lambda *a,**k:None
        main.App._add_monthly_expense(fake)
    finally:
        main.simpledialog.askstring=old_ask
        main.messagebox.showinfo=old_info
    assert list(db.fetch_monthly_expenses(c,2026,9))==[]
    assert fake.refresh_count==0
    c.close()

if __name__=="__main__":
    for t in [test_add_monthly_expense_ui_flow_saves_and_recalculates,test_cancel_does_not_save]:
        t(); print("PASS",t.__name__)
