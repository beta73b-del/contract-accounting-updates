"""Standalone Tk controller for bundled SQLite LAB on Windows/macOS.

This is a development preview, not a release or a substitute for Excel.
No production workbook is read. No remote network is opened.
"""
from __future__ import annotations
from threading import Thread
from pathlib import Path
import sys
import uuid
import webbrowser

def startup_data_state(folder):
    """Classify user data *before* importing server.py (which opens SQLite)."""
    root=Path(folder)
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        return "UNSAFE"
    db=root/"lab.db"
    if db.is_symlink() or (db.exists() and not db.is_file()):
        return "UNSAFE"
    if db.is_file():
        return "EXISTING"
    if root.exists() and any(root.iterdir()):
        return "ORPHANED"
    return "FRESH"

def main():
    import tkinter as tk
    from tkinter import messagebox
    from platform_paths import lab_data_dir
    from recovery_center import active_data_root,installation_lock
    base=lab_data_dir(frozen=bool(getattr(sys,"frozen",False)),
                        source_root=Path(__file__).resolve().parent)
    try:
        with installation_lock(base):
            return main_locked(base,active_data_root(base))
    except Exception as error:
        root=tk.Tk();root.withdraw()
        messagebox.showerror("Запуск остановлен",str(error),parent=root)
        root.destroy()
        return 1

def main_locked(base,target):
    import tkinter as tk
    from tkinter import messagebox,filedialog
    from transfer_package import TransferError
    from recovery_center import restore_first_install,restore_separate,activate_recovered
    from recovery_ui import choose_archive
    from excel_import_ui import import_excel
    root=tk.Tk()
    root.title("Контракты ИП — SQLite LAB (тестовая версия)")
    root.geometry("620x510")
    root.minsize(510,420)
    root.configure(padx=20,pady=15)
    tk.Label(root,text="Контракты ИП · SQLite LAB",font=("Arial",16,"bold")).pack(pady=(5,6))
    tk.Label(root,text="Изолированная тестовая программа.\nРабочая Excel-версия не изменяется.",
             justify="center",wraplength=420).pack(pady=5)
    def recovery_folder():
        try:
            from backup_settings import BackupSettings
            if target.is_dir():
                data=BackupSettings(target).read()
                if data.get("folder"):return data["folder"]
        except Exception:pass
        default=target/"backups"/"automatic"
        return default if default.is_dir() else None

    def recover_to_independent_folder(allow_activation=False):
        chosen=choose_archive(root,recovery_folder())
        if not chosen:return False
        try:
            separate,result=restore_separate(chosen["path"],base,expected_sha=chosen["sha256"])
            if allow_activation and messagebox.askyesno("Восстановление проверено",
                 "Сохранена новая проверенная база:\n"+str(separate)+
                 "\n\nСтарая папка не изменена. Сделать восстановленную базу активной при следующем запуске?"):
                activate_recovered(base,separate)
                messagebox.showinfo("Восстановлено","Запустите приложение заново. Старая папка сохранена:\n"+str(target))
            else:
                messagebox.showinfo("Копия восстановлена отдельно","Проверенная папка:\n"+str(separate)+"\nСтарые данные не заменены.")
            return True
        except Exception as error:
            messagebox.showerror("Восстановление не выполнено","Существующие данные не затронуты.\n"+str(error))
            return False

    def import_archive_first_run():
        chosen=choose_archive(root)
        if not chosen:return False
        try:
            result=restore_first_install(chosen["path"],target,expected_sha=chosen["sha256"])
            if not result.get("appReady"):raise TransferError("Архив не прошёл проверку")
            messagebox.showinfo("Восстановление завершено",
                "Полный комплект проверен и установлен. Контракты, склад, расходы и документы доступны после запуска.")
            return True
        except Exception as error:
            messagebox.showerror("Восстановление остановлено",
                "Существующие данные не заменялись.\n"+str(error))
            return False

    state=startup_data_state(target)
    if state=="UNSAFE":
        messagebox.showerror("Небезопасный каталог данных",
            "Каталог приложения или файл SQLite является ссылкой/неверным типом. "
            "Новая база не создаётся.")
        root.destroy()
        return 1
    if state=="ORPHANED":
        # Critical: database vanished but documents/backups remain.
        # Creating a NEW empty DB here would silently detach all documents.
        if messagebox.askyesno("SQLite отсутствует, документы сохранены",
            "Рабочей базы lab.db нет, но в каталоге приложения остались файлы.\n"
            "Создавать пустую базу поверх этих данных ЗАПРЕЩЕНО.\n\n"
            "Восстановить проверенный архив в отдельную папку?"):
            recover_to_independent_folder(allow_activation=True)
        root.destroy()
        return 1
    if state=="FRESH":
        if messagebox.askyesno("Первый запуск: Excel → SQLite",
            "Перенести данные действующего Excel-приложения в отдельную SQLite LAB?\n\nДа — импорт Excel. Нет — восстановление ZIP или пустая база."):
            imported=import_excel(root,base)
            if imported:
                activate_recovered(base,imported[0])
                messagebox.showinfo("Импорт завершён","Тестовая база готова. Запустите приложение повторно. Excel не изменён.")
            root.destroy()
            return 0 if imported else 1
        # Ask BEFORE importing the HTTP server, which would create a new DB.
        if messagebox.askyesno("Первый запуск — восстановление",
             "Новая SQLite-база отсутствует. Восстановить полный архив ZIP "
             "с Google Drive или другого компьютера (контракты, документы, Корзина)?\n\n"
             "«Нет» — начать с пустой тестовой базы."):
            if not import_archive_first_run():
                # Do not create an empty DB after a failed/cancelled recovery:
                # user should be able to retry without risking replacement.
                root.destroy()
                return 1

    try:
        from server import Handler, HOST, PORT, store, LoopbackHTTPServer, stop_services, configure_backup_folder, cleanup_thread, mutex, run_full_backup
    except Exception:
        # A damaged *existing* DB must not make the recovery UI inaccessible.
        # Restore only to an independent NEW directory; never overwrite the
        # old DB, documents or the user's Excel source.
        if messagebox.askyesno("Не удалось запустить SQLite",
            "Не удалось открыть существующую SQLite-базу или локальный порт.\n"
            "Проверьте, не запущен ли второй экземпляр программы.\n\n"
            "Попробовать восстановить полный ZIP в отдельный каталог?"):
            recover_to_independent_folder(allow_activation=True)
        root.destroy()
        return 1
    try:
        listener=LoopbackHTTPServer((HOST,PORT),Handler)
        listener.daemon_threads=False
        worker=Thread(target=listener.serve_forever,name="SQLiteLabHTTP",daemon=True)
        worker.start()
    except Exception:
        stop_services();store.close()
        messagebox.showerror("Локальный порт недоступен","Закройте другой экземпляр. Данные не переключались.")
        root.destroy()
        return 1

    address=f"http://localhost:{PORT}/"
    tk.Label(root,text=address,font=("Arial",11)).pack(pady=6)
    tk.Button(root,text="Открыть тестовую программу",font=("Arial",12),
              command=lambda:webbrowser.open(address),width=32).pack(pady=6)
    closing=False
    def stop(activate=None):
        nonlocal closing
        if closing:return
        closing=True
        try:
            listener.shutdown()
            listener.server_close()
            worker.join(timeout=8)
            stop_services()
            if worker.is_alive() or cleanup_thread.is_alive():
                raise RuntimeError("Службы не завершились")
            store.close()
            if activate is not None:
                activate_recovered(base,activate)
                messagebox.showinfo("Восстановление готово","Запустите программу заново. Старая папка сохранена:\n"+str(target))
        except Exception:
            messagebox.showerror("Ошибка завершения",
                                 "Не удалось подтвердить освобождение ресурсов. "
                                 "Перед повторным запуском проверьте процессы.")
        finally:
            root.destroy()
    def open_recovery_center():
        chosen=choose_archive(root,recovery_folder())
        if not chosen:return
        try:
            new_target,result=restore_separate(chosen["path"],base,expected_sha=chosen["sha256"])
            if messagebox.askyesno("Копия восстановлена и проверена",
                "Восстановленная база:\n"+str(new_target)+
                "\n\nЗавершить текущую программу и открыть восстановленную при следующем запуске?\nСтарые данные сохраняются целиком."):
                stop(activate=new_target)
            else:
                messagebox.showinfo("Отдельная копия","Текущая база не изменена:\n"+str(new_target))
        except Exception as error:
            messagebox.showerror("Восстановление не выполнено",str(error))

    def open_excel_import():
        imported=import_excel(root,base)
        if not imported:return
        if messagebox.askyesno("Открыть импортированную копию",
            "Новая SQLite сохранена отдельно. Завершить текущую программу и активировать импорт при следующем запуске?\nСтарая SQLite и Excel сохраняются."):
            stop(activate=imported[0])
        else:
            messagebox.showinfo("Отдельная копия",str(imported[0]))
    def backup_now():
        try:
            with mutex:result=run_full_backup(force=True)
            messagebox.showinfo("Полная копия","Локальный архив проверен.\nВнешний диск: "+str(result.get("lastSecondaryStatus","не подтверждено")))
        except Exception as error:
            messagebox.showerror("Резервирование не выполнено",str(error))

    def choose_backup_folder():
        chosen=filedialog.askdirectory(title="Выберите папку Google Drive или внешнего диска",
                                       mustexist=True,parent=root)
        if not chosen:return
        try:
            result=configure_backup_folder(chosen)
            messagebox.showinfo("Настройки резервирования",
                "Путь сохранён:\n"+str(result["selectedPath"])+
                "\n\nДля немедленного резервирования нажмите «Создать полную копию сейчас» в браузере.")
        except (TransferError,OSError,ValueError) as error:
            messagebox.showerror("Не удалось сохранить путь",str(error))
    tk.Button(root,text="Настройки: выбрать папку резервирования",
              command=choose_backup_folder,width=42).pack(pady=4)

    tk.Button(root,text="Создать полную резервную копию сейчас",
              command=backup_now,width=42).pack(pady=4)
    tk.Button(root,text="Импорт базы Excel в SQLite (тест)",
              command=open_excel_import,width=42).pack(pady=4)
    tk.Button(root,text="Центр резервирования и восстановления",
              command=open_recovery_center,width=42).pack(pady=4)
    tk.Button(root,text="Завершить работу",font=("Arial",12),
              command=stop,width=32).pack(pady=4)
    root.protocol("WM_DELETE_WINDOW",stop)
    root.bind("<Control-q>",lambda e:stop())
    if sys.platform=="darwin":
        root.bind("<Command-q>",lambda e:stop())
    root.after(500,lambda:webbrowser.open(address))
    root.mainloop()
    return 0

def frozen_smoke(expect_restored=False):
    """Headless packaged-artifact HTTP smoke against a synthetic temporary DB."""
    from threading import Thread
    from urllib.request import build_opener, ProxyHandler
    from server import Handler,HOST,store,LoopbackHTTPServer,stop_services
    from sqlite_build_safety import report
    if sys.stderr is not None:
        import json
        print("SQLITE_RUNTIME="+json.dumps(report(),sort_keys=True),
              file=sys.stderr,flush=True)
    opener=build_opener(ProxyHandler({}))
    if sys.stderr is not None:
        print("SQLITE_LAB_SMOKE_SERVER_START",file=sys.stderr,flush=True)
    listener=LoopbackHTTPServer((HOST,0),Handler)
    worker=Thread(target=listener.serve_forever,daemon=True)
    try:
        worker.start()
        port=listener.server_address[1]
        if sys.stderr is not None:
            print("SQLITE_LAB_SMOKE_HTTP_GET",file=sys.stderr,flush=True)
        with opener.open(f"http://127.0.0.1:{port}/",timeout=4) as response:
            page=response.read()
            if response.status!=200 or b"SQLite" not in page:
                raise RuntimeError("Bundled app resource healthcheck failed")
        if sys.stderr is not None:
            print("SQLITE_LAB_SMOKE_API_GET",file=sys.stderr,flush=True)
        with opener.open(f"http://127.0.0.1:{port}/api/bootstrap",timeout=4) as response:
            if response.status!=200:
                raise RuntimeError("SQLite API healthcheck failed")
            import json
            from sqlite_build_safety import patched_wal_reset
            payload=json.loads(response.read().decode("utf-8"))
            health=payload["integrity"]
            if expect_restored:
                records=payload["records"]
                stock=payload["stock"]["items"]
                files=payload["files"]
                expenses=payload["finance"]["expenses"]
                if not (len(records)==1 and records[0]["contract_number"]=="42/2026"
                    and stock[0]["physical"]==20 and stock[0]["reserved"]==7
                    and len(files["active"])==1 and len(files["trash"])==1
                    and len(expenses)==1 and expenses[0]["amount"]=="100.40"
                    and health["integrity"]=="ok" and health["foreignKeys"]==0):
                    raise RuntimeError("Recovered fixture differs from expected records")
                if sys.stderr is not None:
                    print("SQLITE_LAB_PACKAGED_RECOVERY_PASS",file=sys.stderr,flush=True)
            expected="wal" if patched_wal_reset() else "delete"
            actual=health.get("journalMode")
            if actual!=expected:
                raise RuntimeError("Bundled SQLite unsafe journal mode: expected "+
                                   expected+", got "+str(actual))
            if sys.stderr is not None:
                print("SQLITE_LAB_SMOKE_JOURNAL="+str(actual)+
                      ";version="+health.get("sqliteVersion","unknown"),
                      file=sys.stderr,flush=True)
        if sys.stderr is not None:
            print("SQLITE_LAB_SMOKE_PASS",file=sys.stderr,flush=True)
        return 0
    finally:
        # BaseServer.shutdown() can block indefinitely if serve_forever() has
        # failed or has not entered its polling loop (observed on macOS CI).
        # Never allow this cleanup to turn an HTTP failure into an opaque
        # subprocess timeout. Stop only if the thread remains alive.
        if worker.is_alive():
            stopper=Thread(target=listener.shutdown,daemon=True)
            stopper.start()
            stopper.join(timeout=3)
        listener.server_close()
        worker.join(timeout=3)
        stop_services()
        store.close()

def frozen_restore_smoke():
    # QA only: no user data root, always a newly allocated temporary directory.
    import os
    from tempfile import TemporaryDirectory
    from recovery_center import restore_first_install
    if os.environ.get("CONTRACTS_SQLITE_LAB_TEST")!="1" or len(sys.argv)!=3:
        raise SystemExit("QA recovery requires test mode and one fixture ZIP")
    with TemporaryDirectory(prefix="packaged-recovery-") as tmp:
        target=Path(tmp)/"fresh-install"
        result=restore_first_install(sys.argv[2],target)
        if not result.get("appReady"):raise RuntimeError("Restore not confirmed")
        os.environ["CONTRACTS_SQLITE_LAB_DB"]=str(target/"lab.db")
        return frozen_smoke(expect_restored=True)

if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1]=="--restore-smoke":
        raise SystemExit(frozen_restore_smoke())
    if "--smoke-no-console" in sys.argv:
        # Reproduce the no-stdio behavior of a real GUI-frozen Windows app.
        sys.stdout=None
        sys.stderr=None
        raise SystemExit(frozen_smoke())
    if "--smoke" in sys.argv:
        raise SystemExit(frozen_smoke())
    raise SystemExit(main())
