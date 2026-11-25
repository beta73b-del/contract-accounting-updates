"""Local-only HTTP test backend for SQLite lab; never connects to Excel or port 8765."""
from __future__ import annotations
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from threading import RLock
from urllib.parse import urlsplit
import json
import os
import sys
import uuid

from store import Store, DataError, Conflict, moscow_today
from finance_ledger import FinanceLedger, FinanceError
from finance_engine import forecast

from platform_paths import lab_data_dir

SOURCE_ROOT = Path(__file__).resolve().parent
# PyInstaller keeps bundled resources under _MEIPASS. Never create the DB there.
BASE = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
HOST = "127.0.0.1"
PORT = int(os.environ.get("CONTRACTS_SQLITE_LAB_PORT", "8766"))
if os.environ.get("CONTRACTS_SQLITE_LAB_DB") and not os.environ.get("CONTRACTS_SQLITE_LAB_TEST"):
    raise SystemExit("Override database path is allowed only when CONTRACTS_SQLITE_LAB_TEST=1")
DATA_DIR = lab_data_dir(frozen=bool(getattr(sys, "frozen", False)), source_root=SOURCE_ROOT)
DB = Path(os.environ.get("CONTRACTS_SQLITE_LAB_DB", str(DATA_DIR / "lab.db"))).resolve()
store = Store(DB)
finance_ledger = FinanceLedger(store)
mutex = RLock()
nonce = token_urlsafe(32)

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
                self.reply(200,{"lab":True,"storage":"SQLite","nonce":nonce,"today":moscow_today(),
                                "records":store.list_contracts(),"trash":{"contracts":store.list_trashed()},"stock":{"items":store.stock_summary()},
                                "finance":finance_ledger.payload(),
                                "integrity":store.verify(),"migration":"NOT RUN"});return
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
            size=int(self.headers.get("Content-Length","-1"))
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
                elif path=="/api/finance":
                    result=finance_ledger.mutate(raw)
                elif path=="/api/backup":
                    target=DB.parent/"backups"/("lab-"+datetime.utcnow().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8]+".db")
                    result=store.backup(target)
                elif path=="/api/verify":
                    result=store.verify()
                else:
                    self.reply(404,{"error":"Маршрут отсутствует"});return
            self.reply(200,result)
        except (DataError, FinanceError, ValueError, KeyError, TypeError) as exc:
            self.reply(409 if isinstance(exc,Conflict) else 400,{"error":str(exc)})
        except (UnicodeError,json.JSONDecodeError):
            self.reply(400,{"error":"Некорректный JSON"})
        except Exception:
            self.reply(500,{"error":"Внутренняя ошибка. Подробности не передаются клиенту."})
            raise

if __name__=="__main__":
    print(f"SQLite LAB ONLY: http://localhost:{PORT}/ ; db={DB} ; production Excel is untouched",flush=True)
    server=ThreadingHTTPServer((HOST,PORT),Handler)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();store.close()
