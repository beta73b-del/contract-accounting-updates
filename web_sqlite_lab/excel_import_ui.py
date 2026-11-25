"""Native import wizard, fixed local Excel API, no network uploads."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from queue import Queue,Empty
from excel_import import prepare_from_installation,build_trial_archive
from recovery_center import restore_separate

def import_excel(parent,base):
    import tkinter as tk
    from tkinter import ttk,filedialog,messagebox
    win=tk.Toplevel(parent);win.title("Excel → SQLite: тестовый импорт")
    win.geometry("720x370");win.minsize(620,330);win.transient(parent);win.grab_set()
    result=None;busy=False;workdir=TemporaryDirectory(prefix="contracts-import-")
    archive=Path(workdir.name)/"migration.zip"
    panel=ttk.Frame(win,padding=15);panel.pack(fill="both",expand=True)
    ttk.Label(panel,text="Импорт Excel в отдельную тестовую базу",font=("Arial",15,"bold")).pack(anchor="w")
    ttk.Label(panel,text="Оставьте Excel-приложение запущенным на localhost:8765. Не меняйте данные до завершения снимка. Выберите папку «Приложение» или её родительскую папку. Можно также выбрать ранее проверенный приватный снимок (manifest.json). Excel не изменяется.",wraplength=650).pack(anchor="w",pady=8)
    folder=tk.StringVar();mode=tk.StringVar(value="live");status=tk.StringVar(value="Данные ещё не импортированы.")
    ttk.Radiobutton(panel,text="Папка действующего Excel-приложения",variable=mode,value="live").pack(anchor="w")
    ttk.Radiobutton(panel,text="Папка сохранённого снимка",variable=mode,value="snapshot").pack(anchor="w")
    ttk.Entry(panel,textvariable=folder).pack(fill="x",pady=6)
    def browse():
        p=filedialog.askdirectory(parent=win,mustexist=True)
        if p:folder.set(p)
    def close():
        if busy:messagebox.showinfo("Импорт выполняется","Дождитесь завершения локального снимка.",parent=win)
        else:win.destroy()
    def start():
        nonlocal busy
        source=folder.get().strip();selected=mode.get()
        if not source:return
        if busy:return
        busy=True;go.configure(state="disabled");pick.configure(state="disabled")
        status.set("Проверка снимка, перенос и сверка данных…")
        q=Queue()
        def worker():
            try:
                if archive.exists():archive.unlink() # private temporary output only
                report=prepare_from_installation(source,archive) if selected=="live" else build_trial_archive(source,archive)
                q.put((True,report))
            except Exception as exc:q.put((False,str(exc)))
        Thread(target=worker,daemon=True).start()
        def poll():
            nonlocal busy,result
            try:ok,value=q.get_nowait()
            except Empty:win.after(150,poll);return
            busy=False;go.configure(state="normal");pick.configure(state="normal")
            if not ok:
                status.set("Импорт остановлен. Excel и действующая SQLite не изменены.")
                messagebox.showerror("Импорт не выполнен",value,parent=win);return
            status.set("Сверка пройдена для поддерживаемых данных. Полный функциональный паритет НЕ проверен.")
            summary=("Контрактов: "+str(value["activeContracts"])+"\nТоваров: "+str(value["products"])+
                "\nДокументов: "+str(value["documents"])+"\nРасходов: "+str(value["finance"]["activeExpenses"])+
                "\nОтсутствующих папок: "+str(len(value["missingLinkedFolders"]))+
                "\nЗамечаний по истории склада: "+str(len(value.get("stockWarnings",[])))+
                "\nЗамечаний по резервам: "+str(len(value.get("reservationWarnings",[])))+
                ("\nСклад и редактирование карточек временно заблокированы до сверки резервов." if value.get("stockImportLocked") else "")+
                "\nКарточек с финансовыми замечаниями (только просмотр): "+str(len(value.get("financialWarnings",[])))+
                "\nКарточек без цены: "+str(len(value.get("missingPricePositions",[])))+
                "\nКарточек с точно рассчитанной ценой: "+str(len(value.get("derivedPricePositions",[])))+
                "\nУдалённых строк Excel (в архиве): "+str(value["deletedExcelRowsPreserved"])+
                "\n\nСкладской журнал, исходный Excel, JSON и неподдерживаемые поля сохранены в архиве."
                "\nИстория, дополнительные поля, письма и полная финансовая сверка пока не реализованы в интерфейсе."
                "\n\nСоздать отдельную тестовую установку? Excel остаётся основной системой.")
            if not messagebox.askyesno("Результат частичного импорта",summary,parent=win):return
            try:
                target,proof=restore_separate(archive,base)
                result=(target,value)
                win.destroy()
            except Exception as exc:messagebox.showerror("Ошибка восстановления",str(exc),parent=win)
        win.after(150,poll)
    pick=ttk.Button(panel,text="Выбрать папку",command=browse);pick.pack(anchor="w")
    ttk.Label(panel,textvariable=status,wraplength=650).pack(anchor="w",pady=10)
    go=ttk.Button(panel,text="Проверить и импортировать",command=start);go.pack(anchor="e")
    win.protocol("WM_DELETE_WINDOW",close)
    try:parent.wait_window(win);return result
    finally:workdir.cleanup()
