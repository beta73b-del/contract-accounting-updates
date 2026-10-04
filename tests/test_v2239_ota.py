import os,sys,tempfile
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import auto_update, main

def test_update_script_replaces_only_exe_and_rolls_back():
    script=auto_update.build_windows_update_script(
        1234,
        r"C:\Apps\UchetZakupok.exe",
        r"C:\Users\Test\AppData\Local\UchetZakupok\updates\v2.23.9\UchetZakupok.exe.new",
        r"C:\Users\Test\AppData\Local\UchetZakupok\updates\previous\UchetZakupok-v2.23.8.exe",
        r"C:\Users\Test\AppData\Local\UchetZakupok\app_debug.log",
    )
    assert "Copy-Item -LiteralPath $source -Destination $target -Force" in script
    assert "Start-Process -FilePath $target -PassThru" in script
    assert "Start-Sleep -Seconds 5" in script
    assert "Copy-Item -LiteralPath $backup -Destination $target -Force" in script
    assert "zakupki.db" not in script
    assert "data\\zakupki" not in script.lower()

def test_update_settings_source_has_manual_check_and_startup_check():
    from pathlib import Path
    source=Path(os.path.join(os.path.dirname(__file__),"..","app","main.py")).read_text(encoding="utf-8")
    assert 'text="Проверить сейчас"' in source
    assert 'text="Проверять обновления при запуске"' in source
    assert 'self.after(2500, lambda: self._start_update_check(manual=False))' in source
    assert 'db.create_backup(db.default_db_path())' in source

def test_update_install_does_not_sync_cloud_in_install_method():
    from pathlib import Path
    source=Path(os.path.join(os.path.dirname(__file__),"..","app","main.py")).read_text(encoding="utf-8")
    start=source.index("    def _install_downloaded_update")
    end=source.index("    def _open_product_catalog",start)
    block=source[start:end]
    assert "sync_working_to_cloud" not in block
    assert "cloud_db_path" not in block
    assert "db.default_db_path()" in block

def test_version_comparison():
    assert auto_update.is_newer("2.24.0","2.23.9")
    assert not auto_update.is_newer("2.23.9","2.23.9")
    assert not auto_update.is_newer("2.23.8","2.23.9")

if __name__=="__main__":
    ts=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in ts:
        t(); print("PASS",t.__name__)
    print("ALL",len(ts),"TESTS PASSED")
