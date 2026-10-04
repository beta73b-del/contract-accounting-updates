import os,sys
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import main

def test_fit_dialog_never_smaller_than_expense_form_minimum():
    w,h,x,y=main.fit_dialog_size(300,120,1920,1080,min_w=680,min_h=380)
    assert w>=680 and h>=380
    assert x>=0 and y>=0

def test_fit_dialog_respects_small_screen():
    w,h,x,y=main.fit_dialog_size(900,900,800,600,min_w=680,min_h=380)
    assert w<=760
    assert h<=520
    assert x>=0 and y>=0

def test_expense_dialog_source_contains_all_fields_and_geometry_guard():
    import pathlib
    s=pathlib.Path(os.path.join(os.path.dirname(__file__),"..","app","main.py")).read_text(encoding="utf-8")
    for text in ["Дата расхода:", "Категория:", "Сумма, руб.:", "Описание:", 'win.minsize(680, 380)', 'win.geometry(f"{width}x{height}+{x}+{y}")']:
        assert text in s, text

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
