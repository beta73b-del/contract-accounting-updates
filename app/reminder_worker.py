# -*- coding: utf-8 -*-
"""Фоновая ежедневная сводка «Требует внимания» без запуска Tkinter-интерфейса."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Callable

import db
import email_notify
from calculations import reminder_state, payment_reminder_state, signing_reminder_state

DATE_FMT = "%d.%m.%Y"
STATE_FILENAME = "email_reminder_state.json"
LOCAL_LOCK_FILENAME = "email_reminder_send.lock"
LOCK_STALE_SECONDS = 20 * 60


def _log(message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] Фоновая почта: {message}"
    try:
        with open(db.local_log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _state_path() -> str:
    return os.path.join(db.app_data_dir(), STATE_FILENAME)


def _read_state() -> dict:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return {}


def _write_state(data: dict) -> None:
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp_" + uuid.uuid4().hex
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _acquire_local_send_lock() -> str | None:
    path = os.path.join(db.local_data_dir(), LOCAL_LOCK_FILENAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            if time.time() - os.path.getmtime(path) > LOCK_STALE_SECONDS:
                os.remove(path)
        except OSError:
            pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    try:
        os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}".encode("utf-8"))
    finally:
        os.close(fd)
    return path


def _release_local_send_lock(path: str | None) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _open_background_connection() -> sqlite3.Connection:
    """Предпочитает облачный снимок; локальная рабочая БД — резервный источник."""
    cloud = db.cloud_db_path()
    local = db.default_db_path()
    path = cloud if os.path.exists(cloud) and os.path.getsize(cloud) > 0 else local
    if not os.path.exists(path):
        raise FileNotFoundError("База контрактов ещё не создана.")
    # Режим read/write для app_settings не нужен: отметка отправки хранится отдельным JSON.
    uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _mail_config(conn: sqlite3.Connection) -> tuple[bool, str, list[str], str]:
    enabled = db.get_setting(conn, "email_enabled", "0") == "1"
    sender = email_notify.normalize_email(db.get_setting(conn, "email_sender", "") or "")
    recipients = email_notify.parse_recipients(db.get_setting(conn, "email_recipients", "") or "")
    app_password = (db.get_setting(conn, "email_app_password", "") or "").strip().replace(" ", "")
    return enabled, sender, recipients, app_password


def attention_items(conn: sqlite3.Connection, today: date | None = None):
    """Единый список задач, требующих внимания сегодня.

    Включает:
      * подписание: просрочка / сегодня / 3, 2, 1 день;
      * исполнение: просрочка / 0..14 дней;
      * оплату: просрочка / 0..7 дней;
      * склад: доступный остаток меньше 50 шт.
    """
    today = today or date.today()
    result = []
    for r in db.deadline_rows(conn):
        sign_date = _parse_date(r["sign_deadline"])
        sign_state, sign_days = signing_reminder_state(sign_date, r["contract_status"], today=today)
        if sign_state == "overdue" or (sign_state == "red" and sign_days in (0, 1, 2, 3)):
            result.append({
                "kind": "signing", "type": "Подписание",
                "severity": "overdue" if sign_state == "overdue" else "critical",
                "purchase_id": int(r["id"]), "customer": r["customer"] or "Без заказчика",
                "contract_no": r["contract_no"] or "—", "product": r["product"] or "—",
                "date": sign_date, "days": sign_days,
                "action": "Подписать контракт",
            })

        exec_date = _parse_date(r["deadline"])
        if (r["exec_status"] or "") not in ("Вручен", "Исполнено"):
            exec_state, exec_days = reminder_state(exec_date, r["handover_date"], today=today)
            if exec_state in ("red", "yellow"):
                severity = "overdue" if exec_days is not None and exec_days < 0 else ("critical" if exec_state == "red" else "warning")
                result.append({
                    "kind": "execution", "type": "Исполнение", "severity": severity,
                    "purchase_id": int(r["id"]), "customer": r["customer"] or "Без заказчика",
                    "contract_no": r["contract_no"] or "—", "product": r["product"] or "—",
                    "date": exec_date, "days": exec_days,
                    "action": "Контроль исполнения",
                })

        pay_date = _parse_date(r["payment_deadline"])
        # Единый срок оплаты начинает контролироваться только после фактического вручения.
        pay_state, pay_days = payment_reminder_state(pay_date, r["payment_status"], today=today)
        if r["handover_date"] and pay_state in ("overdue", "red", "yellow"):
            result.append({
                "kind": "payment", "type": "Оплата",
                "severity": "overdue" if pay_state == "overdue" else ("critical" if pay_state == "red" else "warning"),
                "purchase_id": int(r["id"]), "customer": r["customer"] or "Без заказчика",
                "contract_no": r["contract_no"] or "—", "product": r["product"] or "—",
                "date": pay_date, "days": pay_days,
                "action": "Контроль оплаты",
            })

    # Закупка контролируется по товарным позициям, а не по статусу контракта.
    # «Требуется закупка» показывается сразу; «Отложенная закупка» — только с контрольной даты.
    procurement_rows = conn.execute(
        """SELECT i.*, p.customer, p.contract_no, p.deadline, p.deleted_at, p.exec_status
           FROM purchase_items i JOIN purchases p ON p.id=i.purchase_id
           WHERE p.deleted_at IS NULL
             AND COALESCE(p.stock_written_off,0)=0
             AND COALESCE(i.qty,0) > COALESCE(i.stock_qty,0)
             AND COALESCE(i.supply_mode,'Со склада') IN ('Требуется закупка','Отложенная закупка')"""
    ).fetchall()
    for row in procurement_rows:
        deadline = _parse_date(row["deadline"])
        mode = row["supply_mode"] or "Требуется закупка"
        reminder_days = int(row["procurement_reminder_days"] or 30)
        control_date = (deadline - timedelta(days=reminder_days)) if deadline else today
        if mode == "Отложенная закупка" and today < control_date:
            continue
        days = (control_date - today).days
        need = max(0.0, float(row["qty"] or 0) - float(row["stock_qty"] or 0))
        result.append({
            "kind": "procurement", "type": "Закупка",
            "severity": "overdue" if days < 0 else ("critical" if days <= 3 else "warning"),
            "purchase_id": int(row["purchase_id"]), "customer": row["customer"] or "Без заказчика",
            "contract_no": row["contract_no"] or "—", "product": row["product"] or "—",
            "date": control_date, "days": days, "need_qty": need,
            "action": f"Закупить {need:g} шт.",
        })

    for stock in db.stock_summary(conn):
        available = float(stock["available"] or 0.0)
        if available < 50:
            result.append({
                "kind": "stock", "type": "Склад",
                "severity": "critical" if available <= 0 else "warning",
                "purchase_id": None, "customer": "—", "contract_no": "—",
                "product": stock["product"] or "—", "date": None, "days": None,
                "available": available,
                "action": "Пополнить склад" if available <= 0 else "Проверить остаток",
            })

    severity_order = {"overdue": 0, "critical": 1, "warning": 2}
    type_order = {"Подписание": 0, "Исполнение": 1, "Закупка": 2, "Оплата": 3, "Склад": 4}
    result.sort(key=lambda x: (
        severity_order.get(x["severity"], 9),
        x["date"] is None,
        x["date"] or date.max,
        type_order.get(x["type"], 9),
        x.get("contract_no") or "",
        x.get("product") or "",
    ))
    return result


def attention_counts(items):
    counts = {"total": len(items), "overdue": 0, "signing": 0, "execution": 0, "procurement": 0, "payment": 0, "stock": 0}
    for item in items:
        if item.get("severity") == "overdue":
            counts["overdue"] += 1
        counts[item.get("kind")] = counts.get(item.get("kind"), 0) + 1
    return counts


def _days_text(days):
    if days is None:
        return ""
    if days < 0:
        return f"просрочено на {abs(days)} дн."
    if days == 0:
        return "сегодня"
    return f"осталось {days} дн."


def build_attention_email(items, today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    counts = attention_counts(items)
    lines = [
        f"Требует внимания на {today.strftime(DATE_FMT)}",
        "",
        (f"Всего задач: {counts['total']} | Просрочено: {counts['overdue']} | "
         f"Подписание: {counts['signing']} | Исполнение: {counts['execution']} | "
         f"Закупка: {counts['procurement']} | Оплата: {counts['payment']} | Склад: {counts['stock']}"),
    ]
    groups = [("Подписание", "Подписание"), ("Исполнение", "Исполнение"), ("Закупка", "Пора закупать"), ("Оплата", "Ожидают оплаты"), ("Склад", "Склад")]
    for type_name, heading in groups:
        group = [x for x in items if x["type"] == type_name]
        if not group:
            continue
        lines.extend(["", heading + ":"])
        for item in group:
            if item["kind"] == "stock":
                lines.append(
                    f"— {item['product']} | доступно: {item.get('available', 0):g} шт. | {item['action']}"
                )
                continue
            deadline = item["date"].strftime(DATE_FMT) if item.get("date") else "—"
            lines.append(
                f"— {item['customer']} | № {item['contract_no']} | срок: {deadline} | "
                f"{_days_text(item.get('days'))} | {item['action']}"
            )
    lines.extend(["", "Письмо сформировано автоматически программой «Учет заключенных контрактов». "])
    subject = f"Требует внимания — {counts['total']} задач на {today.strftime(DATE_FMT)}"
    return subject, "\n".join(lines)


def run_attention_digest(
    conn: sqlite3.Connection | None = None,
    *,
    today: date | None = None,
    force: bool = False,
    respect_scheduler_host: bool = True,
    send_func: Callable = email_notify.send_gmail,
) -> dict:
    """Отправляет одну ежедневную сводку «Требует внимания»."""
    today = today or date.today()
    today_key = today.isoformat()
    lock = _acquire_local_send_lock()
    if not lock:
        return {"status": "busy", "sent": False}
    own_conn = False
    try:
        if conn is None:
            conn = _open_background_connection()
            own_conn = True
        enabled, sender, recipients, app_password = _mail_config(conn)
        if not enabled:
            return {"status": "disabled", "sent": False}
        if not sender or not recipients or not app_password:
            return {"status": "not_configured", "sent": False}
        configured_host = (db.get_setting(conn, "email_scheduler_host", "") or "").strip()
        current_host = socket.gethostname()
        if respect_scheduler_host and configured_host and configured_host.lower() != current_host.lower():
            return {"status": "other_scheduler_host", "sent": False, "host": configured_host}
        state = _read_state()
        legacy_last = db.get_setting(conn, "last_signing_email_date", "") or ""
        if not force and (state.get("last_sent_date") == today_key or legacy_last == today_key):
            return {"status": "already_sent", "sent": False}
        items = attention_items(conn, today=today)
        if not items:
            return {"status": "nothing_due", "sent": False, "count": 0}
        subject, body = build_attention_email(items, today=today)
        send_func(sender, recipients, subject, body, app_password=app_password)
        counts = attention_counts(items)
        state.update({
            "last_sent_date": today_key,
            "last_sent_at": datetime.now().isoformat(timespec="seconds"),
            "last_sender": sender,
            "count": len(items),
            "counts": counts,
            "computer": current_host,
            "mode": "attention_digest",
        })
        _write_state(state)
        if conn is not None and not own_conn:
            try:
                db.set_setting(conn, "last_signing_email_date", today_key)
            except Exception:
                pass
        _log(f"отправлена сводка «Требует внимания», задач: {len(items)}")
        return {"status": "sent", "sent": True, "count": len(items), "subject": subject, "counts": counts}
    except Exception as exc:
        _log(f"ошибка: {exc}")
        return {"status": "error", "sent": False, "error": str(exc)}
    finally:
        if own_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _release_local_send_lock(lock)


def signing_candidates(conn: sqlite3.Connection, today: date | None = None):
    today = today or date.today()
    rows = db.deadline_rows(conn)
    result = []
    for r in rows:
        deadline = _parse_date(r["sign_deadline"])
        state, days = signing_reminder_state(deadline, r["contract_status"], today=today)
        if state == "red" and days in (3, 2, 1):
            result.append((r, days, deadline))
    result.sort(key=lambda x: (x[2] or date.max, x[0]["contract_no"] or ""))
    return result


def build_email(candidates, today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    lines = ["Необходимо подписать контракты:", ""]
    for r, days, deadline in candidates:
        lines.append(
            f"— {r['customer'] or 'Без заказчика'} | № {r['contract_no'] or '—'} | "
            f"крайняя дата: {deadline.strftime(DATE_FMT)} | осталось: {days} дн."
        )
    lines.extend(["", "Письмо сформировано автоматически программой «Учет заключенных контрактов». "])
    subject = f"Подписание контрактов — напоминание на {today.strftime(DATE_FMT)}"
    return subject, "\n".join(lines)


def run_signing_reminders(
    conn: sqlite3.Connection | None = None,
    *,
    today: date | None = None,
    force: bool = False,
    respect_scheduler_host: bool = True,
    send_func: Callable = email_notify.send_gmail,
) -> dict:
    """Совместимый alias: теперь отправляет ежедневную сводку «Требует внимания»."""
    return run_attention_digest(
        conn, today=today, force=force,
        respect_scheduler_host=respect_scheduler_host, send_func=send_func
    )


def run_headless() -> int:
    """Точка входа для Планировщика Windows. 0 — проверка выполнена/нечего слать; 2 — ошибка."""
    result = run_attention_digest(conn=None, respect_scheduler_host=True)
    _log(f"завершение фоновой проверки: {result.get('status')}")
    return 2 if result.get("status") == "error" else 0