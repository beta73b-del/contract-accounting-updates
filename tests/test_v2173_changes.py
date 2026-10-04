import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db, main

class DummyWidget:
    def __init__(self, value=""):
        self.value=value
    def set(self, value):
        self.value=value
    def get(self):
        return self.value

class DummyDialog:
    _apply_auto_status_suggestions = main.PurchaseDialog._apply_auto_status_suggestions
    _document_stage_warnings = main.PurchaseDialog._document_stage_warnings
    def __init__(self):
        self.widgets={"exec_status":DummyWidget("Отправлено")}

def test_handover_sets_delivered():
    d=DummyDialog()
    h={"handover_date":"2026-10-04","exec_status":"Отправлено"}
    out=d._apply_auto_status_suggestions(h)
    assert out["exec_status"]=="Вручен"
    assert d.widgets["exec_status"].get()=="Вручен"

def test_handover_does_not_downgrade_executed():
    d=DummyDialog(); d.widgets["exec_status"].set("Исполнено")
    h={"handover_date":"2026-10-04","exec_status":"Исполнено","payment_status":"Оплачено"}
    out=d._apply_auto_status_suggestions(h)
    assert out["exec_status"]=="Исполнено"

def test_empty_handover_does_not_change_status():
    d=DummyDialog()
    h={"handover_date":None,"exec_status":"Отправлено"}
    out=d._apply_auto_status_suggestions(h)
    assert out["exec_status"]=="Отправлено"

def test_no_document_warnings():
    d=DummyDialog()
    assert d._document_stage_warnings({"contract_status":"Заключен","exec_status":"Исполнено"})==[]

if __name__=="__main__":
    tests=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t(); print("PASS",t.__name__)
    print("ALL",len(tests),"TESTS PASSED")
