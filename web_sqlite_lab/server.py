"""Local-only HTTP test backend for SQLite lab; never connects to Excel or port 8765."""
from __future__ import annotations
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from pathlib import Path
from secrets import token_urlsafe
from threading import RLock, Event, Thread
from urllib.parse import urlsplit, parse_qs, quote
import json
import os
import re
import sys
import uuid

from store import Store, DataError, Conflict, moscow_today
from finance_ledger import FinanceLedger, FinanceError
from finance_engine import forecast
from file_vault import FileVault, MAX_FILE_BYTES, RETENTION_DAYS
from transfer_package import export_lab, inspect_transfer, TransferError
from backup_policy import BackupPolicy
from backup_settings import BackupSettings

from platform_paths import lab_data_dir
from recovery_center import active_data_root

SOURCE_ROOT = Path(__file__).resolve().parent
# PyInstaller keeps bundled resources under _MEIPASS. Never create the DB there.
BASE = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
HOST = "127.0.0.1"
PORT = int(os.environ.get("CONTRACTS_SQLITE_LAB_PORT", "8766"))
if os.environ.get("CONTRACTS_SQLITE_LAB_DB") and not os.environ.get("CONTRACTS_SQLITE_LAB_TEST"):
    raise SystemExit("Override database path is allowed only when CONTRACTS_SQLITE_LAB_TEST=1")
DATA_DIR = lab_data_dir(frozen=bool(getattr(sys, "frozen", False)), source_root=SOURCE_ROOT)
if os.environ.get("CONTRACTS_SQLITE_LAB_DB"):
    DB=Path(os.environ["CONTRACTS_SQLITE_LAB_DB"]).resolve()
else:
    DATA_DIR=active_data_root(DATA_DIR)
    DB=(DATA_DIR/"lab.db").resolve()
class LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind performs socket.getfqdn(127.0.0.1):
        # this reverse DNS lookup can hang for 20s+ on macOS CI runners.
        # TCPServer binds directly; Host checks in Handler still apply.
        TCPServer.server_bind(self)
        self.server_name = HOST
        self.server_port = self.server_address[1]

store = Store(DB)
file_vault = FileVault(store, DB.parent / 'managed_files')
# Only app-owned files are managed. Legacy documents remain untouched until
# a user invokes an explicit protected delete through the vault.
(DB.parent / 'attachments').mkdir(parents=True,exist_ok=True)
finance_ledger = FinanceLedger(store)
backup_settings=BackupSettings(DB.parent)
backup_config_error=None
backup_selected_path=None
mutex=RLock()
nonce=token_urlsafe(32)
cleanup_stop=Event()
last_backup_error=None

def refresh_backup_policy():
    global backup_policy,backup_config_error,backup_selected_path
    backup_config_error=None
    backup_selected_path=None
    try:
        data=backup_settings.read()
        backup_selected_path=(os.environ.get("CONTRACTS_SQLITE_LAB_SECONDARY_BACKUP_DIR")
                              if data["legacy"] else data["folder"])
        backup_policy=BackupPolicy(DB,secondary_dir=backup_settings.destination(data))
    except (TransferError,OSError,ValueError):
        backup_config_error="Недоступно внешнее хранилище или его настройки"
        backup_policy=BackupPolicy(DB)

def backup_status():
    return {**backup_policy.status(),"selectedPath":backup_selected_path,
            "lastError":last_backup_error or backup_config_error}

def run_full_backup(force=False):
    global last_backup_error
    refresh_backup_policy()
    try:
        result=backup_policy.run_if_due(force=force)
        last_backup_error=None
        return {**result,**backup_status()}
    except Exception:
        last_backup_error="Не удалось создать проверенную полную копию"
        raise

def configure_backup_folder(raw):
    with mutex:
        backup_settings.save(raw)
        refresh_backup_policy()
        return backup_status()

refresh_backup_policy()

def _cleanup_worker():
    # Trigger on startup and then hourly, including missed daily backups.
    # A failed backup must never delete older verified copies. Run all
    # filesystem and SQL mutations behind the same per-process API mutex.
    global last_backup_error
    while not cleanup_stop.is_set():
        with mutex:
            try:
                file_vault.purge_expired()
                run_full_backup()
            except Exception as error:
                # Fail open for reading/using the application; fail closed
                # for backup claims and deletion of old snapshots.
                last_backup_error = type(error).__name__
                if sys.stderr is not None:
                    print("Automatic backup or Trash retention failed; existing data preserved",
                          file=sys.stderr)
        if cleanup_stop.wait(3600):
            return

cleanup_thread=Thread(target=_cleanup_worker,daemon=True,
                      name="ManagedFiles30DayRetention")
cleanup_thread.start()

def stop_services():
    cleanup_stop.set()
    cleanup_thread.join(timeout=3)

class Handler(BaseHTTPRequestHandler):
    server_version = "ContractsSQLiteLab/0.1"
    def log_message(self, fmt, *args):
        # PyInstaller --windowed may set sys.stderr to None; logging must not
        # abort an HTTP response or leave the browser waiting indefinitely.
        stream=getattr(sys,"stderr",None)
        if stream is not None:
            print("%s %s" % (self.log_date_time_string(), fmt % args), file=stream)

    def trusted_host(self):
        # Reject DNS rebinding: another origin must not read /api/bootstrap
        # (which includes the session nonce and potentially contract data).
        host=self.headers.get("Host","")
        # Headless packaged smoke intentionally binds an ephemeral port.
        # Use the listener's *actual* port, never the default PORT constant.
        port=self.server.server_port
        return host in (f"localhost:{port}",f"127.0.0.1:{port}")

    def reply(self, status, obj):
        body=json.dumps(obj,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.trusted_host():
            self.reply(403,{"error":"Недопустимое имя локального сервера"});return
        path=urlsplit(self.path).path
        if path == "/":
            html=(BASE / "static" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Security-Policy","default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Length",str(len(html)))
            self.end_headers();self.wfile.write(html);return
        if path=="/api/bootstrap":
            with mutex:
                cleanup=file_vault.purge_expired()
                self.reply(200,{"lab":True,"storage":"SQLite","nonce":nonce,"today":moscow_today(),
                                "records":store.list_contracts(),"trash":{"contracts":store.list_trashed()},"stock":{"items":store.stock_summary()},
                                "finance":finance_ledger.payload(),
                                "files":{"active":file_vault.list_files("active"),
                                         "trash":file_vault.list_files("trashed"),
                                         "legacy":file_vault.list_legacy(DB.parent/"attachments"),
                                         "retentionDays":RETENTION_DAYS,
                                         "cleanup":cleanup},
                                "integrity":store.verify(),
                                "backups":backup_status(),
                                "migration":"NOT RUN"});return
        if path=="/api/export/download":
            params=parse_qs(urlsplit(self.path).query)
            names=params.get("name",[])
            if len(names)!=1 or not re.fullmatch(r"complete-[0-9a-f]{32}\.zip",names[0]):
                self.reply(400,{"error":"Неверное имя архивного файла"});return
            dest=DB.parent/"backups"/names[0]
            try:
                with mutex:
                    inspect_transfer(dest)
                    with dest.open("rb") as stream:
                        self.send_response(200)
                        self.send_header("Content-Type","application/zip")
                        self.send_header("Content-Disposition",
                                         'attachment; filename="'+names[0]+'"')
                        self.send_header("Content-Length",str(dest.stat().st_size))
                        self.send_header("Cache-Control","no-store")
                        self.send_header("X-Content-Type-Options","nosniff")
                        self.end_headers()
                        while chunk:=stream.read(1024*1024):
                            self.wfile.write(chunk)
            except (FileNotFoundError,TransferError):
                self.reply(404,{"error":"Архив отсутствует либо не прошёл проверку"})
            return
        if path=="/api/files/legacy-download":
            params=parse_qs(urlsplit(self.path).query)
            names=params.get("path",[])
            if len(names)!=1:
                self.reply(400,{"error":"Укажите относительный путь документа"});return
            try:
                with mutex:
                    file_path=file_vault.legacy_path(names[0],DB.parent/"attachments")
                    with file_path.open("rb") as stream:
                        self.send_response(200)
                        self.send_header("Content-Type","application/octet-stream")
                        self.send_header("Content-Disposition",
                            "attachment; filename*=UTF-8''"+quote(file_path.name,safe=""))
                        self.send_header("Content-Length",str(file_path.stat().st_size))
                        self.send_header("Cache-Control","no-store")
                        self.send_header("X-Content-Type-Options","nosniff")
                        self.end_headers()
                        while chunk:=stream.read(1024*1024):
                            self.wfile.write(chunk)
            except DataError as exc:
                self.reply(404,{"error":str(exc)})
            return
        if path=="/api/files/download":
            parsed=parse_qs(urlsplit(self.path).query)
            file_ids=parsed.get("id",[])
            if len(file_ids)!=1:
                self.reply(400,{"error":"Требуется ID документа"});return
            try:
                with mutex:
                    meta,file_path=file_vault.get_active(file_ids[0])
                    with file_path.open("rb") as stream:
                        self.send_response(200)
                        self.send_header("Content-Type","application/octet-stream")
                        self.send_header("Content-Disposition",
                            "attachment; filename*=UTF-8''"+quote(meta["name"].rsplit("/",1)[-1],safe=""))
                        self.send_header("Content-Length",str(meta["size"]))
                        self.send_header("Cache-Control","no-store")
                        self.send_header("X-Content-Type-Options","nosniff")
                        self.end_headers()
                        while chunk:=stream.read(1024*1024):
                            self.wfile.write(chunk)
            except DataError as error:
                self.reply(404,{"error":str(error)})
            return
        self.reply(404,{"error":"Маршрут отсутствует"})

    def do_POST(self):
        if not self.trusted_host():
            self.reply(403,{"error":"Недопустимое имя локального сервера"});return
        origin=self.headers.get("Origin")
        if origin and origin not in (f"http://localhost:{self.server.server_port}", f"http://127.0.0.1:{self.server.server_port}"):
            self.reply(403,{"error":"Недопустимый Origin"});return
        if self.headers.get("X-Contracts-Key")!=nonce:
            self.reply(403,{"error":"Неверный защитный ключ"});return
        try:
            path=urlsplit(self.path).path
            size=int(self.headers.get("Content-Length","-1"))
            if path=="/api/files/upload":
                if not 0<=size<=MAX_FILE_BYTES:
                    raise DataError("Ограничение загрузки: 256 МБ на файл")
                query=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                def one(key,required=True):
                    result=query.get(key,[])
                    if len(result)>1 or (required and (len(result)!=1 or not result[0])):
                        raise DataError("Неверный параметр файла: "+key)
                    return result[0] if result else ""
                with mutex:
                    result=file_vault.add_stream(
                        one("requestId"),one("id"),one("name"),self.rfile,size,
                        one("contractId",False) or None)
                self.reply(200,result);return
            if size<0 or size>65536: raise DataError("Размер запроса недопустим")
            raw=json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(raw,dict): raise DataError("Неверное тело запроса")
            path=urlsplit(self.path).path
            with mutex:
                if path=="/api/product":
                    name=store.add_product(raw.get("name"))
                    result={"ok":True,"name":name}
                elif path=="/api/receipt":
                    result=store.receipt(raw.get("requestId"),raw.get("product"),raw.get("qty"),raw.get("unitCost"))
                elif path=="/api/record":
                    result=store.save_contract(raw.get("requestId"),raw.get("record"))
                elif path=="/api/trash":
                    result=store.trash_contract(raw.get("requestId"),raw.get("id"),
                                                raw.get("expectedRevision"),raw.get("reason",""))
                elif path=="/api/restore":
                    result=store.restore_contract(raw.get("requestId"),raw.get("id"),
                                                  raw.get("expectedRevision"))
                elif path=="/api/files/legacy-trash":
                    result=file_vault.trash_legacy_attachment(
                        raw.get("requestId"),raw.get("relativePath"),
                        DB.parent/"attachments",raw.get("contractId"))
                elif path=="/api/files/trash":
                    result=file_vault.trash_file(
                        raw.get("requestId"),raw.get("id"),
                        raw.get("revision"),raw.get("reason",""))
                elif path=="/api/files/restore":
                    result=file_vault.restore_file(
                        raw.get("requestId"),raw.get("id"),raw.get("revision"))
                elif path=="/api/finance":
                    result=finance_ledger.mutate(raw)
                elif path=="/api/backup/settings":
                    if "folder" not in raw:
                        raise DataError("Укажите папку или null для отключения")
                    result=configure_backup_folder(raw["folder"])
                elif path=="/api/backup/full":
                    result=run_full_backup(force=True)
                elif path=="/api/backup":
                    target=DB.parent/"backups"/("lab-"+datetime.utcnow().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8]+".db")
                    result=store.backup(target)
                elif path=="/api/export":
                    # Bundle ALL app-managed files including those in 30-day
                    # Trash, plus legacy attachments and a consistent SQLite
                    # online snapshot. No overwrite, no export on SHA failure.
                    file_vault.purge_expired()
                    target=DB.parent/"backups"/("complete-"+uuid.uuid4().hex+".zip")
                    info=export_lab(DB,DB.parent/"attachments",target,
                                    managed_dir=file_vault.root)
                    result={**info,"download":"/api/export/download?name="+target.name}
                elif path=="/api/verify":
                    result=store.verify()
                else:
                    self.reply(404,{"error":"Маршрут отсутствует"});return
            self.reply(200,result)
        except (DataError, FinanceError, TransferError, ValueError, KeyError, TypeError) as exc:
            self.reply(409 if isinstance(exc,Conflict) else 400,{"error":str(exc)})
        except (UnicodeError,json.JSONDecodeError):
            self.reply(400,{"error":"Некорректный JSON"})
        except Exception:
            self.reply(500,{"error":"Внутренняя ошибка. Подробности не передаются клиенту."})
            raise

if __name__=="__main__":
    print(f"SQLite LAB ONLY: http://localhost:{PORT}/ ; db={DB} ; production Excel is untouched",flush=True)
    server=LoopbackHTTPServer((HOST,PORT),Handler)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();stop_services();store.close()
