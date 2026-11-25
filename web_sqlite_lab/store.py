"""Isolated SQLite prototype for the Web 'Контракты ИП' app.

This is NOT the production Excel replacement. Financial/tax parity, Excel import,
stock corrections/returns and all document workflows are not implemented yet.
Money is stored strictly as integer kopecks. No production data is read.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from sqlite_build_safety import patched_wal_reset
import json
import os
import re
import sqlite3
import uuid

MOSCOW = timezone(timedelta(hours=3))
STATUSES = ("формирование", "подписать до", "на подписи", "заключен", "Отправлен", "Вручен", "Оплачен")
SHIPPED = {"Отправлен", "Вручен", "Оплачен"}
MONEY_LIMIT = 90_000_000_000_000
DATE_PATTERN = re.compile(r"^\d{4}-\d\d-\d\d$")

class DataError(ValueError):
    pass

class Conflict(DataError):
    pass

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def moscow_today():
    return datetime.now(MOSCOW).date().isoformat()

def money(value, required=False):
    if isinstance(value, bool) or isinstance(value, float):
        raise DataError("Денежное значение должно быть точным текстом или целым числом")
    raw = str(value if value is not None else "").strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not raw:
        if required: raise DataError("Не указана денежная сумма")
        return 0
    if not re.fullmatch(r"\d{1,12}(\.\d{1,2})?", raw):
        raise DataError("Неверная денежная сумма: требуется точность до копейки")
    try:
        cents = int(Decimal(raw) * 100)
    except (InvalidOperation, ValueError):
        raise DataError("Не удалось разобрать сумму") from None
    if cents < 0 or cents > MONEY_LIMIT or (required and cents == 0):
        raise DataError("Сумма вне допустимого диапазона")
    return cents

def amount(cents):
    return f"{cents // 100}.{cents % 100:02d}"

def validate_date(value):
    if not isinstance(value, str):
        raise DataError("Дата должна быть строкой")
    if value:
        if not DATE_PATTERN.fullmatch(value): raise DataError("Дата должна быть ГГГГ-ММ-ДД")
        try: date.fromisoformat(value)
        except ValueError: raise DataError("Несуществующая дата") from None
    return value

def normalized_lines(raw):
    if not isinstance(raw, list) or not (1 <= len(raw) <= 50):
        raise DataError("Нужно от 1 до 50 товарных строк")
    lines = []
    for row in raw:
        if not isinstance(row, dict): raise DataError("Неверная строка товара")
        product = row.get("product", "")
        if not isinstance(product, str) or not product.strip() or len(product) > 200:
            raise DataError("Укажите наименование товара")
        product = product.strip()
        quantity = row.get("quantity", "")
        if isinstance(quantity, bool) or not re.fullmatch(r"[1-9]\d{0,6}", str(quantity)):
            raise DataError("Количество должно быть положительным целым числом")
        qty = int(quantity)
        price = money(row.get("unitPrice"), required=True)
        if price * qty > MONEY_LIMIT:
            raise DataError("Стоимость товарной строки превышает предел")
        lines.append({"product": product, "quantity": qty, "unit_price": price, "line_total": price * qty})
    return lines

class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA busy_timeout=10000")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4):
                raise DataError("Неизвестная версия SQLite: отказ от автоматического изменения")
            present = {row[0] for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            base = {"products", "contracts", "contract_lines", "receipts", "shipments", "operations"}
            if version == 0 and present:
                raise DataError("Неопознанная непустая база без версии: автоматический импорт запрещён")
            if version in (1, 2, 3, 4):
                if not base.issubset(present) or (version >= 2 and "finance_events" not in present) or (version >= 3 and not {"contract_trash","contract_lifecycle"}.issubset(present)) or (version == 4 and not {"managed_files","file_events"}.issubset(present)):
                    raise DataError("В базе отсутствуют обязательные таблицы; исправление без проверки запрещено")
                if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise DataError("Повреждение базы до миграции")
                if self.db.execute("PRAGMA foreign_key_check").fetchall():
                    raise DataError("Нарушение связей до миграции")
            if version in (1,2,3):
                # Online backup before schema changes, including live WAL transactions.
                label={1:"v2",2:"v3",3:"v4"}[version]
                self.backup(self.path.parent / "backups" /
                            ("before-" + label + "-" + uuid.uuid4().hex + ".sqlite"))
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
        except BaseException:
            self.db.close()
            raise
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS products(
                name TEXT PRIMARY KEY, physical_qty INTEGER NOT NULL DEFAULT 0 CHECK(physical_qty>=0),
                inventory_cost INTEGER NOT NULL DEFAULT 0 CHECK(inventory_cost>=0)
            );
            CREATE TABLE IF NOT EXISTS contracts(
                id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision>=1),
                customer TEXT NOT NULL, platform TEXT NOT NULL, status TEXT NOT NULL,
                contract_number TEXT NOT NULL, contract_date TEXT NOT NULL,
                sign_by TEXT NOT NULL, fulfill_by TEXT NOT NULL,
                handover_date TEXT NOT NULL, payment_due TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contract_lines(
                contract_id TEXT NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
                position INTEGER NOT NULL, product TEXT NOT NULL REFERENCES products(name),
                quantity INTEGER NOT NULL CHECK(quantity>0),
                unit_price INTEGER NOT NULL CHECK(unit_price>=0),
                PRIMARY KEY(contract_id,position)
            );
            CREATE TABLE IF NOT EXISTS receipts(
                request_id TEXT PRIMARY KEY, product TEXT NOT NULL REFERENCES products(name),
                qty INTEGER NOT NULL CHECK(qty>0), unit_cost INTEGER NOT NULL CHECK(unit_cost>=0),
                occurred_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shipments(
                contract_id TEXT NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
                product TEXT NOT NULL REFERENCES products(name),
                qty INTEGER NOT NULL CHECK(qty>0), cost INTEGER NOT NULL CHECK(cost>=0),
                PRIMARY KEY(contract_id,product)
            );
            CREATE TABLE IF NOT EXISTS operations(
                request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
                outcome_json TEXT NOT NULL
            );
        """)
        if version == 0:
            self.db.execute("PRAGMA user_version=1")
        if version in (0, 1):
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("""
                    CREATE TABLE IF NOT EXISTS finance_events(
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        op_id TEXT NOT NULL UNIQUE,
                        payload_json TEXT NOT NULL
                    )""")
                self.db.execute("PRAGMA user_version=2")
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        if version in (0,1,2):
            # A failed migration leaves the preceding version untouched.
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("""CREATE TABLE contract_trash(
                    contract_id TEXT PRIMARY KEY REFERENCES contracts(id) ON DELETE RESTRICT,
                    deleted_at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    deleted_revision INTEGER NOT NULL CHECK(deleted_revision>=2))""")
                self.db.execute("""CREATE TABLE contract_lifecycle(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    contract_id TEXT NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
                    action TEXT NOT NULL CHECK(action IN ('trash','restore')),
                    request_id TEXT NOT NULL UNIQUE,
                    changed_at TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision>=2))""")
                self.db.execute("PRAGMA user_version=3")
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        if version in (0,1,2,3):
            # Managed documents: logical deletion preserves bytes for 30 days.
            # A separate file_events journal survives actual blob expiry.
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("""CREATE TABLE managed_files(
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    blob TEXT NOT NULL UNIQUE,
                    contract_id TEXT REFERENCES contracts(id) ON DELETE RESTRICT,
                    size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
                    sha256 TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active'
                      CHECK(state IN ('active','trashed','purge_pending','purged')),
                    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    expires_at TEXT,
                    purged_at TEXT,
                    reason TEXT NOT NULL DEFAULT ''
                )""")
                self.db.execute("""CREATE TABLE file_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL REFERENCES managed_files(id) ON DELETE RESTRICT,
                    request_id TEXT NOT NULL UNIQUE,
                    action TEXT NOT NULL
                       CHECK(action IN ('create','trash','restore','expire','purge')),
                    happened_at TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision>=1)
                )""")
                self.db.execute("CREATE INDEX idx_managed_files_state_expiry ON managed_files(state,expires_at)")
                self.db.execute("PRAGMA user_version=4")
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        if self.db.execute("PRAGMA user_version").fetchone()[0] != 4:
            raise DataError("SQLite managed-file migration failed")
        required=("finance_events","contract_trash","contract_lifecycle","managed_files","file_events")
        if any(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,)).fetchone() is None for name in required):
            raise DataError("Required finance/trash table missing")
        if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise DataError("SQLite integrity failure after migration")
        if self.db.execute("PRAGMA foreign_key_check").fetchall():
            raise DataError("SQLite foreign key failure after migration")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _operation(self, request_id, payload):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,90}", request_id):
            raise DataError("Нужен уникальный идентификатор операции (8–90 символов)")
        digest = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        old = self.db.execute("SELECT payload_hash,outcome_json FROM operations WHERE request_id=?", (request_id,)).fetchone()
        if old:
            if old["payload_hash"] != digest:
                raise Conflict("Идентификатор операции использован с другими данными")
            return digest, json.loads(old["outcome_json"])
        return digest, None

    def _record_op(self, request_id, digest, result):
        self.db.execute("INSERT INTO operations VALUES(?,?,?)",
                        (request_id, digest, json.dumps(result, ensure_ascii=False, sort_keys=True)))

    def add_product(self, name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise DataError("Неверное название товара")
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO products(name) VALUES(?)", (name.strip(),))
        return name.strip()

    def receipt(self, request_id, product, qty, unit_cost):
        if not isinstance(product, str) or not product.strip():
            raise DataError("Не выбран товар")
        if isinstance(qty, bool) or not re.fullmatch(r"[1-9]\d{0,6}", str(qty)):
            raise DataError("Неверное количество прихода")
        count = int(qty)
        cost = money(unit_cost)
        if count * cost > MONEY_LIMIT:
            raise DataError("Стоимость прихода превышает предел")
        payload = {"kind": "receipt", "product": product.strip(), "qty": count, "cost": cost}
        with self.transaction():
            digest, old = self._operation(request_id, payload)
            if old is not None: return old
            item = self.db.execute("SELECT physical_qty FROM products WHERE name=?", (product.strip(),)).fetchone()
            if not item: raise DataError("Товар отсутствует в справочнике")
            current = self._stock(product.strip())
            if current["physical_qty"] + count > 1_000_000_000 or current["inventory_cost"] + count * cost > MONEY_LIMIT:
                raise DataError("Слишком большой остаток или стоимость товара")
            self.db.execute("UPDATE products SET physical_qty=physical_qty+?, inventory_cost=inventory_cost+? WHERE name=?",
                            (count, count * cost, product.strip()))
            self.db.execute("INSERT INTO receipts VALUES(?,?,?,?,?)", (request_id, product.strip(), count, cost, utcnow()))
            result = {"ok": True, "product": product.strip(), "quantity": count}
            self._record_op(request_id, digest, result)
            return result

    def _stock(self, product):
        return self.db.execute("SELECT physical_qty,inventory_cost FROM products WHERE name=?", (product,)).fetchone()

    def _reserved_other(self, product, exclude_id):
        row = self.db.execute("""
            SELECT COALESCE(SUM(l.quantity),0) FROM contract_lines l
            JOIN contracts c ON c.id=l.contract_id
            WHERE c.status='заключен' AND l.product=? AND c.id<>?
              AND NOT EXISTS(SELECT 1 FROM contract_trash t WHERE t.contract_id=c.id)
        """, (product, exclude_id)).fetchone()
        return int(row[0])

    def save_contract(self, request_id, raw):
        if not isinstance(raw, dict): raise DataError("Неверная карточка")
        cid = raw.get("id") or uuid.uuid4().hex
        if not isinstance(cid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{5,90}", cid):
            raise DataError("Неверный идентификатор контракта")
        status = raw.get("status", "формирование")
        if status not in STATUSES: raise DataError("Неизвестный статус")
        customer = raw.get("customer", "")
        platform = raw.get("platform", "")
        contract_number = raw.get("contractNumber", "")
        if not isinstance(customer, str) or len(customer)>500 or not isinstance(platform,str) or len(platform)>120:
            raise DataError("Неверные реквизиты")
        if not isinstance(contract_number,str) or len(contract_number)>100: raise DataError("Номер контракта слишком длинный")
        dates = {key: validate_date(raw.get(key, "")) for key in
                 ("contractDate", "signBy", "fulfillBy", "handoverDate", "paymentDue")}
        if status in ("Вручен", "Оплачен") and not dates["handoverDate"]:
            raise DataError("При вручении укажите фактическую дату")
        lines = normalized_lines(raw.get("items"))
        if sum(x["line_total"] for x in lines) > MONEY_LIMIT:
            raise DataError("Общая сумма контракта слишком велика")
        payload = {"kind":"contract","id":cid,"status":status, "customer":customer,
                   "paymentConfirmed":raw.get("paymentConfirmed") is True,
                   "platform":platform, "number":contract_number, "dates":dates,
                   "lines":lines, "revision":raw.get("revision",0)}
        with self.transaction():
            digest, old = self._operation(request_id,payload)
            if old is not None: return old
            existing = self.db.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone()
            if existing and self.db.execute(
                    "SELECT 1 FROM contract_trash WHERE contract_id=?", (cid,)).fetchone():
                raise Conflict("Контракт находится в Корзине. Сначала восстановите его")
            version = int(raw.get("revision", 0))
            if (existing and existing["revision"] != version) or (not existing and version != 0):
                raise Conflict("Карточка изменена в другом окне")
            prev_status = existing["status"] if existing else ""
            if prev_status in SHIPPED and status not in SHIPPED:
                raise DataError("Возврат на предыдущий этап требует отдельной складской операции (не реализовано)")
            if status == "Оплачен" and prev_status != "Оплачен":
                if prev_status != "Вручен" or raw.get("paymentConfirmed") is not True:
                    raise DataError("Оплату можно подтвердить только после вручения и фактического поступления всей суммы")
            if prev_status == "Оплачен" and status != "Оплачен":
                raise DataError("Отмена оплаты пока не реализована")
            if prev_status not in SHIPPED and status in SHIPPED and prev_status not in ("заключен","Отправлен"):
                raise DataError("Сначала заключите контракт, затем подтвердите отгрузку")
            if status=="заключен" and prev_status!="заключен" and not dates["contractDate"]:
                dates["contractDate"]=moscow_today()
            if existing and status in SHIPPED and existing["contract_date"] and not dates["contractDate"]:
                # A status change must not silently erase the signed contract date.
                dates["contractDate"]=existing["contract_date"]
            old_lines = self.db.execute("SELECT product,quantity,unit_price FROM contract_lines WHERE contract_id=? ORDER BY position",(cid,)).fetchall()
            if prev_status in SHIPPED and [(x["product"],x["quantity"],x["unit_price"]) for x in old_lines] != [
                (x["product"],x["quantity"],x["unit_price"]) for x in lines]:
                raise DataError("Нельзя изменять фактически отгруженные позиции без возврата")
            counts = {}
            for line in lines:
                if not self._stock(line["product"]):
                    raise DataError("Неизвестный товар: "+line["product"])
                counts[line["product"]] = counts.get(line["product"],0)+line["quantity"]
            # Shipment is atomic across all goods and happens once, even when next status is Delivered/Paid.
            if status in SHIPPED and prev_status not in SHIPPED:
                for product, count in counts.items():
                    stock = self._stock(product)
                    if stock["physical_qty"] - self._reserved_other(product,cid) < count:
                        raise DataError("Не хватает товара для отправки: "+product)
                for product,count in counts.items():
                    stock=self._stock(product)
                    physical,cost=stock["physical_qty"],stock["inventory_cost"]
                    issue=cost if count==physical else (2*cost*count+physical)//(2*physical)
                    self.db.execute("UPDATE products SET physical_qty=physical_qty-?,inventory_cost=inventory_cost-? WHERE name=?",
                                    (count,issue,product))
                    self.db.execute("INSERT INTO shipments VALUES(?,?,?,?)",(cid,product,count,issue))
            now=utcnow()
            if existing:
                self.db.execute("""UPDATE contracts SET revision=?,customer=?,platform=?,status=?,contract_number=?,
                    contract_date=?,sign_by=?,fulfill_by=?,handover_date=?,payment_due=?,updated_at=? WHERE id=?""",
                    (version+1,customer,platform,status,contract_number,dates["contractDate"],
                     dates["signBy"],dates["fulfillBy"],dates["handoverDate"],dates["paymentDue"],now,cid))
                self.db.execute("DELETE FROM contract_lines WHERE contract_id=?", (cid,))
            else:
                self.db.execute("INSERT INTO contracts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid,1,customer,platform,status,contract_number,dates["contractDate"],
                     dates["signBy"],dates["fulfillBy"],dates["handoverDate"],dates["paymentDue"],now,now))
            self.db.executemany("INSERT INTO contract_lines VALUES(?,?,?,?,?)",
                [(cid,i+1,l["product"],l["quantity"],l["unit_price"]) for i,l in enumerate(lines)])
            result=self.get_contract(cid)
            self._record_op(request_id,digest,result)
            return result

    def get_contract(self, cid, include_deleted=False):
        r=self.db.execute("SELECT * FROM contracts WHERE id=?",(cid,)).fetchone()
        if r is None: raise DataError("Контракт не найден")
        deleted=self.db.execute(
            "SELECT deleted_at,reason FROM contract_trash WHERE contract_id=?",(cid,)).fetchone()
        if deleted and not include_deleted:
            raise DataError("Контракт находится в Корзине")
        data=dict(r)
        data["deletedAt"]=deleted["deleted_at"] if deleted else None
        data["deletedReason"]=deleted["reason"] if deleted else ""
        data["items"]=[dict(x) for x in self.db.execute(
            "SELECT product,quantity,unit_price FROM contract_lines WHERE contract_id=? ORDER BY position",(cid,))]
        for item in data["items"]:
            item["unitPrice"] = amount(item.pop("unit_price"))
        return data

    def list_contracts(self):
        ids=self.db.execute("""SELECT c.id FROM contracts c
            WHERE NOT EXISTS(SELECT 1 FROM contract_trash t WHERE t.contract_id=c.id)
            ORDER BY c.created_at DESC,c.id DESC""").fetchall()
        return [self.get_contract(r[0]) for r in ids]

    def list_trashed(self):
        ids=self.db.execute("""SELECT contract_id FROM contract_trash
                               ORDER BY deleted_at DESC,contract_id DESC""").fetchall()
        return [self.get_contract(r[0],include_deleted=True) for r in ids]

    def _trash_request(self,request_id,contract_id,expected_revision,kind,reason=""):
        if not isinstance(contract_id,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{5,90}",contract_id):
            raise DataError("Неверный ID контракта")
        if type(expected_revision)!=int or not 1<=expected_revision<=999999999:
            raise DataError("Требуется актуальная ревизия контракта")
        if not isinstance(reason,str) or len(reason)>300:
            raise DataError("Причина удаления: максимум 300 символов")
        payload={"kind":kind,"id":contract_id,"expectedRevision":expected_revision,
                 "reason":reason if kind=="trash" else ""}
        with self.transaction():
            digest,old=self._operation(request_id,payload)
            if old is not None:return old
            record=self.db.execute("SELECT revision,status FROM contracts WHERE id=?",
                                   (contract_id,)).fetchone()
            if record is None:raise DataError("Контракт не найден")
            if record["revision"]!=expected_revision:
                raise Conflict("Карточка уже изменена. Обновите страницу")
            deleted=self.db.execute("SELECT 1 FROM contract_trash WHERE contract_id=?",
                                    (contract_id,)).fetchone()
            if kind=="trash":
                if deleted:raise Conflict("Контракт уже находится в Корзине")
                if record["status"] in SHIPPED:
                    raise DataError("Отгруженный или оплаченный контракт запрещено удалять: складская история должна сохраниться")
            elif not deleted:
                raise Conflict("Контракт уже восстановлен")
            now=utcnow()
            if kind=="trash":
                self.db.execute("INSERT INTO contract_trash VALUES(?,?,?,?)",
                                (contract_id,now,reason,expected_revision+1))
            else:
                self.db.execute("DELETE FROM contract_trash WHERE contract_id=?",
                                (contract_id,))
            self.db.execute("UPDATE contracts SET revision=revision+1,updated_at=? WHERE id=?",
                            (now,contract_id))
            self.db.execute("INSERT INTO contract_lifecycle(contract_id,action,request_id,changed_at,revision) VALUES(?,?,?,?,?)",
                            (contract_id,kind,request_id,now,expected_revision+1))
            result={"ok":True,"id":contract_id,"revision":expected_revision+1,"action":kind}
            self._record_op(request_id,digest,result)
            return result

    def trash_contract(self,request_id,contract_id,expected_revision,reason=""):
        return self._trash_request(request_id,contract_id,expected_revision,"trash",reason)

    def restore_contract(self,request_id,contract_id,expected_revision):
        return self._trash_request(request_id,contract_id,expected_revision,"restore")

    def stock_summary(self):
        results=[]
        for row in self.db.execute("SELECT name,physical_qty,inventory_cost FROM products ORDER BY name"):
            reserved=self._reserved_other(row["name"],"")
            available=row["physical_qty"]-reserved
            results.append({"product":row["name"],"physical":row["physical_qty"],
                            "reserved":reserved,"available":available,"shortage":max(0,-available),
                            "inventoryCost":amount(row["inventory_cost"])})
        return results

    def verify(self):
        integrity=self.db.execute("PRAGMA integrity_check").fetchone()[0]
        fk=self.db.execute("PRAGMA foreign_key_check").fetchall()
        if integrity!="ok" or fk:
            raise DataError("Ошибка целостности базы данных")
        return {"integrity":integrity,"foreignKeys":len(fk),
                "sqliteVersion":sqlite3.sqlite_version,
                "walResetFixed":patched_wal_reset(),
                "journalMode":self.db.execute("PRAGMA journal_mode").fetchone()[0],
                "contracts":len(self.list_contracts()),
                "trashedContracts":self.db.execute("SELECT COUNT(*) FROM contract_trash").fetchone()[0]}

    def backup(self, target):
        target=Path(target).resolve()
        if target == self.path: raise DataError("Нельзя резервировать базу поверх самой себя")
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): raise DataError("Архив уже существует, замена запрещена")
        tmp=target.with_name(target.name+".staging-"+uuid.uuid4().hex)
        try:
            # sqlite3.Connection's context manager commits but does NOT close the file.
            # Explicit close is required before rename on Windows.
            with closing(sqlite3.connect(tmp)) as destination:
                self.db.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise DataError("Снимок SQLite повреждён")
                if destination.execute("PRAGMA foreign_key_check").fetchall():
                    raise DataError("Нарушены внешние ключи")
            digest=sha256(tmp.read_bytes()).hexdigest()
            # os.link publishes only if target is absent, even when another
            # process creates target between the check and this step.
            # Hard links on the same volume are supported by NTFS/APFS/ext4;
            # unsupported file systems fail closed without overwriting data.
            os.link(tmp, target)
            tmp.unlink()
            return {"file":str(target),"sha256":digest,"bytes":target.stat().st_size}
        finally:
            if tmp.exists(): tmp.unlink()
