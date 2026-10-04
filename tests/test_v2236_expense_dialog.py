import os,sys,pathlib
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import main

def test_expense_dialog_uses_standard_simpledialog():
    source=pathlib.Path(os.path.join(os.path.dirname(__file__),"..","app","main.py")).read_text(encoding="utf-8")
    start=source.index("    def _add_monthly_expense(self):")
    end=source.index("    def _edit_tax_profile(self):", start)
    block=source[start:end]
    assert "simpledialog.askstring" in block
    assert "Введите сумму прочего расхода" in block
    assert "tk.Toplevel" not in block
    assert "Дата расхода:" not in block
    assert "Категория:" not in block
    assert "Описание:" not in block

if __name__=="__main__":
    test_expense_dialog_uses_standard_simpledialog()
    print("PASS test_expense_dialog_uses_standard_simpledialog")
