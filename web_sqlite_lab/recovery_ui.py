"""Native recovery chooser for fresh, healthy and damaged installations."""
from pathlib import Path
from threading import Thread
from queue import Queue,Empty
from recovery_center import scan_archives,review_archive

def choose_archive(parent,initial_folder=None):
    import tkinter as tk
    from tkinter import ttk,filedialog,messagebox
    win=tk.Toplevel(parent)
    win.title("Центр резервирования и восстановления")
    win.geometry("860x480");win.minsize(700,390)
    win.transient(parent);win.grab_set()
    chosen=None;rows={};busy=False
    folder=tk.StringVar(value=str(initial_folder or ""))
    status=tk.StringVar(value="Выберите папку Google Drive или отдельный ZIP.")
    top=ttk.Frame(win,padding=12);top.pack(fill="both",expand=True)
    ttk.Label(top,text="Резервные копии — проверка и восстановление",font=("Arial",15,"bold")).pack(anchor="w")
    ttk.Label(top,text="Проверяются SQLite, документы и контрольные суммы. Повреждённые архивы нельзя выбрать.",wraplength=750).pack(anchor="w",pady=5)
    entry=ttk.Entry(top,textvariable=folder);entry.pack(fill="x",pady=4)
    buttons=ttk.Frame(top);buttons.pack(fill="x",pady=4)
    table=ttk.Treeview(top,columns=("date","size","records","docs","state"),show="headings",selectmode="browse")
    for name,label,width in [("date","Дата UTC",160),("size","Размер, МБ",90),("records","Контракты",90),("docs","Документы",90),("state","Файл / результат",350)]:
        table.heading(name,text=label);table.column(name,width=width,stretch=name=="state")
    frame=ttk.Frame(top);frame.pack(fill="both",expand=True,pady=5)
    table.pack(in_=frame,side="left",fill="both",expand=True)
    scroll=ttk.Scrollbar(frame,orient="vertical",command=table.yview);scroll.pack(side="right",fill="y")
    table.configure(yscrollcommand=scroll.set)
    ttk.Label(top,textvariable=status,wraplength=750).pack(anchor="w",pady=4)
    footer=ttk.Frame(top);footer.pack(fill="x")
    def choose_folder():
        path=filedialog.askdirectory(parent=win,title="Папка Google Drive / резервных копий",mustexist=True)
        if path:folder.set(path);start_scan()
    def fill(result):
        rows.clear()
        for key in table.get_children():table.delete(key)
        for index,info in enumerate(result["valid"]):
            key="ok"+str(index);rows[key]=info
            table.insert("","end",iid=key,values=(info["createdUtc"][:19].replace("T"," "),round(info["size"]/1048576,2),
                info["database"]["counts"].get("contracts",0),info["documents"],Path(info["path"]).parent.name+"/"+Path(info["path"]).name))
        for index,info in enumerate(result["invalid"]):
            table.insert("","end",iid="bad"+str(index),values=("-","-","-","-","НЕ ПРОШЁЛ: "+Path(info["path"]).name+" ("+info["error"]+")"))
        if result["valid"]:table.selection_set("ok0");table.focus("ok0")
        status.set("Исправных: "+str(len(result["valid"]))+"; отклонено: "+str(len(result["invalid"]))+". Рекомендована самая свежая проверенная копия.")
    def work(fn):
        nonlocal busy
        if busy:return
        busy=True
        for b in (browse,scan,manual,accept):b.configure(state="disabled")
        status.set("Проверка архивов…")
        q=Queue()
        def task():
            try:q.put((True,fn()))
            except Exception as error:q.put((False,str(error)))
        Thread(target=task,daemon=True).start()
        def poll():
            nonlocal busy
            try:ok,result=q.get_nowait()
            except Empty:
                if win.winfo_exists():win.after(100,poll)
                return
            busy=False
            if not win.winfo_exists():return
            for b in (browse,scan,manual,accept):b.configure(state="normal")
            if ok:fill(result)
            else:status.set("Проверка не выполнена: "+result)
        win.after(100,poll)
    def start_scan():
        path=folder.get().strip()
        if not path:status.set("Укажите папку");return
        work(lambda:scan_archives(path))
    def manual_file():
        path=filedialog.askopenfilename(parent=win,title="Выберите полный резервный ZIP",filetypes=[("ZIP","*.zip")])
        if not path:return
        def verify():
            info=review_archive(path)
            return {"valid":[{"path":path,"size":Path(path).stat().st_size,**info}],"invalid":[]}
        work(verify)
    def accept_selected():
        nonlocal chosen
        selection=table.selection()
        if not selection or selection[0] not in rows:
            messagebox.showwarning("Нет исправной копии","Выберите проверенный архив",parent=win);return
        info=rows[selection[0]]
        if messagebox.askyesno("Подтвердите восстановление","Восстановить архив от "+info["createdUtc"]+"?\n"+info["path"]+"\n\nКонтрактов: "+str(info["database"]["counts"].get("contracts",0))+"\nДокументов: "+str(info["documents"]),parent=win):
            chosen=info["path"];win.destroy()
    browse=ttk.Button(buttons,text="Выбрать папку Google Drive",command=choose_folder);browse.pack(side="left",padx=3)
    scan=ttk.Button(buttons,text="Найти и проверить архивы",command=start_scan);scan.pack(side="left",padx=3)
    manual=ttk.Button(buttons,text="Выбрать ZIP вручную",command=manual_file);manual.pack(side="left",padx=3)
    accept=ttk.Button(footer,text="Восстановить выбранную копию",command=accept_selected);accept.pack(side="right",padx=3)
    ttk.Button(footer,text="Отмена",command=win.destroy).pack(side="right",padx=3)
    parent.wait_window(win)
    return chosen
