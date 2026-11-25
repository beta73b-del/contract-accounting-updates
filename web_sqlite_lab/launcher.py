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

def main():
    import tkinter as tk
    from tkinter import messagebox,filedialog
    from platform_paths import lab_data_dir
    from transfer_package import restore_new_install, TransferError

    target=lab_data_dir(frozen=bool(getattr(sys,"frozen",False)),
                        source_root=Path(__file__).resolve().parent)
    root=tk.Tk()
    root.title("Контракты ИП — SQLite LAB (тестовая версия)")
    root.geometry("540x360")
    root.minsize(490,310)
    root.configure(padx=20,pady=15)
    tk.Label(root,text="Контракты ИП · SQLite LAB",font=("Arial",16,"bold")).pack(pady=(5,6))
    tk.Label(root,text="Изолированная тестовая программа.\nРабочая Excel-версия не изменяется.",
             justify="center",wraplength=420).pack(pady=5)
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

    if not (target/"lab.db").exists():
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
        from server import Handler, HOST, PORT, store, LoopbackHTTPServer, stop_services
        listener=LoopbackHTTPServer((HOST,PORT),Handler)
        listener.daemon_threads=False
        worker=Thread(target=listener.serve_forever,name="SQLiteLabHTTP",daemon=True)
        worker.start()
    except Exception:
        messagebox.showerror("Ошибка запуска","Не удалось открыть тестовую SQLite-базу или локальный порт.\n"
                             "Проверьте, не запущен ли второй экземпляр лаборатории.")
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
