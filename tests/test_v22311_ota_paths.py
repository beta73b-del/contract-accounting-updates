import os,sys,tempfile,pathlib
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","app"))
import auto_update

def test_powershell_discovery_returns_existing_file():
    p=auto_update._find_powershell_executable()
    assert os.path.isfile(p), p
    assert os.path.basename(p).lower().startswith("powershell")

def test_update_script_contains_paths_and_rollback():
    script=auto_update.build_windows_update_script(
        123,
        r"C:\Apps\UchetZakupok.exe",
        r"C:\Users\Test\AppData\Local\UchetZakupok\updates\v2.23.11\UchetZakupok.exe.new",
        r"C:\Users\Test\AppData\Local\UchetZakupok\updates\previous\UchetZakupok-v2.23.10.exe",
        r"C:\Users\Test\AppData\Local\UchetZakupok\app_debug.log",
    )
    assert "Copy-Item -LiteralPath $source -Destination $target -Force" in script
    assert "Copy-Item -LiteralPath $backup -Destination $target -Force" in script
    assert "Start-Process -FilePath $target -PassThru" in script

def test_launcher_source_has_explicit_path_errors():
    source=pathlib.Path(os.path.join(os.path.dirname(__file__),"..","app","auto_update.py")).read_text(encoding="utf-8")
    assert "Не найден текущий EXE:" in source
    assert "Не найден скачанный EXE:" in source
    assert "Не найдена папка программы:" in source
    assert "PowerShell:" in source
    assert "cwd=update_root" in source

if __name__=="__main__":
    for t in [test_powershell_discovery_returns_existing_file,test_update_script_contains_paths_and_rollback,test_launcher_source_has_explicit_path_errors]:
        t(); print("PASS",t.__name__)
