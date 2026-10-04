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
    "note", "created_at", "stock_written_off",
]
ITEM_FIELDS = ["product", "qty"]
RECEIPT_FIELDS = ["product", "qty", "unit_cost", "receipt_date", "supplier", "note"]
ATTACHMENT_FIELDS = ["filename", "stored_path", "added_date", "category", "note"]
COMPETITOR_FIELDS = ["competitor", "competitor_inn", "product", "trade_type", "qty", "unit_price", "purchase_date"]

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
    deleted_at TEXT,
    stock_written_off INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS purchase_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id INTEGER NOT NULL,
    product TEXT,
    qty REAL,
    supply_mode TEXT NOT NULL DEFAULT 'Со склада',
    stock_qty REAL NOT NULL DEFAULT 0,
    procurement_reminder_days INTEGER NOT NULL DEFAULT 30,
    procurement_status TEXT NOT NULL DEFAULT 'Не начата'
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
    competitor_inn TEXT,
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

CREATE TABLE IF NOT EXISTS stock_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at TEXT NOT NULL,
    product TEXT NOT NULL,
    action TEXT NOT NULL,
    qty REAL,
    purchase_id INTEGER,
    counterparty TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_stock_audit_product ON stock_audit(product, event_at);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS monthly_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_date TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Прочее',
    amount REAL NOT NULL DEFAULT 0,
    description TEXT
);
CREATE INDEX IF NOT EXISTS idx_monthly_expenses_date ON monthly_expenses(expense_date);

CREATE TABLE IF NOT EXISTS tax_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    effective_from TEXT NOT NULL,
    regime TEXT NOT NULL DEFAULT 'С доходов',
    rate REAL NOT NULL DEFAULT 7.0
);
CREATE INDEX IF NOT EXISTS idx_tax_profiles_effective ON tax_profiles(effective_from);

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
EXEC_STATUS_OPTIONS = ["В процессе", "Отправлено", "Вручен", "Исполнено"]
SUPPLY_MODE_OPTIONS = ["Со склада", "Требуется закупка", "Отложенная закупка"]
PROCUREMENT_STATUS_OPTIONS = ["Не начата", "Заказано"]

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

        # Изменились обе копии. Никогда не заменяем локальную рабочую БД автоматически:
        # именно она могла содержать последние введённые контракты. Обе копии сохраняем
        # в recovery, а приложение продолжает работать с локальной и требует ручного разбора.
        rec_local = _save_local_recovery_copy(local, "conflict_local")
        rec_cloud = _save_local_recovery_copy(cloud, "conflict_cloud")
        return {
            "status": "ready", "action": "conflict_local_preserved",
            "recovery": rec_local or recovery, "cloud_recovery": rec_cloud,
        }

    # Первый запуск схемы синхронизации при наличии двух разных исправных БД.
    # Без истории синхронизации невозможно безопасно решить, какая новее:
    # сохраняем обе и НЕ перезаписываем локальную.
    rec_local = _save_local_recovery_copy(local, "pre_migration_local")
    rec_cloud = _save_local_recovery_copy(cloud, "pre_migration_cloud")
    return {
        "status": "ready", "action": "first_local_preserved",
        "recovery": rec_local or recovery, "cloud_recovery": rec_cloud,
    }


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
    "stock_written_off": "INTEGER NOT NULL DEFAULT 0",
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
    "competitor": "TEXT", "competitor_inn": "TEXT", "product": "TEXT", "trade_type": "TEXT",
    "qty": "REAL", "unit_price": "REAL", "purchase_date": "TEXT",
}
_PURCHASE_ITEMS_COLUMN_TYPES = {
    "purchase_id": "INTEGER", "product": "TEXT", "qty": "REAL",
    "supply_mode": "TEXT NOT NULL DEFAULT 'Со склада'",
    "stock_qty": "REAL NOT NULL DEFAULT 0",
    "procurement_reminder_days": "INTEGER NOT NULL DEFAULT 30",
    "procurement_status": "TEXT NOT NULL DEFAULT 'Не начата'",
}


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
        # v2.17: отдельной приемки больше нет. Старые этапы приводим к новой цепочке.
        conn.execute("UPDATE purchases SET exec_status='Вручен' WHERE exec_status='Приемка Заказчиком'")
        conn.execute("UPDATE purchases SET exec_status='Исполнено' WHERE exec_status='Подписан в ЕИС'")
        # Фиксируем факт складского списания отдельно от текущего статуса:
        # после «Вручен»/«Исполнено» товар не должен возвращаться на склад.
        conn.execute("""UPDATE purchases SET stock_written_off=1
                        WHERE exec_status IN ('Отправлено','Вручен','Исполнено')
                           OR (handover_date IS NOT NULL AND handover_date<>'')""")
        # Старые позиции до v2.17 считались полностью обеспеченными складом.
        conn.execute("UPDATE purchase_items SET supply_mode=COALESCE(NULLIF(supply_mode,''),'Со склада')")
        conn.execute("UPDATE purchase_items SET stock_qty=qty WHERE supply_mode='Со склада' AND COALESCE(stock_qty,0)=0")
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
    return row["name"] if row else name

def catalog_products(conn: sqlite3.Connection):
    _sync_product_catalog(conn)
    rows = conn.execute("SELECT name FROM product_catalog WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()
    return [r["name"] for r in rows]

def rename_product(conn: sqlite3.Connection, old_name: str, new_name: str):
    old_name = str(old_name or "").strip()
    new_name = " ".join(str(new_name or "").split()).strip()
    if not old_name or not new_name:
        raise ValueError("Название товара не может быть пустым")
    old_key = normalize_product_key(old_name)
    new_key = normalize_product_key(new_name)
    if old_key == new_key:
        canonical = new_name
        conn.execute("UPDATE product_catalog SET name=? WHERE normalized_key=?", (canonical, old_key))
    else:
        canonical = ensure_product(conn, new_name, commit=False)
    for table in ("purchase_items", "stock_receipts", "manual_reservations", "competitor_records"):
        conn.execute(f"UPDATE {table} SET product=? WHERE product=?", (canonical, old_name))
    if old_key != new_key:
        conn.execute("DELETE FROM product_catalog WHERE normalized_key=?", (old_key,))
    conn.commit()
    return canonical


def add_audit(conn: sqlite3.Connection, purchase_id, action: str, details: str = ""):
    conn.execute(
        "INSERT INTO audit_log(purchase_id, event_at, action, details) VALUES(?,?,?,?)",
        (purchase_id, datetime.now().isoformat(timespec="seconds"), action, details or ""),
    )

def add_stock_audit(conn: sqlite3.Connection, product: str, action: str, qty=None,
                    purchase_id=None, counterparty: str = "", details: str = ""):
    product = str(product or "").strip()
    if not product:
        return
    conn.execute(
        """INSERT INTO stock_audit(event_at, product, action, qty, purchase_id, counterparty, details)
           VALUES(?,?,?,?,?,?,?)""",
        (datetime.now().isoformat(timespec="seconds"), product, action, qty, purchase_id,
         counterparty or "", details or ""),
    )


def fetch_stock_audit(conn: sqlite3.Connection, product: str):
    return conn.execute(
        "SELECT * FROM stock_audit WHERE product=? ORDER BY id DESC", (product,)
    ).fetchall()


def fetch_audit(conn: sqlite3.Connection, purchase_id: int):
    return conn.execute(
        "SELECT * FROM audit_log WHERE purchase_id=? ORDER BY id DESC", (purchase_id,)
    ).fetchall()

def duplicate_contracts(conn: sqlite3.Connection, contract_no: str, exclude_id=None):
    target = str(contract_no or "").strip().casefold()
    if not target:
        return []
    rows = conn.execute(
        "SELECT id, contract_no, contract_date, customer "
        "FROM purchases WHERE deleted_at IS NULL AND trim(COALESCE(contract_no, '')) <> '' "
        "ORDER BY id"
    ).fetchall()
    excluded = int(exclude_id) if exclude_id is not None else None
    return [
        row for row in rows
        if (excluded is None or int(row["id"]) != excluded)
        and str(row["contract_no"] or "").strip().casefold() == target
    ]

def _audit_changes(old_row, old_items, header, items):
    changes=[]
    if old_row is not None:
        for key in HEADER_FIELDS:
            old = old_row[key] if key in old_row.keys() else None
            new = header.get(key)
            if (old or None) != (new or None):
                label=_AUDIT_LABELS.get(key,key)
                changes.append(f"{label}: {old or '—'} → {new or '—'}")
    old_pairs=[(x["product"], float(x["qty"] or 0)) for x in old_items or []]
    new_pairs=[(x.get("product"), float(x.get("qty") or 0)) for x in items or []]
    if old_pairs != new_pairs:
        def fmt(pairs): return "; ".join(f"{p} — {q:g}" for p,q in pairs) or "—"
        changes.append(f"Товары: {fmt(old_pairs)} → {fmt(new_pairs)}")
    return changes

# ---------------------------------------------------------------- Контракты (шапка + позиции)
def _normalize_execution_after_payment(header: dict, old=None):
    """Единое бизнес-правило v2.23.

    «Вручен» не означает «Исполнено». Исполнение наступает только когда товар
    уже вручен заказчику И заказчик оплатил контракт.
    """
    header = dict(header)
    exec_status = (header.get("exec_status") or "").strip()
    payment_status = (header.get("payment_status") or "").strip()
    handover_date = header.get("handover_date")

    delivered = bool(handover_date) or exec_status in ("Вручен", "Исполнено")
    if payment_status == "Оплачено" and delivered:
        header["exec_status"] = "Исполнено"
    elif exec_status == "Исполнено" and payment_status != "Оплачено":
        # Не позволяем вручную завершить неоплаченный контракт.
        header["exec_status"] = "Вручен" if delivered else (old["exec_status"] if old is not None else "В процессе")
    return header


def insert_purchase(conn: sqlite3.Connection, header: dict, items: list) -> int:
    header = _normalize_execution_after_payment(dict(header))
    header["created_at"] = header.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header["stock_written_off"] = int(header.get("stock_written_off") or 0)
    cols = ", ".join(HEADER_FIELDS)
    placeholders = ", ".join(["?"] * len(HEADER_FIELDS))
    values = [header.get(f) for f in HEADER_FIELDS]
    cur = conn.execute(f"INSERT INTO purchases ({cols}) VALUES ({placeholders})", values)
    purchase_id = cur.lastrowid
    _insert_items(conn, purchase_id, items)
    add_audit(conn, purchase_id, "Создан контракт", f"№ {header.get('contract_no') or '—'}")
    for item in items or []:
        product = item.get("product") if hasattr(item, "get") else None
        qty = float(item.get("qty") or 0) if hasattr(item, "get") else 0.0
        stock_qty = float(item.get("stock_qty") or 0) if hasattr(item, "get") else 0.0
        if stock_qty > 0 and product:
            add_audit(conn, purchase_id, "Резерв склада", f"{product}: зарезервировано {stock_qty:g} шт.")
            add_stock_audit(
                conn, product, "Резерв под контракт", stock_qty, purchase_id=purchase_id,
                counterparty=header.get("customer") or "",
                details=f"Контракт № {header.get('contract_no') or '—'}"
            )
        if hasattr(item, "get") and (item.get("procurement_status") or "") == "Заказано":
            need = max(0.0, qty - stock_qty)
            add_audit(conn, purchase_id, "Закупка заказана", f"{product or '—'}: ожидается {need:g} шт.")
            if product and need > 0:
                add_stock_audit(
                    conn, product, "Ожидается поступление", need, purchase_id=purchase_id,
                    counterparty=header.get("customer") or "",
                    details=f"Контракт № {header.get('contract_no') or '—'}; товар заказан"
                )
    conn.commit()
    return purchase_id


def _has_key(item, key):
    if hasattr(item, "keys"):
        return key in item.keys()
    return False


def _insert_items(conn: sqlite3.Connection, purchase_id: int, items: list):
    for item in items:
        product = item["product"] if _has_key(item, "product") else None
        qty = item["qty"] if _has_key(item, "qty") else None
        if not product:
            continue
        product = ensure_product(conn, product, commit=False)
        mode = (item["supply_mode"] if _has_key(item, "supply_mode") else "Со склада") or "Со склада"
        stock_qty = item["stock_qty"] if _has_key(item, "stock_qty") else (qty if mode == "Со склада" else 0)
        reminder_days = item["procurement_reminder_days"] if _has_key(item, "procurement_reminder_days") else 30
        procurement_status = (item["procurement_status"] if _has_key(item, "procurement_status") else "Не начата") or "Не начата"
        if mode == "Со склада":
            procurement_status = "Не начата"
        stock_qty = max(0.0, min(float(stock_qty or 0), float(qty or 0)))
        conn.execute(
            """INSERT INTO purchase_items
               (purchase_id, product, qty, supply_mode, stock_qty, procurement_reminder_days, procurement_status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (purchase_id, product, qty, mode, stock_qty, int(reminder_days or 30), procurement_status),
        )


def update_purchase(conn: sqlite3.Connection, purchase_id: int, header: dict, items: list):
    header = dict(header)
    old = conn.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
    header = _normalize_execution_after_payment(header, old=old)
    old_items = fetch_items(conn, purchase_id)
    header["created_at"] = (header.get("created_at")
                             or (old["created_at"] if old else None)
                             or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    old_written_off = int(old["stock_written_off"] or 0) if old and "stock_written_off" in old.keys() else 0
    if (header.get("exec_status") or "") == "Отправлено":
        header["stock_written_off"] = 1
    else:
        header["stock_written_off"] = max(old_written_off, int(header.get("stock_written_off") or 0))

    def item_map(seq):
        out = {}
        for x in seq or []:
            get = x.get if hasattr(x, "get") else (lambda k, default=None: x[k] if k in x.keys() else default)
            product = str(get("product", "") or "").strip()
            if not product:
                continue
            row = out.setdefault(product, {"qty": 0.0, "stock_qty": 0.0, "procurement_status": "Не начата"})
            row["qty"] += float(get("qty", 0) or 0)
            row["stock_qty"] += float(get("stock_qty", 0) or 0)
            status = get("procurement_status", "Не начата") or "Не начата"
            if status == "Заказано":
                row["procurement_status"] = "Заказано"
        return out

    before_items = item_map(old_items)
    after_items = item_map(items)
    changes = _audit_changes(old, old_items, header, items)

    set_clause = ", ".join(f"{f} = ?" for f in HEADER_FIELDS)
    values = [header.get(f) for f in HEADER_FIELDS] + [purchase_id]
    conn.execute(f"UPDATE purchases SET {set_clause} WHERE id = ?", values)
    conn.execute("DELETE FROM purchase_items WHERE purchase_id = ?", (purchase_id,))
    _insert_items(conn, purchase_id, items)

    if changes:
        add_audit(conn, purchase_id, "Изменён контракт", "\n".join(changes))

    contract_no = header.get("contract_no") or (old["contract_no"] if old else None) or "—"
    customer = header.get("customer") or (old["customer"] if old else None) or ""

    if old is not None:
        old_contract_status = old["contract_status"] or ""
        new_contract_status = header.get("contract_status") or ""
        if old_contract_status != new_contract_status:
            add_audit(conn, purchase_id, "Статус контракта",
                      f"{old_contract_status or '—'} → {new_contract_status or '—'}")

        old_exec = old["exec_status"] or ""
        new_exec = header.get("exec_status") or ""
        if old_exec != new_exec:
            action = {
                "Отправлено": "Товар отправлен",
                "Вручен": "Товар вручен",
                "Исполнено": "Контракт исполнен",
            }.get(new_exec, "Статус исполнения")
            add_audit(conn, purchase_id, action, f"{old_exec or '—'} → {new_exec or '—'}")

        old_payment = old["payment_status"] or ""
        new_payment = header.get("payment_status") or ""
        if old_payment != new_payment:
            add_audit(
                conn, purchase_id,
                "Оплата получена" if new_payment == "Оплачено" else "Статус оплаты",
                f"{old_payment or '—'} → {new_payment or '—'}"
            )

    new_written_off = int(header.get("stock_written_off") or 0)
    if old_written_off == 0 and new_written_off == 1:
        for product, state in after_items.items():
            stock_qty = float(state.get("stock_qty") or 0)
            if stock_qty <= 0:
                continue
            add_audit(conn, purchase_id, "Списание со склада",
                      f"{product}: списано {stock_qty:g} шт. при отправке")
            add_stock_audit(
                conn, product, "Списание по контракту", -stock_qty,
                purchase_id=purchase_id, counterparty=customer,
                details=f"Контракт № {contract_no}; списание зафиксировано при статусе «Отправлено»"
            )

    for product in sorted(set(before_items) | set(after_items)):
        before = before_items.get(product, {"qty": 0.0, "stock_qty": 0.0, "procurement_status": "Не начата"})
        after = after_items.get(product, {"qty": 0.0, "stock_qty": 0.0, "procurement_status": "Не начата"})
        old_stock = float(before.get("stock_qty") or 0)
        new_stock = float(after.get("stock_qty") or 0)
        if old_stock != new_stock and not (old_written_off == 0 and new_written_off == 1):
            delta = new_stock - old_stock
            add_audit(conn, purchase_id, "Резерв склада",
                      f"{product}: {old_stock:g} → {new_stock:g} шт.")
            add_stock_audit(
                conn, product, "Резерв контракта изменён", delta,
                purchase_id=purchase_id, counterparty=customer,
                details=f"Контракт № {contract_no}; резерв {old_stock:g} → {new_stock:g} шт."
            )

        old_proc = before.get("procurement_status") or "Не начата"
        new_proc = after.get("procurement_status") or "Не начата"
        if old_proc != new_proc:
            need = max(0.0, float(after.get("qty") or 0) - new_stock)
            if new_proc == "Заказано":
                add_audit(conn, purchase_id, "Закупка заказана",
                          f"{product}: ожидается поступление {need:g} шт.")
                add_stock_audit(
                    conn, product, "Ожидается поступление", need,
                    purchase_id=purchase_id, counterparty=customer,
                    details=f"Контракт № {contract_no}; закупка отмечена как заказанная"
                )
            else:
                add_audit(conn, purchase_id, "Закупка возвращена в работу",
                          f"{product}: статус «{old_proc}» → «{new_proc}»")
                add_stock_audit(
                    conn, product, "Закупка возвращена в работу", None,
                    purchase_id=purchase_id, counterparty=customer,
                    details=f"Контракт № {contract_no}"
                )

    conn.commit()


def fetch_items(conn: sqlite3.Connection, purchase_id: int):
    return conn.execute(
        "SELECT * FROM purchase_items WHERE purchase_id = ? ORDER BY id", (purchase_id,)
    ).fetchall()


def _products_summary(items, limit=2):
    names = [i["product"] for i in items if i["product"]]
    if not names:
        return ""
    shown = ", ".join(names[:limit])
    if len(names) > limit:
        shown += f" (+{len(names) - limit} ещё)"
    return shown


def _qty_total(items):
    return sum((i["qty"] or 0.0) for i in items)


def _fetch_items_for_purchase_ids(conn: sqlite3.Connection, purchase_ids):
    """Загружает позиции сразу пачкой, без N+1 запроса на каждый контракт."""
    ids = [int(x) for x in purchase_ids]
    result = {pid: [] for pid in ids}
    if not ids:
        return result
    # SQLite обычно допускает 999+ bind-параметров; режем с запасом.
    for offset in range(0, len(ids), 800):
        chunk = ids[offset:offset + 800]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT * FROM purchase_items WHERE purchase_id IN ({marks}) ORDER BY purchase_id, id",
            chunk,
        ).fetchall()
        for item in rows:
            result.setdefault(int(item["purchase_id"]), []).append(item)
    return result


def fetch_all(conn: sqlite3.Connection, year: int = None, month: int = None, search: str = None,
              contract_status: str = None, payment_status: str = None, exec_status: str = None,
              order_by_today: bool = True, operational_period: bool = False):
    """
    Возвращает контракты и позиции. Год/месяц относятся к месяцу фактического
    добавления записи (created_at). Сам created_at остаётся техническим полем и
    в пользовательском интерфейсе не показывается.

    operational_period оставлен в сигнатуре для обратной совместимости со старым
    кодом, но больше не переносит активные контракты между месяцами.
    """
    query = "SELECT p.* FROM purchases p WHERE p.deleted_at IS NULL"
    params = []

    # created_at у новых записей хранится как YYYY-MM-DD HH:MM:SS. Для старых
    # баз поддерживаем DD.MM.YYYY / DD/MM/YYYY / DD-MM-YYYY и, как последнюю
    # страховку, contract_date, если created_at отсутствует.
    historical_date = (
        "(CASE "
        "WHEN substr(COALESCE(p.contract_date,''),5,1)='-' THEN substr(p.contract_date,1,10) "
        "WHEN substr(COALESCE(p.contract_date,''),3,1) IN ('.','/','-') "
        "THEN substr(p.contract_date,7,4)||'-'||substr(p.contract_date,4,2)||'-'||substr(p.contract_date,1,2) "
        "ELSE substr(COALESCE(p.contract_date,''),1,10) END)"
    )
    created_period = (
        "(CASE "
        "WHEN substr(COALESCE(p.created_at,''),5,1)='-' THEN substr(p.created_at,1,10) "
        "WHEN substr(COALESCE(p.created_at,''),3,1) IN ('.','/','-') "
        "THEN substr(p.created_at,7,4)||'-'||substr(p.created_at,4,2)||'-'||substr(p.created_at,1,2) "
        f"WHEN COALESCE(p.created_at,'')='' THEN {historical_date} "
        "ELSE substr(COALESCE(p.created_at,''),1,10) END)"
    )

    if year:
        query += f" AND substr({created_period},1,4)=?"
        params.append(str(year))
    if month:
        query += f" AND substr({created_period},6,2)=?"
        params.append(f"{month:02d}")
    if contract_status:
        query += " AND p.contract_status = ?"
        params.append(contract_status)
    if payment_status:
        query += " AND p.payment_status = ?"
        params.append(payment_status)
    if exec_status:
        query += " AND p.exec_status = ?"
        params.append(exec_status)
    if search:
        like = f"%{search.strip()}%"
        query += (" AND (COALESCE(p.customer,'') LIKE ? COLLATE NOCASE "
                  "OR COALESCE(p.contract_no,'') LIKE ? COLLATE NOCASE "
                  "OR COALESCE(p.registry_record,'') LIKE ? COLLATE NOCASE "
                  "OR EXISTS (SELECT 1 FROM purchase_items si "
                  "WHERE si.purchase_id=p.id AND COALESCE(si.product,'') LIKE ? COLLATE NOCASE))")
        params.extend([like, like, like, like])

    query += f" ORDER BY {created_period} DESC, p.id DESC"
    header_rows = conn.execute(query, params).fetchall()

    items_map = _fetch_items_for_purchase_ids(conn, [h["id"] for h in header_rows])
    result = []
    for h in header_rows:
        items = items_map.get(int(h["id"]), [])
        row = dict(h)
        row["items"] = items
        row["product"] = _products_summary(items)
        row["qty"] = _qty_total(items)
        result.append(row)
    return result

def dashboard_kpis(conn: sqlite3.Connection):
    """KPI главного экрана. Резерв берётся из той же складской сводки,
    что и вкладка «Склад», поэтому расхождений между экранами быть не должно."""
    r = conn.execute(
        """SELECT
               SUM(CASE WHEN COALESCE(exec_status,'') <> 'Исполнено' THEN 1 ELSE 0 END) AS work_count,
               SUM(CASE WHEN COALESCE(exec_status,'') <> 'Исполнено' THEN COALESCE(contract_sum,0) ELSE 0 END) AS work_sum,
               SUM(CASE WHEN COALESCE(payment_status,'') <> 'Оплачено' THEN COALESCE(contract_sum,0) ELSE 0 END) AS awaiting
           FROM purchases WHERE deleted_at IS NULL"""
    ).fetchone()
    reserve_qty = sum(float(x.get("reserved", 0) or 0) for x in stock_summary(conn))
    return {
        "work_count": int(r["work_count"] or 0),
        "work_sum": float(r["work_sum"] or 0),
        "awaiting": float(r["awaiting"] or 0),
        "reserve_qty": reserve_qty,
    }


def deadline_rows(conn: sqlite3.Connection):
    """Только контракты, способные попасть во вкладку дедлайнов, с кратким товаром."""
    rows = conn.execute(
        """SELECT p.* FROM purchases p
           WHERE p.deleted_at IS NULL AND (
             (COALESCE(p.contract_status,'') <> 'Заключен' AND p.sign_deadline IS NOT NULL AND p.sign_deadline <> '')
             OR
             (COALESCE(p.exec_status,'') NOT IN ('Вручен','Исполнено') AND p.deadline IS NOT NULL AND p.deadline <> '')
             OR
             (COALESCE(p.payment_status,'') <> 'Оплачено'
              AND p.handover_date IS NOT NULL AND p.handover_date <> ''
              AND p.payment_deadline IS NOT NULL AND p.payment_deadline <> '')
           )
           ORDER BY p.id"""
    ).fetchall()
    items_map = _fetch_items_for_purchase_ids(conn, [r["id"] for r in rows])
    out=[]
    for h in rows:
        d=dict(h); items=items_map.get(int(h["id"]), [])
        d["items"]=items; d["product"]=_products_summary(items); d["qty"]=_qty_total(items)
        out.append(d)
    return out


def fetch_by_id(conn: sqlite3.Connection, purchase_id: int):
    h = conn.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
    if h is None:
        return None
    row = dict(h)
    row["items"] = fetch_items(conn, purchase_id)
    row["product"] = _products_summary(row["items"])
    row["qty"] = _qty_total(row["items"])
    return row


def distinct_years(conn: sqlite3.Connection):
    """Годы, встречающиеся в контрактах и финансовых итогах."""
    rows = conn.execute(
        "SELECT created_at, contract_date, handover_date FROM purchases WHERE deleted_at IS NULL"
    ).fetchall()
    years = set()

    def _parse_period(value):
        if not value:
            return None
        text = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                return parsed if 2000 <= parsed.year <= 2100 else None
            except ValueError:
                continue
        return None

    for r in rows:
        for value in (r["created_at"], r["contract_date"], r["handover_date"]):
            parsed = _parse_period(value)
            if parsed is not None:
                years.add(parsed.year)
    try:
        for r in conn.execute("SELECT expense_date FROM monthly_expenses").fetchall():
            parsed = _parse_period(r["expense_date"])
            if parsed is not None:
                years.add(parsed.year)
    except sqlite3.OperationalError:
        pass
    return sorted(years)

def distinct_products(conn: sqlite3.Connection):
    return catalog_products(conn)


def _parse_summary_date(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            return parsed if 2000 <= parsed.year <= 2100 else None
        except ValueError:
            continue
    return None


def _is_deferred_purchase_contract(conn: sqlite3.Connection, purchase_id: int) -> bool:
    """В рабочей модели смешанных контрактов нет: наличие отложенной закупки
    делает весь контракт отложенным для финансовых итогов."""
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN supply_mode='Отложенная закупка' THEN 1 ELSE 0 END) AS deferred
           FROM purchase_items WHERE purchase_id=?""",
        (purchase_id,),
    ).fetchone()
    return bool(row and int(row["total"] or 0) > 0 and int(row["deferred"] or 0) > 0)


def _summary_period_for_purchase(conn: sqlite3.Connection, purchase) -> date | None:
    """Период финансовых итогов.
    Обычный контракт -> месяц заведения.
    Отложенная закупка -> только после статуса «Исполнено», месяц даты вручения.
    """
    if _is_deferred_purchase_contract(conn, int(purchase["id"])):
        if (purchase["exec_status"] or "") != "Исполнено":
            return None
        return _parse_summary_date(purchase["handover_date"])
    return _parse_summary_date(purchase["created_at"]) or _parse_summary_date(purchase["contract_date"])


def summary_contracts(conn: sqlite3.Connection, year: int, month: int):
    """Контракты, реально вошедшие в финансовые итоги указанного месяца."""
    rows = conn.execute("SELECT * FROM purchases WHERE deleted_at IS NULL ORDER BY id").fetchall()
    matched = []
    for r in rows:
        period = _summary_period_for_purchase(conn, r)
        if period and period.year == int(year) and period.month == int(month):
            d = dict(r)
            d["items"] = [dict(x) for x in fetch_items(conn, int(r["id"]))]
            d["product"] = _products_summary(d["items"])
            d["qty"] = _qty_total(d["items"])
            d["summary_period"] = period.isoformat()
            d["deferred_purchase"] = _is_deferred_purchase_contract(conn, int(r["id"]))
            matched.append(d)
    return matched


def insert_monthly_expense(conn: sqlite3.Connection, data: dict) -> int:
    expense_date = str(data.get("expense_date") or "").strip()
    if not _parse_summary_date(expense_date):
        raise ValueError("Некорректная дата расхода")
    amount = float(data.get("amount") or 0)
    if amount < 0:
        raise ValueError("Сумма расхода не может быть отрицательной")
    cur = conn.execute(
        "INSERT INTO monthly_expenses(expense_date, category, amount, description) VALUES(?,?,?,?)",
        (expense_date, (data.get("category") or "Прочее").strip(), amount,
         (data.get("description") or "").strip() or None),
    )
    conn.commit()
    return int(cur.lastrowid)


def fetch_monthly_expenses(conn: sqlite3.Connection, year: int = None, month: int = None):
    query = "SELECT * FROM monthly_expenses WHERE 1=1"
    params = []
    if year is not None:
        query += " AND substr(expense_date,1,4)=?"
        params.append(str(int(year)))
    if month is not None:
        query += " AND substr(expense_date,6,2)=?"
        params.append(f"{int(month):02d}")
    query += " ORDER BY expense_date, id"
    return conn.execute(query, params).fetchall()


def delete_monthly_expense(conn: sqlite3.Connection, expense_id: int):
    conn.execute("DELETE FROM monthly_expenses WHERE id=?", (int(expense_id),))
    conn.commit()


def set_tax_profile(conn: sqlite3.Connection, effective_from: str, regime: str, rate: float):
    d = _parse_summary_date(effective_from)
    if not d:
        raise ValueError("Некорректная дата начала действия налогового режима")
    regime = (regime or "").strip()
    if regime not in ("С доходов", "Доходы минус расходы"):
        raise ValueError("Неизвестный налоговый режим")
    rate = float(rate)
    if rate < 0 or rate > 100:
        raise ValueError("Ставка налога должна быть от 0 до 100%")
    start = d.replace(day=1).isoformat()
    row = conn.execute("SELECT id FROM tax_profiles WHERE effective_from=? ORDER BY id DESC LIMIT 1", (start,)).fetchone()
    if row:
        conn.execute("UPDATE tax_profiles SET regime=?, rate=? WHERE id=?", (regime, rate, row["id"]))
    else:
        conn.execute("INSERT INTO tax_profiles(effective_from, regime, rate) VALUES(?,?,?)", (start, regime, rate))
    conn.commit()


def get_tax_profile(conn: sqlite3.Connection, year: int, month: int):
    period = f"{int(year):04d}-{int(month):02d}-01"
    row = conn.execute("SELECT * FROM tax_profiles WHERE effective_from <= ? ORDER BY effective_from DESC, id DESC LIMIT 1", (period,)).fetchone()
    if row:
        return {"effective_from": row["effective_from"], "regime": row["regime"], "rate": float(row["rate"] or 0)}
    return {"effective_from": None, "regime": "С доходов", "rate": 7.0}


def _calc_month_tax(conn: sqlite3.Connection, year: int, month: int, g: dict):
    profile = get_tax_profile(conn, year, month)
    rate = float(profile["rate"] or 0) / 100.0
    if profile["regime"] == "Доходы минус расходы":
        deductible = (float(g.get("purchase_cost",0) or 0) + float(g.get("logistics",0) or 0) + float(g.get("commission",0) or 0) + float(g.get("other_costs",0) or 0) + float(g.get("guarantee",0) or 0) + float(g.get("monthly_expenses",0) or 0))
        base = max(0.0, float(g.get("contract_sum",0) or 0) - deductible)
    else:
        base = max(0.0, float(g.get("contract_sum",0) or 0))
    tax = round(base * rate, 2)
    return tax, base, profile

def monthly_summary(conn: sqlite3.Connection, year: int = None, month: int = None):
    """Финансовые итоги 2.18.

    Обычные контракты учитываются по месяцу заведения. Контракты с отложенной
    закупкой до исполнения из итогов исключены; после «Исполнено» учитываются
    целиком в месяце даты вручения.

    Прочие месячные расходы из monthly_expenses уменьшают чистую прибыль месяца.
    """
    from calculations import calc_profit, calc_margin_pct

    rows = conn.execute("SELECT * FROM purchases WHERE deleted_at IS NULL ORDER BY id").fetchall()
    grouped = {}
    for r in rows:
        period_date = _summary_period_for_purchase(conn, r)
        if period_date is None:
            continue
        if year is not None and period_date.year != int(year):
            continue
        if month is not None and period_date.month != int(month):
            continue
        key = (period_date.year, period_date.month)
        g = grouped.setdefault(key, {
            "contract_sum": 0.0, "purchase_cost": 0.0, "logistics": 0.0,
            "commission": 0.0, "other_costs": 0.0, "guarantee": 0.0,
            "monthly_expenses": 0.0, "qty_total": 0.0, "qty_paid": 0.0,
            "qty_unpaid": 0.0, "contracts_count": 0,
        })
        g["contracts_count"] += 1
        for field in ("contract_sum","purchase_cost","logistics","commission","other_costs","guarantee"):
            g[field] += float(r[field] or 0.0)

        # v2.22.2: «Реализовано, шт.» определяется по фактическому этапу
        # движения товара, а не только по наличию даты вручения.
        # Отправлено -> товар уже покинул склад; Вручен/Исполнено -> тем более реализован.
        # stock_written_off сохраняется идемпотентно после первой отправки и позволяет
        # корректно считать старые записи, даже если статус позже изменён.
        exec_status = (r["exec_status"] or "").strip()
        realized = bool(int(r["stock_written_off"] or 0)) or exec_status in ("Отправлено", "Вручен", "Исполнено")
        if realized:
            qty_row = conn.execute(
                "SELECT COALESCE(SUM(qty),0) AS qty FROM purchase_items WHERE purchase_id=?",
                (r["id"],),
            ).fetchone()
            qty = float(qty_row["qty"] or 0.0)
            g["qty_total"] += qty
            if r["payment_status"] == "Оплачено":
                g["qty_paid"] += qty
            else:
                g["qty_unpaid"] += qty

    # Месяц может содержать только вне-контрактные расходы — он всё равно должен
    # отображаться в итогах.
    for e in fetch_monthly_expenses(conn, year=year, month=month):
        d = _parse_summary_date(e["expense_date"])
        if not d:
            continue
        key = (d.year, d.month)
        g = grouped.setdefault(key, {
            "contract_sum": 0.0, "purchase_cost": 0.0, "logistics": 0.0,
            "commission": 0.0, "other_costs": 0.0, "guarantee": 0.0,
            "monthly_expenses": 0.0, "qty_total": 0.0, "qty_paid": 0.0,
            "qty_unpaid": 0.0, "contracts_count": 0,
        })
        g["monthly_expenses"] += float(e["amount"] or 0.0)

    result = []
    for (y,m),g in sorted(grouped.items()):
        contract_sum=g["contract_sum"]; purchase_cost=g["purchase_cost"]
        logistics=g["logistics"]; commission=g["commission"]
        other_costs=g["other_costs"]; guarantee=g["guarantee"]
        monthly_expenses=g["monthly_expenses"]
        tax,tax_base,tax_profile=_calc_month_tax(conn,y,m,g)
        contract_profit=calc_profit(contract_sum,purchase_cost,logistics,commission,other_costs,guarantee,tax)
        profit=round(contract_profit-monthly_expenses,2)
        total_expenses=round(purchase_cost+logistics+commission+other_costs+guarantee+tax+monthly_expenses,2)
        result.append({
            "year":y,"month":m,"contracts_count":g["contracts_count"],
            "contract_sum":contract_sum,"purchase_cost":purchase_cost,
            "logistics":logistics,"commission":commission,"other_costs":other_costs,
            "guarantee":guarantee,"tax":tax,"tax_base":tax_base,
            "tax_regime":tax_profile["regime"],"tax_rate":tax_profile["rate"],
            "tax_effective_from":tax_profile["effective_from"],"monthly_expenses":monthly_expenses,
            "total_expenses":total_expenses,"profit":profit,
            "margin_pct":calc_margin_pct(profit,contract_sum),
            "qty_total":g["qty_total"],"qty_paid":g["qty_paid"],"qty_unpaid":g["qty_unpaid"],
        })
    return result


def top_customers(conn: sqlite3.Connection, limit: int = 5):
    rows = conn.execute(
        "SELECT customer, SUM(contract_sum) AS total FROM purchases "
        "WHERE customer IS NOT NULL AND customer <> '' AND deleted_at IS NULL "
        "GROUP BY customer ORDER BY total DESC LIMIT ?", (limit,)
    ).fetchall()
    return [(r["customer"], r["total"] or 0.0) for r in rows]


def top_platforms(conn: sqlite3.Connection, limit: int = 5):
    rows = conn.execute(
        "SELECT platform, SUM(contract_sum) AS total, COUNT(*) AS cnt FROM purchases "
        "WHERE platform IS NOT NULL AND platform <> '' AND deleted_at IS NULL "
        "GROUP BY platform ORDER BY total DESC LIMIT ?", (limit,)
    ).fetchall()
    return [(r["platform"], r["total"] or 0.0, r["cnt"]) for r in rows]


# ---------------------------------------------------------------- Склад (приход + резерв)
def insert_receipt(conn: sqlite3.Connection, data: dict) -> int:
    cols = ", ".join(RECEIPT_FIELDS)
    placeholders = ", ".join(["?"] * len(RECEIPT_FIELDS))
    data = dict(data)
    if data.get("product"):
        data["product"] = ensure_product(conn, data["product"])
    values = [data.get(f) for f in RECEIPT_FIELDS]
    cur = conn.execute(f"INSERT INTO stock_receipts ({cols}) VALUES ({placeholders})", values)
    add_stock_audit(
        conn, data.get("product"), "Приход товара", float(data.get("qty") or 0),
        counterparty=data.get("supplier") or "",
        details=f"Приход №{cur.lastrowid}; дата {data.get('receipt_date') or '—'}"
    )
    conn.commit()
    return cur.lastrowid


def update_receipt(conn: sqlite3.Connection, receipt_id: int, data: dict):
    old = fetch_receipt_by_id(conn, receipt_id)
    set_clause = ", ".join(f"{f} = ?" for f in RECEIPT_FIELDS)
    data = dict(data)
    if data.get("product"):
        data["product"] = ensure_product(conn, data["product"])
    values = [data.get(f) for f in RECEIPT_FIELDS] + [receipt_id]
    conn.execute(f"UPDATE stock_receipts SET {set_clause} WHERE id = ?", values)
    old_product = old["product"] if old is not None else data.get("product")
    details = (
        f"Приход №{receipt_id}: "
        f"{float(old['qty'] or 0) if old is not None else 0:g} → {float(data.get('qty') or 0):g} шт.; "
        f"товар {old_product or '—'} → {data.get('product') or '—'}"
    )
    add_stock_audit(conn, old_product or data.get("product"), "Изменён приход", None,
                    counterparty=data.get("supplier") or "", details=details)
    if data.get("product") and data.get("product") != old_product:
        add_stock_audit(conn, data.get("product"), "Изменён приход", None,
                        counterparty=data.get("supplier") or "", details=details)
    conn.commit()


def delete_receipt(conn: sqlite3.Connection, receipt_id: int):
    old = fetch_receipt_by_id(conn, receipt_id)
    if old is not None:
        add_stock_audit(
            conn, old["product"], "Удалён приход", -float(old["qty"] or 0),
            counterparty=old["supplier"] or "",
            details=f"Удалён приход №{receipt_id}; ранее было {float(old['qty'] or 0):g} шт."
        )
    conn.execute("DELETE FROM stock_receipts WHERE id = ?", (receipt_id,))
    conn.commit()


def fetch_receipts(conn: sqlite3.Connection, search: str = None):
    query = "SELECT * FROM stock_receipts WHERE 1=1"
    params = []
    if search:
        query += " AND product LIKE ?"
        params.append(f"%{search}%")
    query += " ORDER BY receipt_date DESC, id DESC"
    return conn.execute(query, params).fetchall()


def fetch_receipt_by_id(conn: sqlite3.Connection, receipt_id: int):
    return conn.execute("SELECT * FROM stock_receipts WHERE id = ?", (receipt_id,)).fetchone()


def available_for_contract(conn: sqlite3.Connection, product: str, purchase_id=None) -> float:
    """Доступно для нового количества в карточке, исключая её собственный текущий резерв."""
    product = ensure_product(conn, product) if product else product
    row = next((r for r in stock_summary(conn) if r["product"] == product), None)
    available = float(row["available"] or 0.0) if row else 0.0
    if purchase_id is not None:
        p = conn.execute("SELECT stock_written_off FROM purchases WHERE id=?", (purchase_id,)).fetchone()
        if p and not int(p["stock_written_off"] or 0):
            own = conn.execute("SELECT COALESCE(SUM(stock_qty),0) AS q FROM purchase_items WHERE purchase_id=? AND product=?", (purchase_id, product)).fetchone()["q"]
            available += float(own or 0.0)
    return available

def stock_summary(conn: sqlite3.Connection):
    """Сводка склада.

    Физический остаток = приход − товары по контрактам, переведённым в «Отправлено».
    Дата вручения сама по себе склад не списывает.
    Автоматический резерв = только та часть позиции, которая назначена «со склада»
    и ещё не отправлена. Будущая потребность учитывается отдельно.
    Доступно = физический остаток − общий резерв.
    """
    received_rows = conn.execute(
        "SELECT product, SUM(qty) AS q FROM stock_receipts "
        "WHERE product IS NOT NULL AND product <> '' GROUP BY product"
    ).fetchall()
    shipped_rows = conn.execute(
        "SELECT i.product AS product, SUM(COALESCE(i.stock_qty,0)) AS q FROM purchase_items i "
        "JOIN purchases p ON p.id = i.purchase_id "
        "WHERE i.product IS NOT NULL AND i.product <> '' AND p.deleted_at IS NULL "
        "AND COALESCE(p.stock_written_off,0)=1 GROUP BY i.product"
    ).fetchall()
    reserved_rows = conn.execute(
        "SELECT i.product AS product, SUM(COALESCE(i.stock_qty,0)) AS q FROM purchase_items i "
        "JOIN purchases p ON p.id = i.purchase_id "
        "WHERE i.product IS NOT NULL AND i.product <> '' AND p.deleted_at IS NULL "
        "AND COALESCE(p.stock_written_off,0)=0 "
        "GROUP BY i.product"
    ).fetchall()
    manual_reserved_rows = conn.execute(
        "SELECT product, SUM(qty) AS q FROM manual_reservations "
        "WHERE product IS NOT NULL AND product <> '' GROUP BY product"
    ).fetchall()
    received = {r["product"]: (r["q"] or 0.0) for r in received_rows}
    shipped = {r["product"]: (r["q"] or 0.0) for r in shipped_rows}
    reserved = {r["product"]: (r["q"] or 0.0) for r in reserved_rows}
    manual_reserved = {r["product"]: (r["q"] or 0.0) for r in manual_reserved_rows}
    future_rows = conn.execute(
        """SELECT i.product AS product,
                  SUM(CASE WHEN COALESCE(i.qty,0) > COALESCE(i.stock_qty,0)
                           THEN COALESCE(i.qty,0)-COALESCE(i.stock_qty,0) ELSE 0 END) AS q
           FROM purchase_items i JOIN purchases p ON p.id=i.purchase_id
           WHERE p.deleted_at IS NULL AND COALESCE(p.stock_written_off,0)=0
           GROUP BY i.product"""
    ).fetchall()
    future = {r["product"]: (r["q"] or 0.0) for r in future_rows}
    products = sorted(set(received) | set(shipped) | set(reserved) | set(manual_reserved) | set(future))
    result = []
    for product in products:
        on_hand = received.get(product, 0.0) - shipped.get(product, 0.0)
        auto_res = reserved.get(product, 0.0)
        manual_res = manual_reserved.get(product, 0.0)
        total_res = auto_res + manual_res
        future_demand = float(future.get(product, 0.0) or 0.0)

        # v2.22.3: сколько реально нужно закупить под действующие контракты.
        # 1) future_demand — часть контрактов, которая изначально помечена как
        #    «Требуется закупка» / «Отложенная закупка» и не обеспечена складом;
        # 2) если складского резерва по контрактам физически больше, чем on_hand,
        #    добавляем этот дефицит.
        # Ручной резерв под потенциальных клиентов сюда намеренно не включается.
        contract_stock_shortage = max(0.0, auto_res - on_hand)
        need_to_buy = future_demand + contract_stock_shortage

        result.append({
            "product": product,
            "on_hand": on_hand,
            "reserved": total_res,
            "auto_reserved": auto_res,
            "manual_reserved": manual_res,
            "available": on_hand - total_res,
            "future_demand": future_demand,
            "need_to_buy": need_to_buy,
        })
    return result


def stock_total_value(conn: sqlite3.Connection):
    """Стоимость текущего физического остатка склада после фактической реализации.
    Для каждого товара используется средневзвешенная себестоимость приходов."""
    received = conn.execute(
        "SELECT product, SUM(qty) AS qty, SUM(qty * COALESCE(unit_cost, 0)) AS value "
        "FROM stock_receipts WHERE product IS NOT NULL AND product <> '' GROUP BY product"
    ).fetchall()
    shipped = conn.execute(
        "SELECT i.product AS product, SUM(COALESCE(i.stock_qty,0)) AS qty FROM purchase_items i "
        "JOIN purchases p ON p.id = i.purchase_id "
        "WHERE i.product IS NOT NULL AND i.product <> '' AND p.deleted_at IS NULL "
        "AND COALESCE(p.stock_written_off,0)=1 "
        "GROUP BY i.product"
    ).fetchall()
    shipped_map = {r['product']: (r['qty'] or 0.0) for r in shipped}
    total = 0.0
    for r in received:
        qty = r['qty'] or 0.0
        if qty <= 0:
            continue
        avg_cost = (r['value'] or 0.0) / qty
        remaining = max(0.0, qty - shipped_map.get(r['product'], 0.0))
        total += remaining * avg_cost
    return round(total, 2)

def stock_product_movement(conn: sqlite3.Connection, product: str):
    """Хронология движения: приход, автоматический резерв, реализация и ручной резерв."""
    events = []
    rows = conn.execute(
        "SELECT receipt_date AS dt, qty, unit_cost, supplier FROM stock_receipts "
        "WHERE product = ? ORDER BY receipt_date, id", (product,)).fetchall()
    for r in rows:
        events.append({"date": r["dt"], "type": "Приход", "qty": r["qty"] or 0.0,
                       "unit_cost": r["unit_cost"] or 0.0, "counterparty": r["supplier"] or "",
                       "details": "Поступление на склад"})

    rows = conn.execute(
        "SELECT p.contract_date AS contract_date, p.handover_date AS handover_date, "
        "p.exec_status, p.stock_written_off, i.qty, i.stock_qty, i.supply_mode, p.contract_no, p.customer FROM purchase_items i "
        "JOIN purchases p ON p.id = i.purchase_id "
        "WHERE i.product = ? AND p.deleted_at IS NULL ORDER BY p.id", (product,)).fetchall()
    for r in rows:
        shipped = bool(r["stock_written_off"])
        stock_qty = float(r["stock_qty"] or 0)
        if shipped and stock_qty > 0:
            dt = r["contract_date"] or ""
            events.append({"date": dt, "type": "Реализация", "qty": -stock_qty,
                           "unit_cost": None, "counterparty": r["customer"] or "",
                           "details": f"Контракт {r['contract_no'] or '—'}"})
        elif stock_qty > 0:
            events.append({"date": r["contract_date"] or "", "type": "Резерв контракта",
                           "qty": stock_qty, "unit_cost": None,
                           "counterparty": r["customer"] or "",
                           "details": f"Контракт {r['contract_no'] or '—'}"})

    rows = conn.execute(
        "SELECT reserved_date AS dt, qty, organization FROM manual_reservations "
        "WHERE product = ? ORDER BY reserved_date, id", (product,)).fetchall()
    for r in rows:
        events.append({"date": r["dt"], "type": "Резерв", "qty": r["qty"] or 0.0,
                       "unit_cost": None, "counterparty": r["organization"] or "",
                       "details": "Ручной резерв"})
    events.sort(key=lambda e: (e["date"] or "", e["type"]))
    balance = 0.0
    for e in events:
        if e["type"] in ("Приход", "Реализация"):
            balance += e["qty"]
        e["balance"] = balance
    return events


# ---------------------------------------------------------------- Вложения
def insert_attachment(conn: sqlite3.Connection, purchase_id: int, filename: str,
                       stored_path: str, note: str = None, category: str = "Прочее") -> int:
    added_date = date.today().isoformat()
    stored_path = make_attachment_relative(stored_path, purchase_id, filename)
    category = category if category in DOCUMENT_CATEGORIES else "Прочее"
    cur = conn.execute(
        "INSERT INTO attachments (purchase_id, filename, stored_path, added_date, category, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (purchase_id, filename, stored_path, added_date, category, note),
    )
    add_audit(conn, purchase_id, "Добавлен документ", f"{category}: {filename}")
    conn.commit()
    return cur.lastrowid


def fetch_attachments(conn: sqlite3.Connection, purchase_id: int):
    return conn.execute(
        "SELECT * FROM attachments WHERE purchase_id = ? ORDER BY id", (purchase_id,)
    ).fetchall()


def update_attachment_category(conn: sqlite3.Connection, attachment_id: int, category: str):
    category = category if category in DOCUMENT_CATEGORIES else "Прочее"
    row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
    if row is None:
        return
    old = row["category"] or "Прочее"
    conn.execute("UPDATE attachments SET category=? WHERE id=?", (category, attachment_id))
    if old != category:
        add_audit(conn, row["purchase_id"], "Изменена категория документа",
                  f"{row['filename']}: {old} → {category}")
    conn.commit()


def delete_attachment(conn: sqlite3.Connection, attachment_id: int, remove_file: bool = True):
    row = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
    if row and remove_file and row["stored_path"]:
        path = resolve_attachment_path(row["stored_path"], row["purchase_id"], row["filename"])
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    if row:
        add_audit(conn, row["purchase_id"], "Удалён документ", f"{row["category"] or 'Прочее'}: {row["filename"]}")
    conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    conn.commit()


# ---------------------------------------------------------------- Анализ конкурентов
def normalize_competitor_name(name: str) -> str:
    """Ключ компании для объединения вариантов написания.

    Игнорируются регистр, пробелы, кавычки, тире/дефисы и прочая пунктуация.
    Буква ё приводится к е. Буквы и цифры сохраняются, поэтому разные
    содержательные названия не склеиваются.
    """
    value = unicodedata.normalize("NFKC", str(name or "")).casefold().replace("ё", "е")
    return "".join(ch for ch in value if ch.isalnum())


def normalize_inn(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def canonical_competitor_name(conn: sqlite3.Connection, name: str, competitor_inn: str = None, exclude_id: int = None) -> str:
    """ИНН имеет приоритет над написанием названия компании."""
    clean = re.sub(r"\s+", " ", str(name or "").strip())
    inn = normalize_inn(competitor_inn)
    if inn:
        query = "SELECT id, competitor FROM competitor_records WHERE competitor_inn=? AND competitor IS NOT NULL AND trim(competitor)<>''"
        params = [inn]
        if exclude_id is not None:
            query += " AND id<>?"
            params.append(int(exclude_id))
        query += " ORDER BY id LIMIT 1"
        row = conn.execute(query, params).fetchone()
        if row:
            return row["competitor"]
    key = normalize_competitor_name(clean)
    if not key:
        return clean
    query = "SELECT id, competitor FROM competitor_records WHERE competitor IS NOT NULL AND trim(competitor) <> ''"
    params = []
    if exclude_id is not None:
        query += " AND id <> ?"
        params.append(int(exclude_id))
    query += " ORDER BY id"
    for row in conn.execute(query, params).fetchall():
        if normalize_competitor_name(row["competitor"]) == key:
            return row["competitor"]
    return clean

def insert_competitor_record(conn: sqlite3.Connection, data: dict) -> int:
    cols = ", ".join(COMPETITOR_FIELDS)
    placeholders = ", ".join(["?"] * len(COMPETITOR_FIELDS))
    data = dict(data)
    if data.get("product"):
        data["product"] = ensure_product(conn, data["product"])
    data["competitor_inn"] = normalize_inn(data.get("competitor_inn")) or None
    if data.get("competitor"):
        data["competitor"] = canonical_competitor_name(conn, data["competitor"], data.get("competitor_inn"))
    values = [data.get(f) for f in COMPETITOR_FIELDS]
    cur = conn.execute(f"INSERT INTO competitor_records ({cols}) VALUES ({placeholders})", values)
    conn.commit()
    return cur.lastrowid


def update_competitor_record(conn: sqlite3.Connection, record_id: int, data: dict):
    set_clause = ", ".join(f"{f} = ?" for f in COMPETITOR_FIELDS)
    data = dict(data)
    if data.get("product"):
        data["product"] = ensure_product(conn, data["product"])
    data["competitor_inn"] = normalize_inn(data.get("competitor_inn")) or None
    if data.get("competitor"):
        data["competitor"] = canonical_competitor_name(conn, data["competitor"], data.get("competitor_inn"), exclude_id=record_id)
    values = [data.get(f) for f in COMPETITOR_FIELDS] + [record_id]
    conn.execute(f"UPDATE competitor_records SET {set_clause} WHERE id = ?", values)
    conn.commit()


def delete_competitor_record(conn: sqlite3.Connection, record_id: int):
    conn.execute("DELETE FROM competitor_records WHERE id = ?", (record_id,))
    conn.commit()


def fetch_competitor_records(conn: sqlite3.Connection, search: str = None):
    query = "SELECT * FROM competitor_records WHERE 1=1"
    params = []
    if search:
        query += " AND (competitor LIKE ? OR competitor_inn LIKE ? OR product LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    query += " ORDER BY purchase_date DESC, id DESC"
    return conn.execute(query, params).fetchall()


def fetch_competitor_record_by_id(conn: sqlite3.Connection, record_id: int):
    return conn.execute("SELECT * FROM competitor_records WHERE id = ?", (record_id,)).fetchone()


def competitor_product_stats(conn: sqlite3.Connection):
    """
    По каждому товару: мин./макс./средняя/медианная цена, кол-во наблюдений,
    изменение средней цены (сравнение первой и второй половины наблюдений
    в хронологическом порядке по дате закупки).
    """
    rows = conn.execute(
        "SELECT product, unit_price, purchase_date FROM competitor_records "
        "WHERE product IS NOT NULL AND product <> '' AND unit_price IS NOT NULL"
    ).fetchall()
    by_product = {}
    for r in rows:
        by_product.setdefault(r["product"], []).append(
            (r["purchase_date"] or "", r["unit_price"])
        )

    result = []
    for product, entries in sorted(by_product.items()):
        prices = [p for _, p in entries]
        entries_sorted = sorted(entries, key=lambda e: e[0])  # по дате (пустые даты — в начале)
        ordered_prices = [p for _, p in entries_sorted]

        change_pct = None
        if len(ordered_prices) >= 2:
            mid = len(ordered_prices) // 2
            first_half = ordered_prices[:mid] if mid > 0 else ordered_prices[:1]
            second_half = ordered_prices[mid:] if mid > 0 else ordered_prices[1:]
            avg_first = sum(first_half) / len(first_half)
            avg_second = sum(second_half) / len(second_half)
            if avg_first:
                change_pct = (avg_second - avg_first) / avg_first

        result.append({
            "product": product,
            "min_price": min(prices),
            "max_price": max(prices),
            "avg_price": sum(prices) / len(prices),
            "median_price": statistics.median(prices),
            "count": len(prices),
            "change_pct": change_pct,
        })
    return result


def competitor_stats(conn: sqlite3.Connection):
    """Статистика по конкурентам. При наличии ИНН записи объединяются по ИНН,
    даже если название компании введено по-разному. Для старых записей без ИНН
    сохраняется объединение по нормализованному названию.
    """
    rows = conn.execute(
        "SELECT id, competitor, competitor_inn, unit_price FROM competitor_records "
        "WHERE competitor IS NOT NULL AND competitor <> '' AND unit_price IS NOT NULL ORDER BY id"
    ).fetchall()
    if not rows:
        return []
    overall_avg = sum(r["unit_price"] for r in rows) / len(rows)
    grouped = {}
    for r in rows:
        inn = normalize_inn(r["competitor_inn"])
        key = ("inn", inn) if inn else ("name", normalize_competitor_name(r["competitor"]))
        if not key[1]:
            continue
        group = grouped.setdefault(key, {"prices": [], "labels": {}, "first": r["competitor"], "inn": inn or None})
        group["prices"].append(r["unit_price"])
        label = re.sub(r"\s+", " ", r["competitor"].strip())
        group["labels"][label] = group["labels"].get(label, 0) + 1
    result = []
    for group in grouped.values():
        prices = group["prices"]
        max_count = max(group["labels"].values())
        competitor = next((label for label,count in group["labels"].items() if count==max_count), group["first"])
        avg_price = sum(prices)/len(prices)
        result.append({
            "competitor": competitor,
            "competitor_inn": group["inn"],
            "wins": len(prices),
            "avg_price": avg_price,
            "min_price": min(prices),
            "max_price": max(prices),
            "relative_pct": (avg_price-overall_avg)/overall_avg if overall_avg else None,
        })
    return sorted(result, key=lambda item: (item.get("competitor_inn") or "", normalize_competitor_name(item["competitor"])))


# ---------------------------------------------------------------- Калькулятор цены
def insert_calculator_row(conn: sqlite3.Connection, data: dict) -> int:
    cols = ", ".join(CALCULATOR_FIELDS)
    placeholders = ", ".join(["?"] * len(CALCULATOR_FIELDS))
    values = [data.get(f) for f in CALCULATOR_FIELDS]
    cur = conn.execute(f"INSERT INTO calculator_rows ({cols}) VALUES ({placeholders})", values)
    conn.commit()
    return cur.lastrowid


def update_calculator_row(conn: sqlite3.Connection, row_id: int, data: dict):
    set_clause = ", ".join(f"{f} = ?" for f in CALCULATOR_FIELDS)
    values = [data.get(f) for f in CALCULATOR_FIELDS] + [row_id]
    conn.execute(f"UPDATE calculator_rows SET {set_clause} WHERE id = ?", values)
    conn.commit()


def delete_calculator_row(conn: sqlite3.Connection, row_id: int):
    conn.execute("DELETE FROM calculator_rows WHERE id = ?", (row_id,))
    conn.commit()


def clear_calculator_rows(conn: sqlite3.Connection):
    conn.execute("DELETE FROM calculator_rows")
    conn.commit()


def fetch_calculator_rows(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM calculator_rows ORDER BY id").fetchall()


def fetch_calculator_row_by_id(conn: sqlite3.Connection, row_id: int):
    row = conn.execute("SELECT * FROM calculator_rows WHERE id = ?", (row_id,)).fetchone()
    return dict(row) if row is not None else None


# ---------------------------------------------------------------- Корзина контрактов
def delete_purchase(conn: sqlite3.Connection, purchase_id: int):
    """
    Перемещает контракт в «корзину» (мягкое удаление): сам контракт, его позиции и
    вложения НЕ стираются физически — просто перестают показываться в обычных списках.
    Через TRASH_KEEP_DAYS дней автоматически удалится навсегда (см. purge_old_trash),
    либо раньше — вручную из корзины (см. purge_purchase).
    """
    conn.execute("UPDATE purchases SET deleted_at = ? WHERE id = ?",
                 (datetime.now().isoformat(timespec="seconds"), purchase_id))
    conn.commit()


def restore_purchase(conn: sqlite3.Connection, purchase_id: int):
    """Возвращает контракт из корзины обратно в обычные списки."""
    conn.execute("UPDATE purchases SET deleted_at = NULL WHERE id = ?", (purchase_id,))
    conn.commit()


def purge_purchase(conn: sqlite3.Connection, purchase_id: int):
    """Настоящее, безвозвратное удаление: контракт, его позиции, вложения и файлы с диска."""
    for att in fetch_attachments(conn, purchase_id):
        try:
            path = resolve_attachment_path(att["stored_path"], att["purchase_id"], att["filename"])
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    conn.execute("DELETE FROM purchase_items WHERE purchase_id = ?", (purchase_id,))
    conn.execute("DELETE FROM attachments WHERE purchase_id = ?", (purchase_id,))
    conn.execute("DELETE FROM purchases WHERE id = ?", (purchase_id,))
    conn.commit()


def fetch_deleted_purchases(conn: sqlite3.Connection):
    """Список контрактов в корзине (с кратким составом товаров), новые сначала."""
    rows = conn.execute(
        "SELECT * FROM purchases WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
    ).fetchall()
    result = []
    for h in rows:
        items = fetch_items(conn, h["id"])
        row = dict(h)
        row["items"] = items
        row["product"] = _products_summary(items)
        row["qty"] = _qty_total(items)
        result.append(row)
    return result


def purge_old_trash(conn: sqlite3.Connection, days: int = TRASH_KEEP_DAYS) -> int:
    """Безвозвратно удаляет контракты, пролежавшие в корзине дольше `days` дней.
    Возвращает количество удалённых контрактов."""
    cutoff = datetime.now() - timedelta(days=days)
    rows = conn.execute(
        "SELECT id, deleted_at FROM purchases WHERE deleted_at IS NOT NULL"
    ).fetchall()
    purged = 0
    for r in rows:
        try:
            deleted_at = datetime.fromisoformat(r["deleted_at"])
        except (ValueError, TypeError):
            continue
        if deleted_at < cutoff:
            purge_purchase(conn, r["id"])
            purged += 1
    return purged


# ---------------------------------------------------------------- Ручной резерв склада
def insert_manual_reservation(conn: sqlite3.Connection, data: dict) -> int:
    cols = ", ".join(RESERVATION_FIELDS)
    placeholders = ", ".join(["?"] * len(RESERVATION_FIELDS))
    data = dict(data)
    if data.get("product"):
        data["product"] = ensure_product(conn, data["product"])
    values = [data.get(f) for f in RESERVATION_FIELDS]
    cur = conn.execute(f"INSERT INTO manual_reservations ({cols}) VALUES ({placeholders})", values)
    add_stock_audit(
        conn, data.get("product"), "Ручной резерв создан", float(data.get("qty") or 0),
        counterparty=data.get("organization") or "",
        details=f"Резерв №{cur.lastrowid}; дата {data.get('reserved_date') or '—'}"
    )
    conn.commit()
    return cur.lastrowid


def update_manual_reservation(conn: sqlite3.Connection, reservation_id: int, data: dict):
    old = fetch_manual_reservation_by_id(conn, reservation_id)
    set_clause = ", ".join(f"{f} = ?" for f in RESERVATION_FIELDS)
    data = dict(data)
    if data.get("product"):
        data["product"] = ensure_product(conn, data["product"])
    values = [data.get(f) for f in RESERVATION_FIELDS] + [reservation_id]
    conn.execute(f"UPDATE manual_reservations SET {set_clause} WHERE id = ?", values)
    old_product = old.get("product") if old else data.get("product")
    details = (
        f"Резерв №{reservation_id}: "
        f"{float(old.get('qty') or 0) if old else 0:g} → {float(data.get('qty') or 0):g} шт."
    )
    add_stock_audit(conn, old_product or data.get("product"), "Ручной резерв изменён", None,
                    counterparty=data.get("organization") or "", details=details)
    if data.get("product") and data.get("product") != old_product:
        add_stock_audit(conn, data.get("product"), "Ручной резерв изменён", None,
                        counterparty=data.get("organization") or "", details=details)
    conn.commit()


def delete_manual_reservation(conn: sqlite3.Connection, reservation_id: int):
    old = fetch_manual_reservation_by_id(conn, reservation_id)
    if old:
        add_stock_audit(
            conn, old.get("product"), "Ручной резерв удалён", -float(old.get("qty") or 0),
            counterparty=old.get("organization") or "",
            details=f"Удалён резерв №{reservation_id}; ранее было {float(old.get('qty') or 0):g} шт."
        )
    conn.execute("DELETE FROM manual_reservations WHERE id = ?", (reservation_id,))
    conn.commit()


def fetch_manual_reservations(conn: sqlite3.Connection, search: str = None):
    query = "SELECT * FROM manual_reservations WHERE 1=1"
    params = []
    if search:
        query += " AND (product LIKE ? OR organization LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    query += " ORDER BY reserved_date DESC, id DESC"
    return conn.execute(query, params).fetchall()


def fetch_manual_reservation_by_id(conn: sqlite3.Connection, reservation_id: int):
    row = conn.execute(
        "SELECT * FROM manual_reservations WHERE id = ?", (reservation_id,)
    ).fetchone()
    return dict(row) if row is not None else None