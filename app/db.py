# -*- coding: utf-8 -*-
"""
Слой доступа к данным и безопасному хранилищу. Во время работы SQLite находится
локально на компьютере; закрытая мастер-копия БД, документы и резервные копии
хранятся в папке data рядом с программой, которую можно синхронизировать облаком.

Модель данных:
  purchases            — контракт: площадка, заказчик, деньги, сроки, статусы,
                          ответственные лица (внутреннее имя таблицы не меняем —
                          пользователь видит только «Контракт» в интерфейсе)
  purchase_items       — товарные позиции внутри контракта
  stock_receipts       — приход товара на склад
  attachments           — файлы, прикреплённые к контракту
  competitor_records   — записи для модуля «Анализ конкурентов»
"""
import glob
import getpass
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import statistics
import sys
import re
import unicodedata
import tempfile
import uuid
import zipfile
from datetime import date, datetime, timedelta

# ---- Поля "карточки" контракта (без товаров — они в purchase_items) ----
HEADER_FIELDS = [
    "platform", "customer", "contract_no", "registry_record", "contract_date", "law",
    "contract_sum", "purchase_cost", "logistics", "commission", "other_costs",
    "guarantee", "contract_status", "sign_deadline", "deadline", "handover_date",
    "payment_status", "payment_deadline", "exec_status",
    "resp_purchase_name", "resp_purchase_phone", "resp_purchase_email",
    "resp_receiving_name", "resp_receiving_phone", "resp_receiving_email",
    "note", "created_at",
]
ITEM_FIELDS = ["product", "qty"]
RECEIPT_FIELDS = ["product", "qty", "unit_cost", "receipt_date", "supplier", "note"]
ATTACHMENT_FIELDS = ["filename", "stored_path", "added_date", "category", "note"]
COMPETITOR_FIELDS = ["competitor", "product", "trade_type", "qty", "unit_price", "purchase_date"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,
    customer TEXT,
    contract_no TEXT,
    registry_record TEXT,
    contract_date TEXT,
    law TEXT,
    contract_sum REAL,
    purchase_cost REAL,
    logistics REAL,
    commission REAL,
    other_costs REAL,
    guarantee REAL,
    contract_status TEXT,
    sign_deadline TEXT,
    deadline TEXT,
    handover_date TEXT,
    payment_status TEXT,
    payment_deadline TEXT,
    exec_status TEXT,
    resp_purchase_name TEXT,
    resp_purchase_phone TEXT,
    resp_purchase_email TEXT,
    resp_receiving_name TEXT,
    resp_receiving_phone TEXT,
    resp_receiving_email TEXT,
    note TEXT,
    created_at TEXT,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS purchase_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id INTEGER NOT NULL,
    product TEXT,
    qty REAL
);

CREATE TABLE IF NOT EXISTS stock_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT,
    qty REAL,
    unit_cost REAL,
    receipt_date TEXT,
    supplier TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id INTEGER NOT NULL,
    filename TEXT,
    stored_path TEXT,
    added_date TEXT,
    category TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS competitor_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor TEXT,
    product TEXT,
    trade_type TEXT,
    qty REAL,
    unit_price REAL,
    purchase_date TEXT
);

CREATE TABLE IF NOT EXISTS calculator_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    qty REAL,
    cost REAL,
    logistics REAL,
    extra REAL,
    commission REAL,
    markup REAL,
    tax REAL
);

CREATE TABLE IF NOT EXISTS manual_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT,
    qty REAL,
    organization TEXT,
    reserved_date TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS product_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    normalized_key TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id INTEGER,
    event_at TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_purchases_active_date ON purchases(deleted_at, contract_date);
CREATE INDEX IF NOT EXISTS idx_purchases_active_exec ON purchases(deleted_at, exec_status);
CREATE INDEX IF NOT EXISTS idx_purchases_active_payment ON purchases(deleted_at, payment_status);
CREATE INDEX IF NOT EXISTS idx_purchases_deadline ON purchases(deleted_at, deadline);
CREATE INDEX IF NOT EXISTS idx_purchases_payment_deadline ON purchases(deleted_at, payment_deadline);
CREATE INDEX IF NOT EXISTS idx_purchase_items_purchase ON purchase_items(purchase_id);
CREATE INDEX IF NOT EXISTS idx_purchase_items_product ON purchase_items(product);
CREATE INDEX IF NOT EXISTS idx_stock_receipts_product ON stock_receipts(product);
CREATE INDEX IF NOT EXISTS idx_manual_reservations_product ON manual_reservations(product);
CREATE INDEX IF NOT EXISTS idx_attachments_purchase ON attachments(purchase_id);
CREATE INDEX IF NOT EXISTS idx_audit_purchase ON audit_log(purchase_id, event_at);
CREATE INDEX IF NOT EXISTS idx_product_catalog_key ON product_catalog(normalized_key);
"""

RESERVATION_FIELDS = ["product", "qty", "organization", "reserved_date", "note"]

CALCULATOR_FIELDS = ["name", "qty", "cost", "logistics", "extra", "commission", "markup", "tax"]

CALCULATOR_SAMPLES = [
    {"name": "Товар А (контракт 1)", "qty": 100, "cost": 1500, "logistics": 18000,
     "extra": 5000, "commission": 12000, "markup": 20, "tax": 7},
    {"name": "Товар Б (контракт 2)", "qty": 50, "cost": 4200, "logistics": 12000,
     "extra": 3500, "commission": 8000, "markup": 18, "tax": 7},
    {"name": "Товар В (крупная поставка)", "qty": 300, "cost": 850, "logistics": 25000,
     "extra": 8000, "commission": 15000, "markup": 12, "tax": 7},
    {"name": "Товар Г", "qty": 20, "cost": 6800, "logistics": 9000,
     "extra": 2000, "commission": 4500, "markup": 25, "tax": 7},
]

TRASH_KEEP_DAYS = 30  # сколько дней контракт лежит в корзине, прежде чем удалится навсегда

PLATFORM_OPTIONS = [
    "Сбербанк-АСТ", "РТС-Тендер", "ЕЭТП", "Национальная электронная площадка",
    "ЭТП ГПБ", "ТЭК-Торг", "ЭТП РАД", "Fabrikant", "B2B-Center",
    "ЕИС (zakupki.gov.ru)", "Другое",
]
LAW_OPTIONS = ["44-ФЗ", "223-ФЗ", "Коммерческая закупка"]
# По ТЗ: только три значения статуса контракта
CONTRACT_STATUS_OPTIONS = ["Формирование", "На подписи у Заказчика", "Заключен"]
PAYMENT_STATUS_OPTIONS = ["Не оплачено", "Оплачено"]
# По ТЗ: "Просрочено" больше не ручной статус — вычисляется автоматически по датам
EXEC_STATUS_OPTIONS = ["В процессе", "Отправлено", "Приемка Заказчиком",
                        "Подписан в ЕИС", "Исполнено"]

BACKUP_KEEP = 20


# ---------------------------------------------------------------- Подключение
def app_root_dir() -> str:
    """
    Папка, где реально расположена программа — используется как переносимое/облачное
    хранилище мастер-копии БД, документов и резервных копий. Открытая рабочая SQLite
    находится отдельно в local_data_dir().

    Для собранного .exe (PyInstaller) — это папка, где лежит сам .exe, а НЕ временная
    папка распаковки (sys._MEIPASS), в которую PyInstaller разворачивает программу при
    каждом запуске и которая существует только во время работы программы.
    Для обычного запуска как python-скрипта — папка, где лежит main.py.

    Можно переопределить переменной окружения ZAKUPKI_APP_ROOT (используется в тестах,
    чтобы не писать тестовые данные поверх реальной программы).
    """
    override = os.getenv("ZAKUPKI_APP_ROOT")
    if override:
        return override
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    try:
        return os.path.dirname(os.path.abspath(sys.modules["__main__"].__file__))
    except (KeyError, AttributeError):
        return os.getcwd()


def app_data_dir() -> str:
    """Облачная/переносимая папка данных рядом с программой."""
    folder = os.path.join(app_root_dir(), "data")
    os.makedirs(folder, exist_ok=True)
    return folder


def local_data_dir() -> str:
    """
    Локальная рабочая папка SQLite. Она намеренно НЕ лежит рядом с программой:
    облачный клиент не должен синхронизировать открытый SQLite-файл во время записи.

    Для тестов путь можно переопределить ZAKUPKI_LOCAL_DATA.
    """
    override = os.getenv("ZAKUPKI_LOCAL_DATA")
    if override:
        folder = override
    elif sys.platform.startswith("win"):
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        folder = os.path.join(base, "UchetZakupok")
    elif sys.platform == "darwin":
        folder = os.path.expanduser("~/Library/Application Support/UchetZakupok")
    else:
        base = os.getenv("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        folder = os.path.join(base, "UchetZakupok")
    os.makedirs(folder, exist_ok=True)
    return folder


def cloud_db_path() -> str:
    """Закрытая мастер-копия базы в папке программы/облачного диска."""
    return os.path.join(app_data_dir(), "zakupki.db")


def default_db_path() -> str:
    """Рабочая SQLite-база: всегда локальная, не синхронизируется облаком напрямую."""
    return os.path.join(local_data_dir(), "zakupki_work.db")


def local_log_path() -> str:
    return os.path.join(local_data_dir(), "app_debug.log")


def attachments_dir() -> str:
    # Документы остаются рядом с программой и синхронизируются обычным облачным клиентом.
    path = os.path.join(app_data_dir(), "attachments")
    os.makedirs(path, exist_ok=True)
    return path


def backup_dir() -> str:
    # Резервные копии специально находятся в облачной папке: они переживут поломку ПК.
    path = os.path.join(app_data_dir(), "backups")
    os.makedirs(path, exist_ok=True)
    return path


SYNC_PROTOCOL_VERSION = 1
LOCK_FILENAME = "app.lock"
CLOUD_STATE_FILENAME = "sync_state.json"
LOCAL_STATE_FILENAME = "sync_state.json"
LOCK_STALE_HOURS = 12


class CloudLockError(RuntimeError):
    def __init__(self, info):
        self.info = info or {}
        super().__init__("Программа уже используется на другом компьютере или предыдущий сеанс завершился некорректно.")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return {}


def _write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".tmp_{uuid.uuid4().hex}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sqlite_quick_check(path):
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return False, "Файл базы отсутствует или пуст."
    conn = None
    try:
        uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        row = conn.execute("PRAGMA quick_check").fetchone()
        ok = bool(row and row[0] == "ok")
        return ok, ("ok" if ok else (row[0] if row else "quick_check не вернул результат"))
    except sqlite3.Error as e:
        return False, str(e)
    finally:
        if conn is not None:
            conn.close()


def _atomic_copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + f".tmp_{uuid.uuid4().hex}"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def acquire_app_lock(force=False):
    """Создаёт lock-файл в облачной папке и возвращает уникальный токен этого запуска."""
    path = os.path.join(app_data_dir(), LOCK_FILENAME)
    existing = _read_json(path) if os.path.exists(path) else {}
    if existing and not force:
        heartbeat = existing.get("heartbeat_at") or existing.get("started_at")
        stale = False
        if heartbeat:
            try:
                stale = (datetime.now() - datetime.fromisoformat(heartbeat)).total_seconds() > LOCK_STALE_HOURS * 3600
            except ValueError:
                stale = False
        if not stale:
            raise CloudLockError(existing)

    token = uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    info = {
        "protocol": SYNC_PROTOCOL_VERSION,
        "token": token,
        "computer": socket.gethostname(),
        "user": getpass.getuser(),
        "pid": os.getpid(),
        "started_at": now,
        "heartbeat_at": now,
    }
    _write_json_atomic(path, info)
    return token


def refresh_app_lock(token):
    path = os.path.join(app_data_dir(), LOCK_FILENAME)
    info = _read_json(path)
    if not info or info.get("token") != token:
        return False
    info["heartbeat_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json_atomic(path, info)
    return True


def release_app_lock(token):
    path = os.path.join(app_data_dir(), LOCK_FILENAME)
    info = _read_json(path)
    if info and info.get("token") == token:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return True
    return False


def describe_lock(info):
    if not info:
        return "сведения о предыдущем запуске недоступны"
    return (f"компьютер: {info.get('computer') or '—'}; "
            f"пользователь: {info.get('user') or '—'}; "
            f"последняя активность: {info.get('heartbeat_at') or info.get('started_at') or '—'}")


def _cloud_state_path():
    return os.path.join(app_data_dir(), CLOUD_STATE_FILENAME)


def _local_state_path():
    return os.path.join(local_data_dir(), LOCAL_STATE_FILENAME)


def _write_sync_state(cloud_hash, local_hash=None):
    now = datetime.now().isoformat(timespec="seconds")
    common = {
        "protocol": SYNC_PROTOCOL_VERSION,
        "db_sha256": cloud_hash,
        "synced_at": now,
        "computer": socket.gethostname(),
    }
    _write_json_atomic(_cloud_state_path(), common)
    local = dict(common)
    local["cloud_path"] = cloud_db_path()
    local["local_sha256"] = local_hash or cloud_hash
    _write_json_atomic(_local_state_path(), local)


def _save_local_recovery_copy(path, label="unsynced"):
    if not path or not os.path.exists(path):
        return None
    folder = os.path.join(local_data_dir(), "recovery")
    os.makedirs(folder, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = os.path.join(folder, f"zakupki_{label}_{ts}.db")
    shutil.copy2(path, target)
    return target


def prepare_working_storage():
    """
    Готовит локальную рабочую БД перед открытием приложения.

    Возвращает словарь status/action/message. Функция не открывает SQLite и не
    перезаписывает повреждённую облачную базу — восстановление выполняется отдельно.
    """
    cloud = cloud_db_path()
    local = default_db_path()
    os.makedirs(os.path.dirname(local), exist_ok=True)

    cloud_exists = os.path.exists(cloud)
    local_exists = os.path.exists(local)

    if cloud_exists:
        ok, detail = _sqlite_quick_check(cloud)
        if not ok:
            local_valid = False
            if local_exists:
                local_valid, _ = _sqlite_quick_check(local)
            return {
                "status": "cloud_corrupt", "detail": detail, "cloud": cloud, "local": local,
                "local_valid": local_valid,
            }

        cloud_hash = _file_sha256(cloud)
        cloud_state = _read_json(_cloud_state_path())
        if cloud_state.get("protocol") == SYNC_PROTOCOL_VERSION:
            stated_hash = cloud_state.get("db_sha256")
            if stated_hash and stated_hash != cloud_hash:
                return {
                    "status": "cloud_sync_incomplete",
                    "detail": "Хэш базы не совпадает с маркером последней синхронизации.",
                    "cloud": cloud, "local": local,
                }
    else:
        cloud_hash = None

    if local_exists:
        ok, _detail = _sqlite_quick_check(local)
        if not ok:
            recovery = _save_local_recovery_copy(local, "corrupt")
            try:
                os.remove(local)
            except OSError:
                pass
            local_exists = False
        else:
            recovery = None
    else:
        recovery = None

    if cloud_exists and not local_exists:
        _atomic_copy(cloud, local)
        _write_json_atomic(_local_state_path(), {
            "protocol": SYNC_PROTOCOL_VERSION,
            "db_sha256": cloud_hash,
            "local_sha256": _file_sha256(local),
            "synced_at": datetime.now().isoformat(timespec="seconds"),
            "cloud_path": cloud,
        })
        return {"status": "ready", "action": "cloud_to_local", "recovery": recovery}

    if not cloud_exists and local_exists:
        return {"status": "ready", "action": "local_only", "recovery": recovery}

    if not cloud_exists and not local_exists:
        return {"status": "ready", "action": "new", "recovery": recovery}

    # Обе базы существуют. Определяем, какая изменилась после последней синхронизации.
    local_hash = _file_sha256(local)
    state = _read_json(_local_state_path())
    if state.get("protocol") == SYNC_PROTOCOL_VERSION:
        last_cloud_hash = state.get("db_sha256")
        last_local_hash = state.get("local_sha256") or last_cloud_hash
    else:
        last_cloud_hash = None
        last_local_hash = None

    if local_hash == cloud_hash:
        _write_json_atomic(_local_state_path(), {
            "protocol": SYNC_PROTOCOL_VERSION, "db_sha256": cloud_hash,
            "local_sha256": _file_sha256(local),
            "synced_at": datetime.now().isoformat(timespec="seconds"), "cloud_path": cloud,
        })
        return {"status": "ready", "action": "already_synced", "recovery": recovery}

    if last_cloud_hash and last_local_hash:
        if local_hash == last_local_hash and cloud_hash != last_cloud_hash:
            _atomic_copy(cloud, local)
            _write_json_atomic(_local_state_path(), {
                "protocol": SYNC_PROTOCOL_VERSION, "db_sha256": cloud_hash,
                "local_sha256": _file_sha256(local),
                "synced_at": datetime.now().isoformat(timespec="seconds"), "cloud_path": cloud,
            })
            return {"status": "ready", "action": "cloud_newer", "recovery": recovery}
        if cloud_hash == last_cloud_hash and local_hash != last_local_hash:
            # Предыдущий запуск, вероятно, завершился аварийно до выгрузки в облако.
            return {"status": "ready", "action": "local_unsynced", "recovery": recovery}

        # Изменились обе копии: сохраняем локальную в recovery и выбираем облачную.
        rec = _save_local_recovery_copy(local, "conflict")
        _atomic_copy(cloud, local)
        _write_json_atomic(_local_state_path(), {
            "protocol": SYNC_PROTOCOL_VERSION, "db_sha256": cloud_hash,
            "local_sha256": _file_sha256(local),
            "synced_at": datetime.now().isoformat(timespec="seconds"), "cloud_path": cloud,
        })
        return {"status": "ready", "action": "conflict_cloud_wins", "recovery": rec or recovery}

    # Первый запуск новой схемы: облачная база рядом с программой считается основной.
    rec = _save_local_recovery_copy(local, "pre_migration")
    _atomic_copy(cloud, local)
    _write_json_atomic(_local_state_path(), {
        "protocol": SYNC_PROTOCOL_VERSION, "db_sha256": cloud_hash,
        "local_sha256": _file_sha256(local),
        "synced_at": datetime.now().isoformat(timespec="seconds"), "cloud_path": cloud,
    })
    return {"status": "ready", "action": "first_cloud_import", "recovery": rec or recovery}


def sync_working_to_cloud(conn=None):
    """Безопасно синхронизирует локальную SQLite в закрытую мастер-копию облачной папки."""
    local = default_db_path()
    if not os.path.exists(local) and conn is None:
        return {"changed": False, "reason": "local_missing"}

    snapshot = os.path.join(local_data_dir(), f"sync_snapshot_{uuid.uuid4().hex}.db")
    try:
        if conn is not None:
            conn.commit()
            dest_conn = sqlite3.connect(snapshot)
            try:
                conn.backup(dest_conn)
            finally:
                dest_conn.close()
        else:
            shutil.copy2(local, snapshot)

        ok, detail = _sqlite_quick_check(snapshot)
        if not ok:
            raise sqlite3.DatabaseError(f"Локальная база не прошла проверку целостности: {detail}")

        new_hash = _file_sha256(snapshot)
        cloud = cloud_db_path()
        old_hash = _file_sha256(cloud) if os.path.exists(cloud) else None
        if old_hash != new_hash:
            _atomic_copy(snapshot, cloud)
        local_actual_hash = _file_sha256(local) if os.path.exists(local) else new_hash
        _write_sync_state(new_hash, local_actual_hash)
        return {
            "changed": old_hash != new_hash,
            "hash": new_hash,
            "synced_at": datetime.now(),
            "cloud": cloud,
        }
    finally:
        try:
            os.remove(snapshot)
        except OSError:
            pass


def repair_cloud_from_local():
    """Восстанавливает повреждённую облачную мастер-копию из исправной локальной базы."""
    local = default_db_path()
    ok, detail = _sqlite_quick_check(local)
    if not ok:
        raise sqlite3.DatabaseError(f"Локальная база тоже повреждена: {detail}")
    cloud = cloud_db_path()
    preserved = _save_local_recovery_copy(cloud, "cloud_corrupt") if os.path.exists(cloud) else None
    result = sync_working_to_cloud(None)
    result["preserved_corrupt_cloud"] = preserved
    return result


def find_latest_valid_backup():
    """Находит самый свежий бэкап, внутри которого SQLite проходит quick_check."""
    for path, _mtime in list_backups():
        temp_db = None
        try:
            if path.lower().endswith(".zip"):
                with zipfile.ZipFile(path, "r") as zf:
                    if "zakupki.db" not in zf.namelist():
                        continue
                    fd, temp_db = tempfile.mkstemp(prefix="zakupki_restore_", suffix=".db", dir=local_data_dir())
                    os.close(fd)
                    with zf.open("zakupki.db") as src, open(temp_db, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            else:
                temp_db = path
            ok, _ = _sqlite_quick_check(temp_db)
            if ok:
                return path
        except (OSError, zipfile.BadZipFile):
            continue
        finally:
            if temp_db and temp_db != path:
                try:
                    os.remove(temp_db)
                except OSError:
                    pass
    return None


def recover_database_from_backup(backup_path):
    """
    Аварийное восстановление БД. Документы не удаляем: это безопаснее при сбое базы,
    потому что более новые файлы-вложения могут ещё существовать в облачной папке.
    """
    local = default_db_path()
    cloud = cloud_db_path()
    temp_db = os.path.join(local_data_dir(), f"recovered_{uuid.uuid4().hex}.db")
    try:
        if backup_path.lower().endswith(".zip"):
            with zipfile.ZipFile(backup_path, "r") as zf:
                with zf.open("zakupki.db") as src, open(temp_db, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            shutil.copy2(backup_path, temp_db)
        ok, detail = _sqlite_quick_check(temp_db)
        if not ok:
            raise sqlite3.DatabaseError(f"Резервная база повреждена: {detail}")
        _atomic_copy(temp_db, local)
        _atomic_copy(temp_db, cloud)
        recovered_hash = _file_sha256(temp_db)
        _write_sync_state(recovered_hash, _file_sha256(local))
    finally:
        try:
            os.remove(temp_db)
        except OSError:
            pass


def storage_status():
    cloud = cloud_db_path()
    local = default_db_path()
    state = _read_json(_local_state_path())
    return {
        "cloud_path": cloud,
        "local_path": local,
        "last_sync": state.get("synced_at"),
        "cloud_exists": os.path.exists(cloud),
        "local_exists": os.path.exists(local),
    }


def make_attachment_relative(stored_path, purchase_id=None, filename=None):
    """Преобразует путь вложения в переносимый вид attachments/<id>/<file>."""
    if not stored_path:
        if purchase_id and filename:
            return os.path.join("attachments", str(purchase_id), filename)
        return stored_path
    text = str(stored_path).replace("\\", "/")
    lower = text.lower()
    marker = "/attachments/"
    idx = lower.rfind(marker)
    if idx >= 0:
        return text[idx + 1:].replace("/", os.sep)
    if lower.startswith("attachments/"):
        return text.replace("/", os.sep)
    try:
        rel = os.path.relpath(os.path.abspath(stored_path), app_data_dir())
        if not rel.startswith("..") and rel.lower().startswith("attachments" + os.sep):
            return rel
    except (OSError, ValueError):
        pass
    if purchase_id and filename:
        expected = os.path.join(attachments_dir(), str(purchase_id), filename)
        if os.path.exists(expected):
            return os.path.join("attachments", str(purchase_id), filename)
    return stored_path


def resolve_attachment_path(stored_path, purchase_id=None, filename=None):
    if not stored_path:
        return None
    path = str(stored_path)
    if not os.path.isabs(path):
        return os.path.normpath(os.path.join(app_data_dir(), path))
    if os.path.exists(path):
        return path
    portable = make_attachment_relative(path, purchase_id, filename)
    if portable and not os.path.isabs(portable):
        return os.path.normpath(os.path.join(app_data_dir(), portable))
    if purchase_id and filename:
        return os.path.join(attachments_dir(), str(purchase_id), filename)
    return path


def migrate_attachment_paths(conn):
    """Переводит старые абсолютные пути вложений в переносимые относительные пути."""
    rows = conn.execute("SELECT id, purchase_id, filename, stored_path FROM attachments").fetchall()
    changed = 0
    for row in rows:
        new_path = make_attachment_relative(row["stored_path"], row["purchase_id"], row["filename"])
        if new_path and new_path != row["stored_path"]:
            conn.execute("UPDATE attachments SET stored_path = ? WHERE id = ?", (new_path, row["id"]))
            changed += 1
    if changed:
        conn.commit()
    return changed


# Типы столбцов для автоматической миграции старых баз (см. _migrate_schema).
# Должны соответствовать актуальному СOZDATь TABLE в SCHEMA выше.
_PURCHASES_COLUMN_TYPES = {
    "platform": "TEXT", "customer": "TEXT", "contract_no": "TEXT", "registry_record": "TEXT", "contract_date": "TEXT",
    "law": "TEXT", "contract_sum": "REAL", "purchase_cost": "REAL", "logistics": "REAL",
    "commission": "REAL", "other_costs": "REAL", "guarantee": "REAL", "contract_status": "TEXT",
    "sign_deadline": "TEXT", "deadline": "TEXT", "handover_date": "TEXT", "payment_status": "TEXT",
    "payment_deadline": "TEXT", "exec_status": "TEXT",
    "resp_purchase_name": "TEXT", "resp_purchase_phone": "TEXT", "resp_purchase_email": "TEXT",
    "resp_receiving_name": "TEXT", "resp_receiving_phone": "TEXT", "resp_receiving_email": "TEXT",
    "note": "TEXT", "created_at": "TEXT", "deleted_at": "TEXT",
}
_STOCK_RECEIPTS_COLUMN_TYPES = {
    "product": "TEXT", "qty": "REAL", "unit_cost": "REAL", "receipt_date": "TEXT",
    "supplier": "TEXT", "note": "TEXT",
}
_ATTACHMENTS_COLUMN_TYPES = {
    "purchase_id": "INTEGER", "filename": "TEXT", "stored_path": "TEXT",
    "added_date": "TEXT", "category": "TEXT", "note": "TEXT",
}
_COMPETITOR_COLUMN_TYPES = {
    "competitor": "TEXT", "product": "TEXT", "trade_type": "TEXT",
    "qty": "REAL", "unit_price": "REAL", "purchase_date": "TEXT",
}
_PURCHASE_ITEMS_COLUMN_TYPES = {"purchase_id": "INTEGER", "product": "TEXT", "qty": "REAL"}


def _migrate_schema(conn: sqlite3.Connection):
    """
    Если файл БД остался от более старой версии приложения (структура таблиц менялась
    несколько раз), сама структура таблиц (CREATE TABLE IF NOT EXISTS) НЕ добавляет новые
    столбцы в уже существующие таблицы — из-за этого чтение старых данных падало с
    AttributeError/KeyError на отсутствующих полях (например, payment_deadline,
    resp_purchase_name), а карточка контракта переставала открываться корректно.
    Здесь недостающие столбцы добавляются автоматически через ALTER TABLE, без каких-либо
    действий от пользователя и без потери уже сохранённых данных.
    """
    tables = {
        "purchases": _PURCHASES_COLUMN_TYPES,
        "stock_receipts": _STOCK_RECEIPTS_COLUMN_TYPES,
        "attachments": _ATTACHMENTS_COLUMN_TYPES,
        "competitor_records": _COMPETITOR_COLUMN_TYPES,
        "purchase_items": _PURCHASE_ITEMS_COLUMN_TYPES,
    }
    for table, columns in tables.items():
        try:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.OperationalError:
            continue  # таблицы ещё нет — её создаст SCHEMA при следующем execute
        for col, col_type in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    # Для старых записей без даты заведения используем дату заключения,
    # а если её нет — дату текущей миграции.
    try:
        conn.execute(
            "UPDATE purchases SET created_at = COALESCE(created_at, contract_date, datetime('now')) "
            "WHERE created_at IS NULL OR created_at = ''"
        )
    except sqlite3.OperationalError:
        pass
    # Индекс нового поля создаём только ПОСЛЕ миграции старой базы, иначе
    # CREATE INDEX упадёт на БД предыдущей версии, где столбца ещё нет.
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_purchases_registry_record ON purchases(registry_record)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_purchases_sign_deadline ON purchases(deleted_at, sign_deadline)")
        conn.execute("UPDATE purchases SET payment_status='Не оплачено' WHERE payment_status='Частично оплачено'")
        _sync_product_catalog(conn)
    except sqlite3.OperationalError:
        pass
    conn.commit()


def get_connection(db_path: str = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='calculator_rows'"
    )
    is_new_db = cur.fetchone() is None
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_schema(conn)
    if is_new_db:
        # Примеры добавляются ОДИН РАЗ, только при самом первом создании базы —
        # если пользователь потом сам очистит калькулятор, они не должны появляться снова.
        for sample in CALCULATOR_SAMPLES:
            insert_calculator_row(conn, sample)
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else default


def set_setting(conn: sqlite3.Connection, key: str, value):
    conn.execute(
        "INSERT INTO app_settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, None if value is None else str(value)),
    )
    conn.commit()



# ---------------------------------------------------------------- Резервные копии
def create_backup(db_path: str = None) -> str:
    """
    Полная резервная копия: файл базы данных + вся папка вложений (attachments),
    упакованные в один ZIP-архив. Раньше копировался только файл базы — документы,
    прикреплённые к контрактам, в бэкап не попадали.
    """
    db_path = db_path or default_db_path()
    if not os.path.exists(db_path):
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir(), f"zakupki_{ts}.zip")
    att_dir = attachments_dir()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_path, arcname="zakupki.db")
        if os.path.isdir(att_dir):
            for root, _dirs, files in os.walk(att_dir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    rel_path = os.path.join("attachments", os.path.relpath(full_path, att_dir))
                    zf.write(full_path, arcname=rel_path)
    _cleanup_old_backups()
    return dest


def _cleanup_old_backups(keep: int = BACKUP_KEEP):
    files = sorted(
        glob.glob(os.path.join(backup_dir(), "zakupki_*.zip"))
        + glob.glob(os.path.join(backup_dir(), "zakupki_*.db")),
        key=os.path.getmtime,
    )
    for f in (files[:-keep] if len(files) > keep else []):
        try:
            os.remove(f)
        except OSError:
            pass


def list_backups():
    """Список резервных копий (новые .zip с документами и старые .db-копии без них), новые сначала."""
    files = (
        glob.glob(os.path.join(backup_dir(), "zakupki_*.zip"))
        + glob.glob(os.path.join(backup_dir(), "zakupki_*.db"))
    )
    files.sort(key=os.path.getmtime, reverse=True)
    return [(f, datetime.fromtimestamp(os.path.getmtime(f))) for f in files]


def restore_backup(backup_path: str, db_path: str = None):
    """
    Восстанавливает базу данных (и, если это новый .zip-бэкап, вложения) из резервной
    копии. Старые .db-бэкапы (созданные до этого обновления) тоже поддерживаются —
    они восстанавливают только базу, без документов, как и раньше.
    """
    db_path = db_path or default_db_path()
    if backup_path.lower().endswith(".zip"):
        att_dir = attachments_dir()
        with zipfile.ZipFile(backup_path, "r") as zf:
            names = zf.namelist()
            with zf.open("zakupki.db") as src, open(db_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if os.path.isdir(att_dir):
                shutil.rmtree(att_dir)
            os.makedirs(att_dir, exist_ok=True)
            for name in names:
                if name.startswith("attachments/") and not name.endswith("/"):
                    target = os.path.join(app_data_dir(), name)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        # Старый формат бэкапа (просто .db, без документов)
        shutil.copy2(backup_path, db_path)


def auto_backup_if_needed(db_path: str = None, min_interval_hours: int = 24) -> bool:
    backups = list_backups()
    if backups:
        age_hours = (datetime.now() - backups[0][1]).total_seconds() / 3600
        if age_hours < min_interval_hours:
            return False
    return create_backup(db_path) is not None


# ---------------------------------------------------------------- Справочник товаров / аудит

DOCUMENT_CATEGORIES = [
    "Контракт", "Спецификация", "УПД / накладная", "Акт",
    "Счёт", "Платёжный документ", "Переписка", "Прочее",
]

_AUDIT_LABELS = {
    "customer": "Заказчик", "contract_no": "Номер контракта", "registry_record": "Реестровая запись",
    "contract_date": "Дата заключения", "contract_sum": "Сумма контракта",
    "purchase_cost": "Себестоимость", "logistics": "Логистика", "commission": "Комиссия",
    "other_costs": "Другие расходы", "guarantee": "Обеспечение", "contract_status": "Статус контракта",
    "sign_deadline": "Подписать до", "deadline": "Срок исполнения", "handover_date": "Дата вручения",
    "payment_status": "Оплата", "payment_deadline": "Срок оплаты", "exec_status": "Исполнение",
    "platform": "Площадка", "law": "Закон", "note": "Примечание", "created_at": "Дата",
}

def normalize_product_key(name: str) -> str:
    text = unicodedata.normalize("NFKC", str(name or "")).strip().casefold().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    # Пробелы около пунктуации не должны создавать отдельный товар.
    text = re.sub(r"\s*([./,+()])\s*", r"\1", text)
    return text

def _sync_product_catalog(conn: sqlite3.Connection):
    sources = [
        "SELECT product AS name FROM purchase_items",
        "SELECT product AS name FROM stock_receipts",
        "SELECT product AS name FROM manual_reservations",
        "SELECT product AS name FROM competitor_records",
    ]
    for sql in sources:
        try:
            for row in conn.execute(sql).fetchall():
                name = (row["name"] or "").strip()
                if name:
                    ensure_product(conn, name, commit=False)
        except sqlite3.OperationalError:
            pass
    conn.commit()

def ensure_product(conn: sqlite3.Connection, name: str, *, commit: bool = True) -> str:
    name = " ".join(str(name or "").split()).strip()
    if not name:
        return ""
    key = normalize_product_key(name)
    row = conn.execute("SELECT name FROM product_catalog WHERE normalized_key = ?", (key,)).fetchone()
    if row:
        return row["name"]
    conn.execute(
        "INSERT OR IGNORE INTO product_catalog(name, normalized_key, active, created_at) VALUES(?,?,1,?)",
        (name, key, datetime.now().isoformat(timespec="seconds")),
    )
    if commit:
        conn.commit()
    row = conn.execute("SELECT name FROM product_catalog WHERE normalized_key = ?", (key,)).fetchone()