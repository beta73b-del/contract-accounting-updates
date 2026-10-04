"""Проверка перезапуска OTA с унаследованным окружением onefile."""
import argparse
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import auto_update
from app_version import __version__


def test_launcher_does_not_pass_old_onefile_environment():
    with tempfile.TemporaryDirectory() as folder:
        current = Path(folder) / "UchetZakupok.exe"
        new = Path(folder) / "new.exe"
        current.write_bytes(b"old")
        new.write_bytes(b"new")
        inherited = {"_PYI_ARCHIVE_FILE": str(current),
                     "_PYI_APPLICATION_HOME_DIR": str(Path(folder) / "deleted_MEI"),
                     "_PYI_PARENT_PROCESS_LEVEL": "1", "_MEIPASS2": "deleted",
                     "ZAKUPKI_LOCAL_DATA": "keep_user_path"}
        with patch.dict(os.environ, inherited), \
             patch.object(sys, "platform", "win32"), \
             patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", str(current)), \
             patch.object(auto_update, "_find_powershell_executable", return_value="powershell.exe"), \
             patch.object(auto_update.subprocess, "Popen") as launch:
            auto_update.launch_windows_installer(str(new), folder, str(Path(folder) / "update.log"))
            env = launch.call_args.kwargs["env"]
            assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
            assert env["ZAKUPKI_LOCAL_DATA"] == "keep_user_path"
            assert not any(k.startswith("_PYI_") or k == "_MEIPASS2" for k in env)
            assert os.environ["_PYI_APPLICATION_HOME_DIR"] == inherited["_PYI_APPLICATION_HOME_DIR"]


def _app_window_pid(target):
    """Подтвердить окно приложения, а не живой процесс с диалогом ошибки DLL."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                  ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    found = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]

    @callback_type
    def visit(hwnd, unused):
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, len(title))
        if title.value != f"Учет заключенных контрактов — v{__version__}":
            return True
        pid = ctypes.c_uint32()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                path = ctypes.create_unicode_buffer(32768)
                size = ctypes.c_uint32(len(path))
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    if os.path.normcase(path.value) == os.path.normcase(str(target)):
                        found.append(pid.value)
            finally:
                kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
    user32.EnumWindows(visit, None)
    return found[0] if found else None


def test_windows_restart_and_rollback(exe):
    assert sys.platform == "win32", "EXE integration requires Windows"
    for rollback in (False, True):
        with tempfile.TemporaryDirectory(prefix="OTA restart ") as folder:
            root = Path(folder)
            target = root / "UchetZakupok.exe"
            source = root / "download.exe.new"
            backup = root / "previous" / "UchetZakupok.exe"
            log = root / "update.log"
            shutil.copy2(exe, target)
            if rollback:
                source.write_bytes(b"invalid executable to exercise rollback")
            else:
                shutil.copy2(exe, source)
            marker = root / "data" / "user-data.txt"
            marker.parent.mkdir()
            marker.write_bytes(b"preserve data")
            script = root / "update.ps1"
            script.write_text(auto_update.build_windows_update_script(
                2147483647, str(target), str(source), str(backup), str(log)), encoding="utf-8-sig")
            env = dict(os.environ, _PYI_ARCHIVE_FILE=str(target),
                       _PYI_APPLICATION_HOME_DIR=str(root / "deleted_MEI"),
                       _PYI_PARENT_PROCESS_LEVEL="1", _MEIPASS2=str(root / "deleted_MEI"),
                       ZAKUPKI_LOCAL_DATA=str(root / "local"))
            env.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
            pid = None
            try:
                if not rollback:
                    # Reproduce the old launch: it reuses a deleted _MEI directory.
                    broken = subprocess.Popen([str(target)], env=env)
                    try:
                        time.sleep(2)
                        assert not _app_window_pid(target), "Negative control unexpectedly started normally"
                    finally:
                        subprocess.run(["taskkill", "/PID", str(broken.pid), "/T", "/F"],
                                       capture_output=True, check=False)
                        broken.wait(timeout=10)
                    print("PASS old restart failure reproduced with deleted onefile directory")
                subprocess.run([auto_update._find_powershell_executable(), "-NoProfile",
                                "-ExecutionPolicy", "Bypass", "-File", str(script)],
                               env=env, check=True, timeout=45)
                for _ in range(20):
                    pid = _app_window_pid(target)
                    if pid:
                        break
                    time.sleep(0.25)
                assert pid, "Main application window did not appear after OTA restart"
                text = log.read_text(encoding="utf-8-sig")
                assert ("выполнен откат" if rollback else "установлено успешно") in text, text
                assert marker.read_bytes() == b"preserve data"
                assert auto_update.sha256_file(str(target)) == auto_update.sha256_file(str(exe))
                print("PASS", "real EXE rollback" if rollback else "real EXE OTA restart")
            finally:
                pid = pid or _app_window_pid(target)
                if pid:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, check=False)
                    time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    test_launcher_does_not_pass_old_onefile_environment()
    print("PASS launcher environment")
    if args.exe:
        test_windows_restart_and_rollback(args.exe.resolve())
