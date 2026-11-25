"""Standalone Tk controller for bundled SQLite LAB on Windows/macOS.

This is a development preview, not a release or a substitute for Excel.
No production workbook is read. No remote network is opened.
"""
from __future__ import annotations
from threading import Thread
import sys
import webbrowser

def main():
    import tkinter as tk
    from tkinter import messagebox
    root=tk.Tk()
    root.title("Контракты ИП — SQLite LAB (тестовая версия)")
    root.geometry("470x260")
    root.minsize(410,230)
    root.configure(padx=20,pady=15)
    tk.Label(root,text="Контракты ИП · SQLite LAB",font=("Arial",16,"bold")).pack(pady=(5,6))
    tk.Label(root,text="Изолированная тестовая программа.\nРабочая Excel-версия не изменяется.",
             justify="center",wraplength=420).pack(pady=5)
    try:
        from server import Handler, HOST, PORT, store
        from http.server import ThreadingHTTPServer
        listener=ThreadingHTTPServer((HOST,PORT),Handler)
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
            store.close()
        except Exception:
            messagebox.showerror("Ошибка завершения",
                                 "Не удалось подтвердить освобождение ресурсов. "
                                 "Перед повторным запуском проверьте процессы.")
        finally:
            root.destroy()
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
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import urlopen
    from server import Handler,HOST,store
    listener=ThreadingHTTPServer((HOST,0),Handler)
    worker=Thread(target=listener.serve_forever,daemon=True)
    try:
        worker.start()
        port=listener.server_address[1]
        with urlopen(f"http://localhost:{port}/",timeout=10) as response:
            page=response.read()
            if response.status!=200 or b"SQLite" not in page:
                raise RuntimeError("Bundled app resource healthcheck failed")
        with urlopen(f"http://localhost:{port}/api/bootstrap",timeout=10) as response:
            if response.status!=200:
                raise RuntimeError("SQLite API healthcheck failed")
        return 0
    finally:
        listener.shutdown()
        listener.server_close()
        worker.join(timeout=5)
        store.close()

if __name__=="__main__":
    if "--smoke" in sys.argv:
        raise SystemExit(frozen_smoke())
    raise SystemExit(main())
