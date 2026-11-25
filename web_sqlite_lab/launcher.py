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
    from tkinter import messagebox,filedialog
    from platform_paths import lab_data_dir
    from transfer_package import restore_new_install, TransferError

    target=lab_data_dir(frozen=bool(getattr(sys,"frozen",False)),
                        source_root=Path(__file__).resolve().parent)
    root=tk.Tk()
    root.title("Контракты ИП — SQLite LAB (тестовая версия)")
    root.geometry("580x420")
    root.minsize(490,380)
    root.configure(padx=20,pady=15)
    tk.Label(root,text="Контракты ИП · SQLite LAB",font=("Arial",16,"bold")).pack(pady=(5,6))
    tk.Label(root,text="Изолированная тестовая программа.\nРабочая Excel-версия не изменяется.",
             justify="center",wraplength=420).pack(pady=5)
    def recover_to_independent_folder():
        """Use a verified archive, preserving the whole old data root."""
        default=target/"backups"/"automatic"
        chosen=filedialog.askopenfilename(
            title="Выберите проверенный полный ZIP",
            initialdir=str(default if default.is_dir() else target.parent),
            filetypes=[("Резервный ZIP","*.zip"),("Все файлы","*.*")])
        if not chosen:return False
        separate=target.with_name(target.name+"-Recovered-"+uuid.uuid4().hex[:8])
        try:
            result=restore_new_install(chosen,separate)
            if not result.get("appReady"):
                raise TransferError("Incomplete restore")
            messagebox.showinfo("Копия восстановлена отдельно",
                "Полный комплект проверен. Папка:\n"+str(separate)+"\n\n"
                "Старые база и документы не заменены. Переключение "
                "разрешено только после сверки восстановленных данных.")
            return True
        except Exception:
            messagebox.showerror("Восстановление не выполнено",
                "Архив не прошёл полную проверку. Существующие данные не затронуты.")
            return False

    def import_archive_first_run():
        """Only for a new, not-yet-created application data directory."""
        chosen=filedialog.askopenfilename(
            title="Выберите полный архив Контракты ИП ZIP",
            filetypes=[("Полный архив ZIP","*.zip"),("Все файлы","*.*")])
        if not chosen:
            return False
        if target.is_symlink() or (target.exists() and any(target.iterdir())):
            messagebox.showerror("Восстановление отменено",
                                 "Каталог приложения уже содержит данные. "
                                 "Нельзя заменять их архивом.")
            return False
        try:
            if target.exists():
                target.rmdir()  # Only an EXISTING EMPTY directory.
            target.parent.mkdir(parents=True,exist_ok=True)
            result=restore_new_install(chosen,target)
            if not result.get("appReady"):
                raise TransferError("Архив не прошёл полную проверку")
            messagebox.showinfo("Восстановление завершено",
                                "Полный комплект проверен и установлен в новый "
                                "каталог данных. Рабочая Excel-версия не затронута.")
            return True
        except Exception:
            messagebox.showerror("Восстановление остановлено",
                                 "Архив не удалось проверить или развернуть. "
                                 "Существующие данные не заменялись.")
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
            recover_to_independent_folder()
        root.destroy()
        return 1
    if state=="FRESH":
        # Ask BEFORE importing the HTTP server, which would create a new DB.
        if messagebox.askyesno("Первый запуск — восстановление",
             "Новая SQLite-база отсутствует. Восстановить полный архив ZIP "
             "с другого компьютера (контракты, документы, Корзина)?\n\n"
             "«Нет» — начать с пустой тестовой базы."):
            if not import_archive_first_run():
                # Do not create an empty DB after a failed/cancelled recovery:
                # user should be able to retry without risking replacement.
                root.destroy()
                return 1

    try:
        from server import Handler, HOST, PORT, store, LoopbackHTTPServer, stop_services, configure_backup_folder
        listener=LoopbackHTTPServer((HOST,PORT),Handler)
        listener.daemon_threads=False
        worker=Thread(target=listener.serve_forever,name="SQLiteLabHTTP",daemon=True)
        worker.start()
    except Exception:
        # A damaged *existing* DB must not make the recovery UI inaccessible.
        # Restore only to an independent NEW directory; never overwrite the
        # old DB, documents or the user's Excel source.
        if messagebox.askyesno("Не удалось запустить SQLite",
            "Не удалось открыть существующую SQLite-базу или локальный порт.\n"
            "Проверьте, не запущен ли второй экземпляр программы.\n\n"
            "Попробовать восстановить полный ZIP в отдельный каталог?"):
            recover_to_independent_folder()
        root.destroy()
        return 1

    address=f"http://localhost:{PORT}/"
    tk.Label(root,text=address,font=("Arial",11)).pack(pady=6)
    tk.Button(root,text="Открыть тестовую программу",font=("Arial",12),
              command=lambda:webbrowser.open(address),width=32).pack(pady=6)
    closing=False
    def stop():
        nonlocal closing
        if closing:return
        closing=True
        try:
            listener.shutdown()
            listener.server_close()
            worker.join(timeout=8)
            stop_services()
            store.close()
        except Exception:
            messagebox.showerror("Ошибка завершения",
                                 "Не удалось подтвердить освобождение ресурсов. "
                                 "Перед повторным запуском проверьте процессы.")
        finally:
            root.destroy()
    def restore_separate():
        """Never overwrite an open/current database: restore to a sibling."""
        chosen=filedialog.askopenfilename(
            title="Выберите полный архив ZIP",
            filetypes=[("ZIP архив","*.zip"),("Все файлы","*.*")])
        if not chosen:return
        new_target=target.with_name(target.name+"-Restored-"+uuid.uuid4().hex[:8])
        try:
            restore_new_install(chosen,new_target)
            messagebox.showinfo("Новая копия восстановлена",
                "Все данные проверены и сохранены отдельно:\n"+str(new_target)+
                "\n\nТекущая база НЕ заменена. Чтобы переключить установку, "
                "потребуется отдельная проверенная процедура переноса.")
        except Exception:
            messagebox.showerror("Импорт не выполнен",
                                 "Не удалось подтвердить целостность полного архива. "
                                 "Текущая база и документы не изменялись.")

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

    tk.Button(root,text="Восстановить ZIP в отдельную папку",
              command=restore_separate,width=38).pack(pady=4)
    tk.Button(root,text="Завершить работу",font=("Arial",12),
              command=stop,width=32).pack(pady=4)
    root.protocol("WM_DELETE_WINDOW",stop)
    root.bind("<Control-q>",lambda e:stop())
    if sys.platform=="darwin":
        root.bind("<Command-q>",lambda e:stop())
    root.after(500,lambda:webbrowser.open(address))
    root.mainloop()
    return 0

def frozen_smoke():
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
            health=json.loads(response.read().decode("utf-8"))["integrity"]
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

if __name__=="__main__":
    if "--smoke-no-console" in sys.argv:
        # Reproduce the no-stdio behavior of a real GUI-frozen Windows app.
        sys.stdout=None
        sys.stderr=None
        raise SystemExit(frozen_smoke())
    if "--smoke" in sys.argv:
        raise SystemExit(frozen_smoke())
    raise SystemExit(main())
