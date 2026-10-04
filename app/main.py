# -*- coding: utf-8 -*-
"""
Учет заключенных контрактов — настольное приложение.
Запуск: python main.py
Сборка в .exe: см. README.md
"""
import os
import shutil
import subprocess
import sys
import socket
import time
import traceback
import textwrap
import itertools
import threading
import unicodedata
import difflib
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog, simpledialog
from datetime import date, datetime

import db
import email_notify
import reminder_worker
import scheduler_win
import theme as app_theme
import auto_update

DEFAULT_UPDATE_REPO = "beta73b-del/contract-accounting-updates"
from app_version import __version__
from calculations import (reminder_state, parse_money, effective_exec_status,
                           payment_reminder_state, signing_reminder_state, calc_contract_price)
try:
    from spellcheck import check_text_spelling
except ImportError:
    check_text_spelling = None


def _log(msg):
    """
    Диагностическое логирование. Пишет и в консоль (видно, если запущено через
    'python main.py' в терминале), и в локальный файл UchetZakupok/app_debug.log
    (виден и при запуске собранного .exe без консоли). Нужно, чтобы понять,
    доходит ли клик пользователя до нашего кода вообще, или проблема раньше —
    в самом окне (не в фокусе / перекрыто / не поднято поверх).
    """
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    try:
        print(line)
    except Exception:
        pass
    try:
        log_path = db.local_log_path()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

DATE_FMT = "%d.%m.%Y"
YEARS_AHEAD = 10
MIN_VALID_YEAR = 2000
MAX_VALID_YEAR = 2100
BASE_FONT_SIZE = 11
TREE_ROW_HEIGHT = 34
PRODUCT_DISPLAY_LIMIT = 35  # символов — компактный показ длинных наименований товара


def fmt_date(iso_str):
    if not iso_str:
        return ""
    try:
        return datetime.strptime(iso_str, "%Y-%m-%d").strftime(DATE_FMT)
    except ValueError:
        return iso_str


def normalize_date_text(text):
    text = (text or "").strip()
    if not text:
        return ""
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            d = datetime.strptime(text, fmt).date()
            return d.strftime(DATE_FMT)
        except ValueError:
            continue
    return text


def parse_date_ru(text):
    text = (text or "").strip()
    if not text:
        return None
    d = datetime.strptime(text, DATE_FMT).date()
    if not (MIN_VALID_YEAR <= d.year <= MAX_VALID_YEAR):
        raise ValueError(
            f"Год даты должен быть в диапазоне {MIN_VALID_YEAR}–{MAX_VALID_YEAR}: {text}"
        )
    return d.isoformat()


def parse_date_iso_to_date(iso_str):
    if not iso_str:
        return None
    try:
        return datetime.strptime(iso_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def prepare_monthly_expense_input(date_text, amount_text, category="", description="", expected_period=None):
    """Валидирует прочий расход.

    Дата расхода справочная. Если выбран период итогов, именно он определяет,
    в какой месяц попадёт расход, независимо от даты.
    """
    normalized = normalize_date_text(date_text)
    expense_iso = parse_date_ru(normalized)
    expense_date = parse_date_iso_to_date(expense_iso)
    if expense_date is None:
        raise ValueError("Укажите корректную дату расхода.")

    amount = parse_money(amount_text)
    if amount <= 0:
        raise ValueError("Сумма расхода должна быть больше нуля.")

    data = {
        "expense_date": expense_iso,
        "category": str(category or "").strip() or "Прочее",
        "amount": amount,
        "description": str(description or "").strip(),
    }
    if expected_period is not None:
        data["period_year"] = int(expected_period[0])
        data["period_month"] = int(expected_period[1])
    return data, expense_date




def prepare_selected_month_expense(amount_text, year, month):
    """Готовит прочий расход для уже выбранного месяца итогов.

    Пользователь вводит только сумму. Техническая дата ставится первым числом
    выбранного месяца и не участвует в выборе периода.
    """
    amount = parse_money(amount_text)
    if amount <= 0:
        raise ValueError("Сумма расхода должна быть больше нуля.")
    year = int(year)
    month = int(month)
    if month < 1 or month > 12:
        raise ValueError("Некорректный месяц.")
    return {
        "expense_date": f"{year:04d}-{month:02d}-01",
        "period_year": year,
        "period_month": month,
        "category": "Прочее",
        "amount": amount,
        "description": "",
    }


def bind_date_autodots(entry, var):
    """Надёжный ввод даты ДД.ММ.ГГГГ без перестановки цифр.

    Цифры обрабатываются самим виджетом: это исключает гонку StringVar/курсора,
    из-за которой на некоторых Windows/Tk при быстром наборе цифры могли вставать
    не по порядку. Поддерживаются редактирование в середине, выделение,
    Backspace/Delete и вставка из буфера.
    """
    state = {"formatting": False}

    def _digits(text):
        return "".join(ch for ch in str(text or "") if ch.isdigit())[:8]

    def _format(digits):
        digits = digits[:8]
        if len(digits) <= 2:
            return digits + ("." if len(digits) == 2 else "")
        if len(digits) <= 4:
            return digits[:2] + "." + digits[2:] + ("." if len(digits) == 4 else "")
        return digits[:2] + "." + digits[2:4] + "." + digits[4:8]

    def _digit_pos_from_char(text, char_index):
        return sum(ch.isdigit() for ch in str(text)[:max(0, int(char_index))])

    def _char_index_after_digits(text, digit_count):
        if digit_count <= 0:
            return 0
        seen = 0
        for idx, ch in enumerate(str(text)):
            if ch.isdigit():
                seen += 1
                if seen >= digit_count:
                    pos = idx + 1
                    # После дня/месяца курсор должен стоять уже ПОСЛЕ
                    # автоматически добавленной точки, иначе визуально кажется,
                    # что следующая цифра вставляется «не по порядку».
                    while pos < len(str(text)) and str(text)[pos] == ".":
                        pos += 1
                    return pos
        return len(str(text))

    def _selection_digit_range(text):
        try:
            if not entry.selection_present():
                return None
            a = int(entry.index("sel.first"))
            b = int(entry.index("sel.last"))
            return (_digit_pos_from_char(text, a), _digit_pos_from_char(text, b))
        except Exception:
            return None

    def _set_digits(digits, cursor_digits=None):
        formatted = _format(digits)
        state["formatting"] = True
        try:
            var.set(formatted)
            if cursor_digits is None:
                cursor_digits = len(_digits(formatted))
            entry.icursor(_char_index_after_digits(formatted, cursor_digits))
            try:
                entry.selection_clear()
            except Exception:
                pass
        finally:
            state["formatting"] = False

    def _on_key_press(event):
        # Ctrl/Alt-комбинации оставляем глобальным обработчикам буфера обмена.
        if getattr(event, "state", 0) & 0x0C:
            return None
        keysym = getattr(event, "keysym", "")
        char = getattr(event, "char", "")
        text = var.get()
        digits = list(_digits(text))
        sel = _selection_digit_range(text)
        cursor_digit = _digit_pos_from_char(text, entry.index("insert"))

        if char.isdigit() and len(char) == 1:
            if sel:
                a, b = sel
                del digits[a:b]
                cursor_digit = a
            if len(digits) < 8:
                digits.insert(min(cursor_digit, len(digits)), char)
                cursor_digit += 1
            _set_digits("".join(digits), cursor_digit)
            return "break"

        if keysym == "BackSpace":
            if sel:
                a, b = sel
                del digits[a:b]
                cursor_digit = a
            elif cursor_digit > 0:
                del digits[cursor_digit - 1]
                cursor_digit -= 1
            _set_digits("".join(digits), cursor_digit)
            return "break"

        if keysym == "Delete":
            if sel:
                a, b = sel
                del digits[a:b]
                cursor_digit = a
            elif cursor_digit < len(digits):
                del digits[cursor_digit]
            _set_digits("".join(digits), cursor_digit)
            return "break"

        # Точки вводить не требуется — они расставляются автоматически. Разрешаем
        # навигацию, Tab, Home/End и альтернативные разделители / и -.
        if char == ".":
            return "break"
        return None

    def _on_change(*_args):
        if state["formatting"]:
            return
        raw = var.get()
        if not raw or "/" in raw or "-" in raw:
            return
        if any(ch not in "0123456789." for ch in raw):
            return
        formatted = _format(_digits(raw))
        if formatted != raw:
            try:
                cursor_digits = _digit_pos_from_char(raw, entry.index("insert"))
            except Exception:
                cursor_digits = len(_digits(raw))
            _set_digits(_digits(raw), cursor_digits)

    def _paste_date(event=None):
        # Если пользователь скопировал реальные файлы в Проводнике Windows,
        # Ctrl+V из любого поля карточки прикрепляет их как документы.
        try:
            file_paths = get_clipboard_file_paths(entry, allow_text_fallback=False)
            top = entry.winfo_toplevel()
            if file_paths and hasattr(top, "_add_documents_from_paths"):
                return top._add_documents_from_paths(file_paths, source="clipboard")
        except Exception:
            pass
        clip = get_clipboard_text(entry)
        if clip is None:
            return "break"
        clip = normalize_clipboard_text(clip).strip()
        clip_digits = _digits(clip)
        # Если в буфере полноценная дата, заменяем выделение/текущее значение целиком.
        if len(clip_digits) >= 6:
            _set_digits(clip_digits, len(clip_digits))
            return "break"
        # Короткую цифровую вставку ведём как последовательный ввод в позицию курсора.
        text = var.get()
        digits = list(_digits(text))
        sel = _selection_digit_range(text)
        cursor_digit = _digit_pos_from_char(text, entry.index("insert"))
        if sel:
            a, b = sel
            del digits[a:b]
            cursor_digit = a
        for ch in clip_digits:
            if len(digits) >= 8:
                break
            digits.insert(min(cursor_digit, len(digits)), ch)
            cursor_digit += 1
        _set_digits("".join(digits), cursor_digit)
        return "break"

    # Контекстное меню мыши использует тот же обработчик, что и Ctrl+V.
    entry._app_paste_handler = _paste_date
    entry.bind("<KeyPress>", _on_key_press, add="+")
    # Ctrl+V / Shift+Insert / вставка через контекстное меню проходят через
    # единый глобальный диспетчер. Он увидит _app_paste_handler и вызовет
    # именно _paste_date, поэтому маска даты не конфликтует с class-bindings.
    entry.bind("<<Paste>>", lambda _e: entry.after_idle(_on_change), add="+")
    var.trace_add("write", _on_change)

def fmt_money(v):
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if n.is_integer():
        number = f"{int(n):,}".replace(",", " ")
    else:
        number = f"{n:,.2f}".replace(",", " ").replace(".", ",")
    return f"{number} ₽"


def fmt_pct(v):
    if v is None:
        return ""
    return f"{v * 100:.1f}%"


def fmt_qty(v):
    if v is None:
        return ""
    if float(v).is_integer():
        return str(int(v))
    return f"{v:g}"



def fmt_product_quantities(values):
    if not values:
        return "—"
    parts = []
    for product, qty in sorted(values.items(), key=lambda kv: str(kv[0]).casefold()):
        parts.append(f"{product} — {fmt_qty(qty)} шт.")
    return "; ".join(parts) if parts else "—"


def fit_dialog_size(req_w, req_h, screen_w, screen_h, min_w=640, min_h=360):
    """Возвращает безопасный размер и позицию небольшого диалога."""
    width = max(int(min_w), int(req_w or 0) + 20)
    height = max(int(min_h), int(req_h or 0) + 20)
    width = min(width, max(320, int(screen_w) - 40))
    height = min(height, max(240, int(screen_h) - 80))
    x = max(0, (int(screen_w) - width) // 2)
    y = max(0, (int(screen_h) - height) // 2)
    return width, height, x, y

def truncate(text, limit=PRODUCT_DISPLAY_LIMIT):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def wrap_customer_name(text, width=34):
    """Переносит полное название заказчика по словам без обрезания текста."""
    text = " ".join(str(text or "").split())
    if not text:
        return "—"
    return "\n".join(textwrap.wrap(
        text, width=width, break_long_words=True, break_on_hyphens=True
    ))


_TREE_WRAP_SEQ = itertools.count(1)

def normalize_clipboard_text(text):
    """Приводит вставляемый текст к обычному Unicode-тексту без чужого форматирования."""
    text = unicodedata.normalize("NFKC", str(text))
    return text.replace("\u00a0", " ").replace("\u202f", " ")


def _tree_font(tree):
    """Возвращает фактический шрифт строк конкретного Treeview."""
    try:
        style = ttk.Style(tree)
        style_name = str(tree.cget("style") or "Treeview")
        font_name = style.lookup(style_name, "font") or style.lookup("Treeview", "font") or "TkDefaultFont"
        return tkfont.nametofont(font_name) if isinstance(font_name, str) else tkfont.Font(font=font_name)
    except Exception:
        return tkfont.nametofont("TkDefaultFont")


def _wrap_line_pixels(text, max_px, font):
    """Переносит одну логическую строку по словам, измеряя реальную ширину в пикселях."""
    text = str(text or "")
    if not text:
        return [""]
    words = text.split()
    if not words:
        return [""]
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if font.measure(candidate) <= max_px:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        # Если само слово шире текущей колонки, Treeview иначе просто обрежет
        # его справа. Поэтому делим только такое слово по символам на минимальные
        # фрагменты, которые реально помещаются. При последующем расширении
        # колонки reflow выполняется заново из исходного текста, и слово снова
        # собирается в одну строку автоматически.
        if font.measure(word) > max_px:
            chunk = ""
            for ch in word:
                trial = chunk + ch
                if chunk and font.measure(trial) > max_px:
                    lines.append(chunk)
                    chunk = ch
                else:
                    chunk = trial
            current = chunk
        else:
            current = word
    if current or not lines:
        lines.append(current)
    return lines


def _center_multiline_lines(lines, font):
    """Визуально центрирует каждую строку многострочного текста внутри общего блока.

    ttk.Treeview умеет центрировать только весь текстовый блок, а строки внутри
    многострочного значения Tcl рисует с левым justify. Добавляем минимальный
    левый отступ более коротким строкам, чтобы они визуально оставались по центру.
    Исходное значение хранится отдельно и используется для копирования/повторного
    переноса, поэтому эти служебные пробелы не попадают в Ctrl+C.
    """
    if len(lines) <= 1:
        return lines
    widths = [font.measure(line) for line in lines]
    max_width = max(widths) if widths else 0
    space_px = max(1, font.measure(" "))
    result = []
    for line, width in zip(lines, widths):
        lead = max(0, int(round((max_width - width) / (2 * space_px))))
        result.append((" " * lead) + line)
    return result


def _wrap_cell_to_width(tree, column, value):
    """Переносит полный текст по текущей фактической ширине столбца Treeview."""
    if value is None:
        return "", 1
    text = str(value)
    if not text:
        return text, 1
    try:
        width_px = int(tree.column(column, "width"))
    except Exception:
        width_px = 120
    max_px = max(24, width_px - 20)
    font = _tree_font(tree)
    out = []
    for original_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        out.extend(_wrap_line_pixels(original_line, max_px, font))
    out = out or [""]
    try:
        anchor = str(tree.column(column, "anchor") or "center")
    except Exception:
        anchor = "center"
    if anchor == "center":
        out = _center_multiline_lines(out, font)
    return "\n".join(out), max(1, len(out))


def _ensure_tree_wrap_style(tree):
    style = ttk.Style(tree)
    style_name = getattr(tree, "_wrap_style_name", None)
    if not style_name:
        current = str(tree.cget("style") or "")
        if current and current != "Treeview":
            style_name = current
        else:
            style_name = f"Wrapped{next(_TREE_WRAP_SEQ)}.Treeview"
            tree.configure(style=style_name)
        tree._wrap_style_name = style_name
    return style, style_name


def _set_tree_wrap_rowheight(tree, lines, allow_shrink=False):
    """Задаёт высоту строк под текущий максимум переноса; при resize умеет уменьшаться."""
    lines = max(1, int(lines or 1))
    old_max = getattr(tree, "_wrap_max_lines", 1)
    if not allow_shrink and lines <= old_max and getattr(tree, "_wrap_style_ready", False):
        return
    tree._wrap_max_lines = lines if allow_shrink else max(old_max, lines)
    style, style_name = _ensure_tree_wrap_style(tree)
    if style_name == "Purchases.Treeview":
        rowheight = max(92, tree._wrap_max_lines * 30 + 12)
        style.configure(style_name, font=(app_theme.FONT, 13), rowheight=rowheight)
    else:
        rowheight = max(TREE_ROW_HEIGHT, tree._wrap_max_lines * 22 + 8)
        style.configure(style_name, font="TkDefaultFont", rowheight=rowheight)
    style.map(style_name, background=[("selected", app_theme.SOFT_BLUE)], foreground=[("selected", app_theme.INK)])
    tree._wrap_style_ready = True


def _rewrap_tree(tree):
    """Пересчитывает перенос всех видимых строк после изменения ширины столбцов."""
    raw_map = getattr(tree, "_raw_tree_values", {})
    columns = list(tree.cget("columns"))
    max_lines = 1
    for iid in tree.get_children(""):
        raw_values = raw_map.get(str(iid))
        if raw_values is None:
            # Для строк, созданных не через tree_insert_wrapped, текущее значение
            # становится исходным один раз.
            raw_values = tuple(tree.item(iid, "values"))
            raw_map[str(iid)] = raw_values
        wrapped = []
        row_lines = 1
        for idx, value in enumerate(raw_values):
            if idx < len(columns):
                cell, lines = _wrap_cell_to_width(tree, columns[idx], value)
            else:
                cell, lines = str(value), 1
            wrapped.append(cell)
            row_lines = max(row_lines, lines)
        tree.item(iid, values=wrapped)
        max_lines = max(max_lines, row_lines)
    tree._raw_tree_values = raw_map
    _set_tree_wrap_rowheight(tree, max_lines, allow_shrink=True)


def _schedule_tree_rewrap(tree, delay=60):
    try:
        job = getattr(tree, "_rewrap_after_id", None)
        if job:
            tree.after_cancel(job)
    except Exception:
        pass
    try:
        tree._rewrap_after_id = tree.after(delay, lambda: _rewrap_tree(tree))
    except tk.TclError:
        pass


def _install_dynamic_tree_wrap(tree):
    """Один раз включает динамический reflow при resize таблицы/колонки.

    На Windows изменение ширины столбца не всегда вызывает <Configure> у самого
    Treeview. Поэтому отслеживаем и перетаскивание разделителя заголовка: во
    время движения мыши выполняется debounce-пересчёт, а после отпускания —
    финальный. Это делает перенос заметным сразу при сужении/расширении.
    """
    if getattr(tree, "_dynamic_wrap_installed", False):
        return
    tree._dynamic_wrap_installed = True
    tree._column_resize_active = False

    def _press(event):
        try:
            tree._column_resize_active = tree.identify_region(event.x, event.y) == "separator"
        except tk.TclError:
            tree._column_resize_active = False

    def _motion(_event):
        if getattr(tree, "_column_resize_active", False):
            _schedule_tree_rewrap(tree, 20)

    def _release(_event):
        if getattr(tree, "_column_resize_active", False):
            tree._column_resize_active = False
        _schedule_tree_rewrap(tree, 20)

    tree.bind("<Configure>", lambda _e: _schedule_tree_rewrap(tree), add="+")
    tree.bind("<ButtonPress-1>", _press, add="+")
    tree.bind("<B1-Motion>", _motion, add="+")
    tree.bind("<ButtonRelease-1>", _release, add="+")


def tree_insert_wrapped(tree, parent="", index="end", *, values=(), **kwargs):
    """Вставляет строку и сохраняет исходный текст для динамического переноса."""
    _install_dynamic_tree_wrap(tree)
    raw_values = tuple("" if v is None else str(v) for v in values)
    columns = list(tree.cget("columns"))
    wrapped = []
    max_lines = 1
    for idx, value in enumerate(raw_values):
        if idx < len(columns):
            cell, lines = _wrap_cell_to_width(tree, columns[idx], value)
        else:
            cell, lines = value, 1
        wrapped.append(cell)
        max_lines = max(max_lines, lines)
    iid = tree.insert(parent, index, values=wrapped, **kwargs)
    raw_map = getattr(tree, "_raw_tree_values", {})
    raw_map[str(iid)] = raw_values
    tree._raw_tree_values = raw_map
    _set_tree_wrap_rowheight(tree, max_lines)
    # Один отложенный пересчёт после серии insert'ов автоматически уменьшит
    # высоту строк, если новые данные короче старых. Повторные вызовы debounce'ятся.
    _schedule_tree_rewrap(tree, 20)
    return iid

def center_tree_columns(tree):
    """Центрирует заголовки и содержимое всех колонок таблицы."""
    for column in tree.cget("columns"):
        try:
            tree.heading(column, anchor="center")
            tree.column(column, anchor="center")
        except tk.TclError:
            pass


def get_clipboard_text(widget):
    try:
        return widget.clipboard_get()
    except tk.TclError:
        return None


def _set_clipboard(widget, text):
    if text is None:
        return
    try:
        widget.clipboard_clear()
        widget.clipboard_append(str(text))
        widget.update_idletasks()
    except tk.TclError:
        pass


def _is_text_widget(widget):
    return isinstance(widget, tk.Text)


def _entry_is_readonly(widget):
    try:
        state = str(widget.cget("state"))
    except Exception:
        return False
    return state in ("readonly", "disabled")


def _copy_from_text_widget(widget):
    """Копировать выделение, а если выделения нет — всё содержимое поля."""
    try:
        if _is_text_widget(widget):
            ranges = widget.tag_ranges("sel")
            if ranges:
                text = widget.get(ranges[0], ranges[1])
            else:
                text = widget.get("1.0", "end-1c")
        else:
            try:
                has_sel = bool(widget.selection_present())
            except Exception:
                has_sel = False
            if has_sel:
                try:
                    first = int(widget.index("sel.first"))
                    last = int(widget.index("sel.last"))
                    text = widget.get()[first:last]
                except Exception:
                    text = widget.selection_get()
            else:
                text = widget.get()
        _set_clipboard(widget, text)
    except tk.TclError:
        pass
    return "break"



def get_clipboard_file_paths(widget, allow_text_fallback=True):
    """Возвращает пути файлов из буфера обмена.

    На Windows читает CF_HDROP (файлы, скопированные в Проводнике). В остальных
    случаях пробует разобрать текстовый список путей. Никаких изменений буфера не
    делает.
    """
    paths = []
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            CF_HDROP = 15
            user32.OpenClipboard.argtypes = [wintypes.HWND]
            user32.OpenClipboard.restype = wintypes.BOOL
            user32.GetClipboardData.argtypes = [wintypes.UINT]
            user32.GetClipboardData.restype = wintypes.HANDLE
            user32.CloseClipboard.argtypes = []
            shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
            shell32.DragQueryFileW.restype = wintypes.UINT
            if user32.OpenClipboard(None):
                try:
                    hdrop = user32.GetClipboardData(CF_HDROP)
                    if hdrop:
                        count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                        for i in range(count):
                            length = shell32.DragQueryFileW(hdrop, i, None, 0)
                            buf = ctypes.create_unicode_buffer(length + 1)
                            shell32.DragQueryFileW(hdrop, i, buf, length + 1)
                            if os.path.isfile(buf.value):
                                paths.append(os.path.abspath(buf.value))
                finally:
                    user32.CloseClipboard()
        except Exception:
            paths = []
    if paths:
        return paths
    if not allow_text_fallback:
        return []

    # Фолбэк нужен только для явной команды «Вставить документы из буфера».
    # Обычный Ctrl+V в текстовом поле не должен превращать строку-путь в вложение.
    try:
        raw = widget.clipboard_get()
    except Exception:
        return []
    if not isinstance(raw, str) or not raw.strip():
        return []
    candidates = []
    try:
        candidates.extend(widget.tk.splitlist(raw))
    except Exception:
        candidates.extend(raw.replace("\r", "\n").split("\n"))
    for item in candidates:
        item = str(item).strip().strip('"').strip("{}")
        if item.startswith("file:///"):
            item = item[8:].replace("/", os.sep)
        elif item.startswith("file://"):
            item = item[7:]
        if os.path.isfile(item):
            path = os.path.abspath(item)
            if path not in paths:
                paths.append(path)
    return paths

def _paste_into_text_widget(widget):
    """Единая вставка из буфера с заменой выделенного текста.

    Поля со специальной маской (например дата) могут зарегистрировать собственный
    обработчик. Файлы из Проводника Windows прикрепляются к карточке только если
    буфер действительно содержит CF_HDROP, а обычная текстовая строка-путь остаётся
    обычным текстом.
    """
    custom = getattr(widget, "_app_paste_handler", None)
    if callable(custom):
        return custom()
    try:
        file_paths = get_clipboard_file_paths(widget, allow_text_fallback=False)
        top = widget.winfo_toplevel()
        if file_paths and hasattr(top, "_add_documents_from_paths"):
            top._add_documents_from_paths(file_paths, source="clipboard")
            return "break"
    except Exception:
        pass
    text = get_clipboard_text(widget)
    if text is None:
        return "break"
    text = normalize_clipboard_text(text)
    try:
        state = str(widget.cget("state")) if hasattr(widget, "cget") else "normal"
    except Exception:
        state = "normal"
    if state == "disabled":
        return "break"
    if isinstance(widget, ttk.Combobox) and state == "readonly":
        values = tuple(str(v) for v in widget.cget("values"))
        candidate = str(text).strip()
        if candidate in values:
            widget.set(candidate)
            widget.event_generate("<<ComboboxSelected>>")
        else:
            try:
                widget.bell()
            except Exception:
                pass
        return "break"
    try:
        if _is_text_widget(widget):
            ranges = widget.tag_ranges("sel")
            if ranges:
                widget.delete(ranges[0], ranges[1])
            widget.insert("insert", text)
            try:
                widget.edit_modified(True)
            except Exception:
                pass
        else:
            # Однострочные поля не должны получать переносы строк из буфера.
            clean = str(text).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
            # Вставка должна происходить именно НА МЕСТО выделения. После delete()
            # Tk не гарантирует, что insert-курсор останется в sel.first.
            insert_at = None
            try:
                if widget.selection_present():
                    insert_at = int(widget.index("sel.first"))
                    widget.delete("sel.first", "sel.last")
            except Exception:
                insert_at = None
            try:
                if insert_at is not None:
                    widget.icursor(insert_at)
                    widget.insert(insert_at, clean)
                    widget.icursor(insert_at + len(clean))
                else:
                    widget.insert("insert", clean)
            except Exception:
                widget.insert("insert", clean)
    except tk.TclError:
        pass
    return "break"


def _cut_from_text_widget(widget):
    try:
        state = str(widget.cget("state")) if hasattr(widget, "cget") else "normal"
    except Exception:
        state = "normal"
    if state in ("readonly", "disabled"):
        return _copy_from_text_widget(widget)
    try:
        if _is_text_widget(widget):
            ranges = widget.tag_ranges("sel")
            if ranges:
                text = widget.get(ranges[0], ranges[1])
                _set_clipboard(widget, text)
                widget.delete(ranges[0], ranges[1])
        else:
            if widget.selection_present():
                text = widget.selection_get()
                _set_clipboard(widget, text)
                widget.delete("sel.first", "sel.last")
    except (tk.TclError, AttributeError):
        pass
    return "break"


def _select_all_text_widget(widget):
    try:
        if _is_text_widget(widget):
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "end-1c")
        else:
            widget.selection_range(0, "end")
            widget.icursor("end")
    except tk.TclError:
        pass
    return "break"


def select_treeview_row(event):
    """Явно выбрать строку любого Treeview по обычному левому клику.

    Это единая страховка для всех таблиц приложения: склад, конкуренты,
    дедлайны, итоги, калькулятор, главная таблица и Treeview в диалогах.
    """
    tree = event.widget
    try:
        row = tree.identify_row(event.y)
    except (tk.TclError, AttributeError):
        return
    if row:
        try:
            # Если штатная обработка Tk уже выбрала строку (в том числе как часть
            # множественного Ctrl/Shift-выделения), не схлопываем выбор до одной
            # строки. Принудительно выбираем только когда клик почему-то не попал
            # в selection — именно этот случай и вызывал проблему в разделах.
            if row not in tree.selection():
                tree.selection_set(row)
            tree.focus(row)
            tree.see(row)
        except tk.TclError:
            pass


def copy_treeview_rows(tree):
    """Копировать выбранные строки Treeview целиком, колонки разделены TAB."""
    try:
        rows = list(tree.selection())
        if not rows:
            focus = tree.focus()
            if focus:
                rows = [focus]
        if not rows:
            return "break"
        lines = []
        raw_map = getattr(tree, "_raw_tree_values", {})
        for iid in rows:
            values = raw_map.get(str(iid), tree.item(iid, "values"))
            clean_values = []
            for value in values:
                cell = "" if value is None else str(value)
                # Многострочные ячейки (например, несколько товаров) оставляем
                # одной строкой в буфере, чтобы вставка в Excel не разъезжалась
                # на дополнительные строки.
                cell = cell.replace("\r\n", " | ").replace("\n", " | ").replace("\r", " | ")
                clean_values.append(cell)
            lines.append("\t".join(clean_values))
        _set_clipboard(tree, "\n".join(lines))
    except tk.TclError:
        pass
    return "break"


def install_global_clipboard_bindings(root):
    """Единые Ctrl+C/Ctrl+V и меню мыши для всех полей и таблиц приложения."""
    def text_menu(event):
        w = event.widget
        menu = tk.Menu(w, tearoff=0)
        menu.add_command(label="Вырезать", command=lambda: _cut_from_text_widget(w))
        menu.add_command(label="Копировать", command=lambda: _copy_from_text_widget(w))
        menu.add_command(label="Вставить", command=lambda: _paste_into_text_widget(w))
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: _select_all_text_widget(w))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass
        return "break"

    def tree_menu(event):
        tree = event.widget
        row = tree.identify_row(event.y)
        if row:
            if row not in tree.selection():
                tree.selection_set(row)
            tree.focus(row)
        menu = tk.Menu(tree, tearoff=0)
        menu.add_command(label="Копировать строку (Ctrl+C)", command=lambda: copy_treeview_rows(tree))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass
        return "break"

    text_classes = ("Entry", "TEntry", "TCombobox", "Text", "Spinbox", "TSpinbox")
    for cls in text_classes:
        for seq in ("<Control-c>", "<Control-C>", "<Control-Insert>"):
            root.bind_class(cls, seq, lambda e: _copy_from_text_widget(e.widget))
        for seq in ("<Control-v>", "<Control-V>", "<Shift-Insert>"):
            root.bind_class(cls, seq, lambda e: _paste_into_text_widget(e.widget))
        # Виртуальное событие покрывает вставку, инициированную самим Tk/системным
        # меню, и делает поведение одинаковым для Entry/Text/Combobox.
        root.bind_class(cls, "<<Paste>>", lambda e: _paste_into_text_widget(e.widget))
        for seq in ("<Control-x>", "<Control-X>"):
            root.bind_class(cls, seq, lambda e: _cut_from_text_widget(e.widget))
        root.bind_class(cls, "<Control-a>", lambda e: _select_all_text_widget(e.widget))
        root.bind_class(cls, "<Control-A>", lambda e: _select_all_text_widget(e.widget))
        root.bind_class(cls, "<Button-3>", text_menu, add="+")

    def _ctrl_v_layout_fallback(event):
        """Страховка для Ctrl+V при неанглийской раскладке, прежде всего Windows.

        На русской раскладке физическая клавиша V может приходить как Cyrillic_em,
        поэтому обычная привязка <Control-v> иногда не срабатывает. Английский
        Ctrl+V уже обрабатывается bind_class выше и до этого обработчика не доходит.
        """
        widget = event.widget
        try:
            widget_class = widget.winfo_class()
        except Exception:
            return None
        if widget_class not in text_classes:
            return None
        keysym = str(getattr(event, "keysym", "") or "").lower()
        keycode = getattr(event, "keycode", None)
        is_v_key = keysym in {"v", "cyrillic_em"}
        if os.name == "nt" and keycode == 86:  # VK_V, независимо от раскладки
            is_v_key = True
        if is_v_key:
            return _paste_into_text_widget(widget)
        return None

    # add="+" не вмешивается в остальные Ctrl-комбинации приложения.
    root.bind_all("<Control-KeyPress>", _ctrl_v_layout_fallback, add="+")

    for seq in ("<Control-c>", "<Control-C>", "<Control-Insert>"):
        root.bind_class("Treeview", seq, lambda e: copy_treeview_rows(e.widget))
    # Общая логика выделения применяется ко ВСЕМ Treeview: главная, итоги,
    # дедлайны, склад, конкуренты, калькулятор и таблицы в дочерних окнах.
    # add="+" сохраняет штатную обработку Tk и добавляет нашу страховку поверх неё.
    root.bind_class("Treeview", "<Button-1>", select_treeview_row, add="+")
    root.bind_class("Treeview", "<Button-3>", tree_menu, add="+")


def open_file_external(path, parent=None):
    """Открыть файл в системном приложении по умолчанию (Windows/macOS/Linux)."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # доступно только на Windows
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception as e:
        messagebox.showerror("Не удалось открыть файл", str(e), parent=parent)


# ============================================================ Карточка контракта
class PurchaseDialog(tk.Toplevel):
    """Карточка контракта с быстрым сценарием создания, товарами и документами."""

    DATE_FIELD_KEYS = ("contract_date", "sign_deadline", "deadline", "handover_date", "payment_deadline", "payment_date")

    HEADER_LABELS = [
        ("platform", "Площадка (ЭТП)", "combo", db.PLATFORM_OPTIONS),
        ("customer", "Заказчик", "entry", None),
        ("contract_no", "Номер контракта", "entry", None),
        ("registry_record", "Реестровая запись", "entry", None),
        ("contract_date", "Дата заключения контракта (ДД.ММ.ГГГГ)", "entry", None),
        ("law", "По какому закону", "combo", db.LAW_OPTIONS),
        ("contract_sum", "Сумма контракта, руб.", "entry", None),
        ("purchase_cost", "Себестоимость, руб.", "entry", None),
        ("logistics", "Стоимость логистики, руб.", "entry", None),
        ("commission", "Комиссия площадки, руб.", "entry", None),
        ("other_costs", "Другие расходы, руб.", "entry", None),
        ("guarantee", "Сумма обеспечения/гарантии, руб.", "entry", None),
        ("contract_status", "Статус контракта", "combo", db.CONTRACT_STATUS_OPTIONS),
        ("sign_deadline", "Подписать до (ДД.ММ.ГГГГ)", "entry", None),
        ("deadline", "Срок исполнения (ДД.ММ.ГГГГ)", "entry", None),
        ("handover_date", "Дата вручения (ДД.ММ.ГГГГ)", "entry", None),
        ("payment_status", "Оплата", "combo", db.PAYMENT_STATUS_OPTIONS),
        ("payment_deadline", "Крайний срок оплаты Заказчиком (ДД.ММ.ГГГГ)", "entry", None),
        ("payment_date", "Дата оплаты (ДД.ММ.ГГГГ)", "entry", None),
        ("exec_status", "Исполнение", "combo", db.EXEC_STATUS_OPTIONS),
        ("note", "Примечание", "text", None),
    ]

    def __init__(self, parent, on_save, existing=None, conn=None, prefill=None):
        super().__init__(parent)
        if existing is None:
            _log("PurchaseDialog: создание новой карточки")
        else:
            _log(f"PurchaseDialog: открытие существующей карточки id={existing['id']}")
        # Временная защита ДО того, как карточка полностью построена: если построение упадёт,
        # окно всё равно можно будет закрыть системным крестиком, а не оно останется "мёртвым".
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        try:
            self._build(parent, on_save, existing, conn, prefill)
        except Exception:
            tb = traceback.format_exc()
            _log(f"PurchaseDialog.__init__: ИСКЛЮЧЕНИЕ ПРИ ПОСТРОЕНИИ КАРТОЧКИ "
                 f"(вот из-за чего она не работала полностью):\n{tb}")
            messagebox.showerror(
                "Ошибка открытия карточки",
                "Не удалось полностью построить карточку контракта — подробности записаны "
                "в файл app_debug.log. Возможно, база данных осталась от старой версии "
                "программы — структура таблиц изменилась. Карточка будет закрыта.",
                parent=parent,
            )
            self.destroy()
            return
        # Построение прошло успешно — включаем настоящий обработчик закрытия с сохранением
        self.protocol("WM_DELETE_WINDOW", self._on_close_button)

    def _build(self, parent, on_save, existing, conn, prefill=None):
        self.title("Новый контракт" if existing is None else f"Контракт #{existing['id']}")
        self.on_save = on_save
        self.existing = existing
        self._initially_new = existing is None
        self.prefill = prefill
        self.conn = conn
        # True только когда НОВАЯ карточка была предварительно сохранена как
        # технический черновик ради прикрепления документов. В этом случае при
        # закрытии нужно UPDATE этого же id, а не второй INSERT через callback
        # главного окна.
        self._draft_created = False
        # Инициализируем до построения секций: новая карточка ещё не имеет ID в БД,
        # поэтому _refresh_documents() не должен обращаться к self.existing["id"].
        self.doc_tree = None
        self.resizable(True, True)
        self._maximized = False

        if existing is not None:
            self.items = [dict(i) for i in existing["items"]]
        elif prefill is not None:
            self.items = [dict(i) for i in prefill.get("items", [])]
        else:
            self.items = []

        # --- Кнопка «Закрыть» закреплена СНИЗУ и всегда видна ---
        btn_frame = ttk.Frame(self)
        btn_frame.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        ttk.Button(btn_frame, text="Сохранить", command=self._save_without_close).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Закрыть", command=self._on_close_button).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="История изменений", command=self._show_history).pack(side="left", padx=4)
        ttk.Label(btn_frame, text="  Ctrl+S — сохранить | Ctrl+Enter — сохранить и закрыть | Esc — закрыть",
                  foreground=app_theme.MUTED).pack(side="left")

        # Вся рабочая часть карточки находится в одной прокручиваемой области.
        body = self._build_vscroll_area(self)

        quick_card_nav = ttk.Frame(body)
        quick_card_nav.pack(fill="x", pady=(0, 6))
        self.quick_docs_var = tk.StringVar(value="Документы: 0 файлов")
        ttk.Button(
            quick_card_nav,
            textvariable=self.quick_docs_var,
            command=self._scroll_to_documents,
        ).pack(side="right")

        content_row = ttk.Frame(body)
        content_row.pack(fill="both", expand=True)

        # --- Левая часть: данные сгруппированы по смыслу, а не одним длинным списком. ---
        left_col = ttk.Frame(content_row)
        left_col.pack(side="left", fill="y", padx=(0, 12))

        self.widgets = {}

        contract_keys = {
            "platform", "customer", "contract_no", "contract_date", "law",
            "contract_sum", "purchase_cost"
        }
        deadline_keys = {"sign_deadline", "deadline", "payment_deadline", "payment_date"}
        state_keys = {"contract_status", "exec_status", "payment_status"}

        contract_specs = [spec for spec in self.HEADER_LABELS if spec[0] in contract_keys]
        deadline_specs = [spec for spec in self.HEADER_LABELS if spec[0] in deadline_keys]
        state_specs = [spec for spec in self.HEADER_LABELS if spec[0] in state_keys]
        primary_keys = contract_keys | deadline_keys | state_keys
        additional_specs = [spec for spec in self.HEADER_LABELS if spec[0] not in primary_keys]

        contract_frame = ttk.LabelFrame(left_col, text="Контракт", padding=8)
        contract_frame.pack(fill="x", pady=(0, 8))
        contract_half = (len(contract_specs) + 1) // 2
        self._build_header_column(contract_frame, contract_specs[:contract_half], col_offset=0)
        self._build_header_column(contract_frame, contract_specs[contract_half:], col_offset=2)

        deadline_frame = ttk.LabelFrame(left_col, text="Сроки", padding=8)
        deadline_frame.pack(fill="x", pady=(0, 8))
        deadline_half = (len(deadline_specs) + 1) // 2
        self._build_header_column(deadline_frame, deadline_specs[:deadline_half], col_offset=0)
        self._build_header_column(deadline_frame, deadline_specs[deadline_half:], col_offset=2)
        ttk.Label(
            deadline_frame,
            text=("«Подписать до» контролируется при статусах «Формирование» и "
                  "«На подписи у Заказчика». После статуса «Заключен» напоминание отключается."),
            wraplength=760, justify="left", foreground=app_theme.MUTED
        ).grid(row=deadline_half, column=0, columnspan=4, sticky="w", pady=(8, 2))

        state_frame = ttk.LabelFrame(left_col, text="Состояние", padding=8)
        state_frame.pack(fill="x", pady=(0, 8))
        state_half = (len(state_specs) + 1) // 2
        self._build_header_column(state_frame, state_specs[:state_half], col_offset=0)
        self._build_header_column(state_frame, state_specs[state_half:], col_offset=2)

        # Информация о сроке и само действие разделены: срок можно быстро
        # прочитать, а на кнопке остаётся только однозначная команда.
        next_action_frame = ttk.LabelFrame(left_col, text="Следующее действие", padding=8)
        next_action_frame.pack(fill="x", pady=(0, 8))
        self.next_action_info_var = tk.StringVar(value="—")
        ttk.Label(
            next_action_frame,
            textvariable=self.next_action_info_var,
            justify="left",
            wraplength=760,
            font=(app_theme.FONT, BASE_FONT_SIZE, "bold"),
        ).pack(fill="x", pady=(0, 6))
        self.next_action_button_var = tk.StringVar(value="Действий не требуется")
        self.next_action_button = ttk.Button(
            next_action_frame,
            textvariable=self.next_action_button_var,
            command=self._perform_next_action,
            style="NextAction.TButton",
        )
        self.next_action_button.pack(fill="x")

        # Сворачиваемый блок «Дополнительно» закреплён в отдельном контейнере
        # ПЕРЕД сотрудниками. Поэтому раскрытие физически раздвигает раскладку:
        # сотрудники уходят ниже, а при сворачивании сразу поднимаются обратно.
        self.additional_visible = False
        self.additional_section = ttk.Frame(left_col)
        self.additional_section.pack(fill="x", pady=(0, 8))

        self.additional_toggle_var = tk.StringVar()
        self.additional_toggle = ttk.Button(
            self.additional_section,
            textvariable=self.additional_toggle_var,
            command=self._toggle_additional_fields,
        )
        self.additional_toggle.pack(fill="x")

        self.additional_frame = ttk.LabelFrame(
            self.additional_section,
            text="Дополнительные сведения",
            padding=8,
        )
        add_half = (len(additional_specs) + 1) // 2
        self._build_header_column(self.additional_frame, additional_specs[:add_half], col_offset=0)
        self._build_header_column(self.additional_frame, additional_specs[add_half:], col_offset=2)
        self._update_additional_visibility()

        # Ответственные сотрудники всегда идут следом за контейнером «Дополнительно».
        self._build_responsible_section(left_col)

        if existing is not None:
            self._fill_header_from_existing(existing)
        else:
            if prefill is not None:
                self._fill_header_from_existing(prefill)
            # Быстрый старт новой карточки: готовые рабочие статусы.
            for key, value in (("contract_status", "Формирование"),
                               ("exec_status", "В процессе")):
                try:
                    if not self.widgets[key].get().strip():
                        self._set_field(key, value)
                except Exception:
                    self._set_field(key, value)
            self._set_field("payment_status", "Не оплачено")

        self._bind_next_action_updates()
        self._refresh_next_action()

        # --- Правая часть: товары в контракте. Отдельный контейнер с фиксированной
        #     минимальной шириной — чтобы широкая левая часть НИКОГДА не вытесняла
        #     таблицу товаров за пределы видимой области. ---
        items_container = ttk.Frame(content_row, width=480)
        items_container.pack(side="left", fill="both", expand=True)
        items_container.pack_propagate(False)

        items_frame = ttk.LabelFrame(items_container, text="Товары в контракте", padding=8)
        items_frame.pack(fill="both", expand=True)

        # Строка добавления товара разбита на ДВЕ подстроки (товар+кол-во сверху,
        # кнопка снизу на всю ширину) — так кнопка «Добавить позицию» никогда не
        # обрезается независимо от ширины окна.
        add_row1 = ttk.Frame(items_frame)
        add_row1.pack(fill="x", pady=(0, 4))
        ttk.Label(add_row1, text="Товар:").pack(side="left")
        self.new_product_var = tk.StringVar()
        product_options = db.distinct_products(self.conn) if self.conn is not None else []
        self.new_product_entry = ttk.Combobox(add_row1, textvariable=self.new_product_var,
                                               values=product_options, width=20)
        self.new_product_entry.pack(side="left", padx=(4, 10), fill="x", expand=True)
        ttk.Label(add_row1, text="Кол-во:").pack(side="left")
        self.new_qty_var = tk.StringVar()
        self.new_qty_entry = ttk.Entry(add_row1, textvariable=self.new_qty_var, width=8)
        self.new_qty_entry.pack(side="left", padx=(4, 0))
        self._bind_context_menu(self.new_product_entry)
        self._bind_context_menu(self.new_qty_entry)

        # Складская подсказка вынесена на отдельную строку, чтобы она не могла
        # вытеснить поле количества за правую границу карточки.
        stock_row = ttk.Frame(items_frame)
        stock_row.pack(fill="x", pady=(0, 4))
        self.product_stock_var = tk.StringVar(value="Склад: —")
        ttk.Label(stock_row, textvariable=self.product_stock_var, foreground=app_theme.ACCENT,
                  anchor="w").pack(fill="x")
        self.new_product_var.trace_add("write", lambda *_: self._update_product_stock_hint())
        self.new_qty_var.trace_add("write", lambda *_: self._update_product_stock_hint())

        supply_row = ttk.Frame(items_frame)
        supply_row.pack(fill="x", pady=(0, 4))
        ttk.Label(supply_row, text="Обеспечение:").pack(side="left")
        self.new_supply_mode_var = tk.StringVar(value="Со склада")
        ttk.Combobox(supply_row, textvariable=self.new_supply_mode_var,
                     values=db.SUPPLY_MODE_OPTIONS, state="readonly", width=20).pack(side="left", padx=(4, 10))
        ttk.Label(supply_row, text="Со склада, шт.:").pack(side="left")
        self.new_stock_qty_var = tk.StringVar()
        ttk.Entry(supply_row, textvariable=self.new_stock_qty_var, width=8).pack(side="left", padx=(4, 10))
        ttk.Label(supply_row, text="Напомнить за, дн.:").pack(side="left")
        self.new_procurement_days_var = tk.StringVar(value="30")
        ttk.Entry(supply_row, textvariable=self.new_procurement_days_var, width=6).pack(side="left", padx=(4, 0))

        add_row2 = ttk.Frame(items_frame)
        add_row2.pack(fill="x", pady=(0, 6))
        ttk.Button(add_row2, text="Добавить позицию", command=self._add_item).pack(fill="x")

        ttk.Label(items_frame, text="Здесь перечисляются ВСЕ товары контракта — их может быть "
                                     "несколько, и количество у каждого своё (например, 1 шт. и 20 шт. "
                                     "в одном контракте одновременно).",
                  wraplength=380, justify="left", foreground=app_theme.MUTED).pack(fill="x", pady=(0, 6))

        self.items_tree = ttk.Treeview(items_frame, columns=("product", "qty", "supply", "stock", "future", "procurement"),
                                        show="headings", height=10)
        for key, label in (("product","Наименование"),("qty","Кол-во"),("supply","Обеспечение"),
                           ("stock","Со склада"),("future","Будущая потребность"),("procurement","Закупка")):
            self.items_tree.heading(key, text=label, anchor="center")
        self.items_tree.column("product", width=220, anchor="center")
        self.items_tree.column("qty", width=70, anchor="center")
        self.items_tree.column("supply", width=150, anchor="center")
        self.items_tree.column("stock", width=85, anchor="center")
        self.items_tree.column("future", width=120, anchor="center")
        self.items_tree.column("procurement", width=190, anchor="center")
        self.items_tree.pack(fill="both", expand=True)
        self.items_tree.bind("<<TreeviewSelect>>", lambda e: self._show_full_product_name())

        item_actions = ttk.Frame(items_frame)
        item_actions.pack(fill="x", pady=(6, 4))
        ttk.Button(item_actions, text="Удалить выбранную позицию",
                   command=self._remove_selected_item).pack(side="left", padx=(0, 6))
        ttk.Button(item_actions, text="Заказано / вернуть в закупку",
                   command=self._toggle_selected_procurement_status).pack(side="left")

        self.full_name_var = tk.StringVar(value="—")
        ttk.Label(items_frame, textvariable=self.full_name_var, wraplength=380,
                  justify="left", foreground=app_theme.ACCENT).pack(fill="x", anchor="w")
        self.selected_stock_var = tk.StringVar(value="Склад по выбранной позиции: —")
        ttk.Label(items_frame, textvariable=self.selected_stock_var, wraplength=380,
                  justify="left", foreground=app_theme.ACCENT).pack(fill="x", anchor="w", pady=(2, 0))

        self._refresh_items_tree()

        # Документы расположены ниже основных данных и товаров и всегда открываются
        # компактно. После добавления первого файла блок раскрывается автоматически.
        self._build_documents_section(body)
        self._refresh_document_advice()

        def _safe_step(name, fn):
            """Выполняет один шаг финализации окна; если он упадёт — логируем и идём дальше,
            а не прерываем весь конструктор карточки (как это, судя по логам, происходило)."""
            try:
                fn()
            except Exception:
                _log(f"PurchaseDialog: шаг '{name}' вызвал исключение (продолжаю дальше):\n"
                     + traceback.format_exc())

        _safe_step("развернуть на весь экран", self._maximize_window)

        # --- Модальность и фокус НАСТРАИВАЕМ В САМОМ КОНЦЕ, когда окно уже полностью
        #     построено и позиционировано. Известная проблема Tkinter: если сделать
        #     grab_set()/transient() ДО того как окно готово и поднято поверх остальных,
        #     оно иногда остаётся "под" родительским окном — выглядит открытым, но клики
        #     фактически попадают мимо (в родительское окно позади). Каждый шаг обёрнут
        #     отдельно: если, например, grab_set() упадёт с ошибкой (бывает на некоторых
        #     системах, если окно ещё не считается "viewable"), это не должно прерывать
        #     остальные шаги и не должно оставлять конструктор карточки незавершённым. ---
        _safe_step("transient", lambda: self.transient(parent))
        _safe_step("update_idletasks", self.update_idletasks)
        _safe_step("deiconify", self.deiconify)
        _safe_step("lift", self.lift)
        _safe_step("attributes(-topmost, True)", lambda: self.attributes("-topmost", True))
        _safe_step("after(topmost off)", lambda: self.after(200, lambda: self.attributes("-topmost", False)))
        _safe_step("focus_force", self.focus_force)
        _safe_step("grab_set", self.grab_set)
        # На части систем Windows transient/grab_set может слегка изменить геометрию
        # Toplevel. Повторно разворачиваем уже полностью построенную карточку, чтобы при
        # повторных открытиях окно не «сползало» вниз и не обрезалось краем экрана.
        _safe_step("финальное разворачивание", self._maximize_window)
        _safe_step("прокрутка карточки в начало", lambda: getattr(self, "_card_canvas", None) and self._card_canvas.yview_moveto(0))
        _log("PurchaseDialog: окно построено, поднято поверх и должно принимать клики")

    def _on_close_button(self):
        _log("Нажата кнопка «Закрыть» или системный крестик окна карточки")
        self._try_close()

    def _maximize_window(self):
        """Карточка всегда открывается на весь экран — так гарантированно видно все поля
        и таблицу товаров, без отдельной кнопки разворачивания."""
        try:
            self.state("zoomed")  # штатный способ на Windows
        except Exception:
            # Резервный вариант, если 'zoomed' недоступен (например, не-Windows система)
            self.update_idletasks()
            w, h = self.winfo_screenwidth(), self.winfo_screenheight()
            # Небольшой запас снизу не даёт кнопкам уйти под панель задач на системах,
            # где Tk не поддерживает state("zoomed").
            self.geometry(f"{max(900, w - 10)}x{max(600, h - 80)}+0+0")
        self._maximized = True

    def _build_vscroll_area(self, parent):
        """Прокручиваемая область карточки.

        Вертикальная прокрутка защищает карточку от обрезания по высоте, а горизонтальная
        включается только когда рабочая область уже содержимого. Это особенно важно на
        небольших экранах и при масштабировании Windows 125–150%.
        """
        outer = ttk.Frame(parent)
        outer.pack(side="top", fill="both", expand=True, padx=12, pady=(6, 0))

        canvas = tk.Canvas(outer, highlightthickness=0)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        hscroll = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)

        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        canvas.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")

        inner = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        self._card_inner = inner
        self._card_canvas = canvas
        self._card_canvas_window = window_id

        def update_scrollregion(_event=None):
            bbox = canvas.bbox("all")
            if bbox:
                canvas.configure(scrollregion=bbox)

        inner.bind("<Configure>", update_scrollregion)

        # Если содержимое уже видимой области, растягиваем его ровно до ширины canvas.
        # Если шире — сохраняем естественную ширину и используем горизонтальную прокрутку.
        def on_canvas_configure(event):
            try:
                req_w = inner.winfo_reqwidth()
                canvas.itemconfigure(window_id, width=max(event.width, req_w))
                update_scrollregion()
            except tk.TclError:
                pass
        canvas.bind("<Configure>", on_canvas_configure)

        def on_mousewheel(event):
            # Binding is installed on the Toplevel itself. Child widgets of this
            # dialog include the toplevel path in their bindtags, so the card
            # scrolls regardless of whether the pointer is over an Entry, Text,
            # Combobox, label, table or empty canvas area.
            delta = getattr(event, "delta", 0)
            if delta:
                steps = -1 if delta > 0 else 1
                # Keep larger/high-resolution wheel deltas useful without making
                # one notch jump excessively far.
                magnitude = max(1, min(4, abs(int(delta / 120)) or 1))
                canvas.yview_scroll(steps * magnitude, "units")
            elif getattr(event, "num", None) == 4:   # Linux/X11 wheel up
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:   # Linux/X11 wheel down
                canvas.yview_scroll(1, "units")

        self.bind("<MouseWheel>", on_mousewheel, add="+")
        self.bind("<Button-4>", on_mousewheel, add="+")
        self.bind("<Button-5>", on_mousewheel, add="+")

        # Горячие клавиши карточки.
        self.bind("<Control-s>", lambda e: (self._save_without_close(), "break")[1], add="+")
        self.bind("<Control-S>", lambda e: (self._save_without_close(), "break")[1], add="+")
        self.bind("<Control-Return>", lambda e: (self._on_close_button(), "break")[1], add="+")
        self.bind("<Escape>", lambda e: (self._on_close_button(), "break")[1], add="+")
        canvas.xview_moveto(0)
        canvas.yview_moveto(0)
        return inner

    def _update_additional_visibility(self):
        if not hasattr(self, "additional_frame"):
            return
        if self.additional_visible:
            if not self.additional_frame.winfo_manager():
                self.additional_frame.pack(fill="x", pady=(6, 0))
            self.additional_toggle_var.set("Дополнительно ▲   Скрыть дополнительные поля")
        else:
            self.additional_frame.pack_forget()
            self.additional_toggle_var.set("Дополнительно ▼   Расходы, примечание и служебные сведения")

    def _toggle_additional_fields(self):
        self.additional_visible = not bool(self.additional_visible)
        self._update_additional_visibility()

    def _build_header_column(self, header_frame, specs, col_offset):
        for row, (key, label, kind, options) in enumerate(specs):
            ttk.Label(header_frame, text=label + ":", width=28, wraplength=210, justify="left").grid(
                row=row, column=col_offset, sticky="w", pady=5, padx=(0 if col_offset == 0 else 20, 10))
            if kind == "combo":
                # У «Площадки» разрешён ручной ввод (список — лишь подсказка), у статусных
                # полей — строго список (readonly), как того требует ТЗ.
                combo_state = "normal" if key == "platform" else "readonly"
                w = ttk.Combobox(header_frame, values=options, width=22, state=combo_state)
                w.grid(row=row, column=col_offset + 1, sticky="w", pady=5)
                if key == "platform":
                    self._bind_context_menu(w)
                elif key in ("contract_status", "exec_status"):
                    w.bind("<<ComboboxSelected>>", lambda e: self._refresh_document_advice(), add="+")
            elif kind == "text":
                w = tk.Text(header_frame, width=24, height=3, font="TkDefaultFont")
                w.grid(row=row, column=col_offset + 1, sticky="w", pady=5)
                self._bind_context_menu(w)
                self._enable_live_spellcheck(w)
            else:
                if key in self.DATE_FIELD_KEYS:
                    date_var = tk.StringVar()
                    w = ttk.Entry(header_frame, width=25, textvariable=date_var)
                    self._bind_date_autodots(w, date_var)
                else:
                    w = ttk.Entry(header_frame, width=25)
                w.grid(row=row, column=col_offset + 1, sticky="w", pady=5)
                self._bind_context_menu(w)
            self.widgets[key] = w

    def _bind_date_autodots(self, entry, var):
        bind_date_autodots(entry, var)

    def _fit_to_screen(self):
        """
        Окно ВСЕГДА должно целиком помещаться в границы экрана — иначе системный крестик
        и кнопка «Закрыть» физически уезжают за пределы видимой области (это и произошло
        в прошлый раз). Поэтому ширину и высоту здесь жёстко ограничиваем размером экрана;
        если содержимому не хватает места — работает вертикальная прокрутка (см.
        _build_vscroll_area), а кнопка «Закрыть» и заголовок с «Развернуть» всё равно
        закреплены и остаются видимыми, т.к. не входят в прокручиваемую область.
        """
        try:
            self.update_idletasks()
            req_w = self.winfo_reqwidth()
            req_h = self.winfo_reqheight()
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            width = min(req_w, screen_w - 40)
            height = min(req_h, screen_h - 70)
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2 - 10)
            self.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
        except Exception:
            pass  # на некоторых системах winfo_screenwidth/height может быть недоступен сразу

    # ---- ответственные лица (два блока размещены РЯДОМ по горизонтали) ----
    def _build_responsible_section(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 8))

        RESP_FIELDS = [
            ("resp_purchase_name", "ФИО"), ("resp_purchase_phone", "Телефон"),
            ("resp_purchase_email", "E-mail"),
        ]
        f1 = ttk.LabelFrame(row, text="Ответственный за закупку", padding=8)
        f1.pack(side="left", fill="both", expand=True, padx=(0, 6))
        for r, (key, label) in enumerate(RESP_FIELDS):
            ttk.Label(f1, text=label + ":").grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
            w = ttk.Entry(f1, width=26)
            w.grid(row=r, column=1, sticky="w", pady=2)
            self.widgets[key] = w

        RESP_FIELDS2 = [
            ("resp_receiving_name", "ФИО"), ("resp_receiving_phone", "Телефон"),
            ("resp_receiving_email", "E-mail"),
        ]
        f2 = ttk.LabelFrame(row, text="Ответственный за получение", padding=8)
        f2.pack(side="left", fill="both", expand=True, padx=(6, 0))
        for r, (key, label) in enumerate(RESP_FIELDS2):
            ttk.Label(f2, text=label + ":").grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
            w = ttk.Entry(f2, width=26)
            w.grid(row=r, column=1, sticky="w", pady=2)
            self.widgets[key] = w
            self._bind_context_menu(w)


    # ---- документы: компактно для новой карточки, раскрыто для существующей ----
    def _build_documents_section(self, parent):
        self.doc_frame = ttk.LabelFrame(parent, text="Документы", padding=8)
        self.doc_frame.pack(fill="x", pady=(0, 8))
        self.doc_expanded = False
        self.doc_count_var = tk.StringVar(value="Документы — 0 файлов")

        top = ttk.Frame(self.doc_frame)
        top.pack(fill="x")
        ttk.Label(top, textvariable=self.doc_count_var, style="Section.TLabel").pack(side="left", padx=(0, 12))
        ttk.Button(top, text="+ Добавить документы...", command=self._add_document).pack(side="left", padx=(0, 6))
        ttk.Button(top, text="Вставить из буфера (Ctrl+V)", command=self._paste_documents_from_clipboard).pack(side="left", padx=(0, 6))
        self.doc_toggle_var = tk.StringVar(value="Скрыть ▲" if self.doc_expanded else "Показать ▼")
        ttk.Button(top, textvariable=self.doc_toggle_var, command=self._toggle_documents).pack(side="right")

        self.doc_body = ttk.Frame(self.doc_frame)
        actions = ttk.Frame(self.doc_body)
        actions.pack(fill="x", pady=(6, 6))
        ttk.Button(actions, text="Открыть...", command=self._open_document).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Сохранить копию...", command=self._download_document).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Изменить категорию", command=self._change_document_category).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Удалить", command=self._delete_document).pack(side="left")

        style = ttk.Style(self)
        style.configure("Documents.Treeview", rowheight=TREE_ROW_HEIGHT)
        style.map("Documents.Treeview",
                  background=[("selected", app_theme.SOFT_BLUE)],
                  foreground=[("selected", app_theme.INK)])

        self.doc_tree = ttk.Treeview(self.doc_body, columns=("category", "filename", "added_date"),
                                      show="headings", height=4, selectmode="browse",
                                      style="Documents.Treeview")
        self.doc_tree.heading("category", text="Категория", anchor="center")
        self.doc_tree.heading("filename", text="Файл", anchor="center")
        self.doc_tree.heading("added_date", text="Добавлен", anchor="center")
        self.doc_tree.column("category", width=150, anchor="center")
        self.doc_tree.column("filename", width=340, anchor="center")
        self.doc_tree.column("added_date", width=100, anchor="center")
        self.doc_tree.pack(fill="x")
        self.doc_selected_var = tk.StringVar(value="Выбран файл: —")
        ttk.Label(self.doc_body, textvariable=self.doc_selected_var).pack(anchor="w", pady=(5, 0))
        self.doc_advice_var = tk.StringVar(value="")
        ttk.Label(self.doc_body, textvariable=self.doc_advice_var, foreground=app_theme.AMBER,
                  wraplength=1100, justify="left").pack(anchor="w", pady=(3, 0))
        self.doc_tree.bind("<<TreeviewSelect>>", self._on_document_select)
        self.doc_tree.bind("<Button-1>", self._on_document_click, add="+")
        self.doc_tree.bind("<Double-1>", lambda e: self._open_selected_document())
        for seq in ("<Control-v>", "<Control-V>"):
            self.doc_tree.bind(seq, self._paste_documents_from_clipboard, add="+")
            self.doc_frame.bind(seq, self._paste_documents_from_clipboard, add="+")
        self._refresh_documents()
        self._update_documents_visibility()

    def _update_documents_visibility(self):
        if not hasattr(self, "doc_body"):
            return
        if self.doc_expanded:
            if not self.doc_body.winfo_manager():
                self.doc_body.pack(fill="x")
            self.doc_toggle_var.set("Скрыть ▲")
        else:
            self.doc_body.pack_forget()
            self.doc_toggle_var.set("Показать ▼")

    def _toggle_documents(self):
        self.doc_expanded = not bool(self.doc_expanded)
        self._update_documents_visibility()

    def _scroll_to_documents(self):
        """Раскрывает документы и прокручивает карточку прямо к ним."""
        if not hasattr(self, "doc_frame"):
            return
        self.doc_expanded = True
        self._update_documents_visibility()
        try:
            self.update_idletasks()
            inner = getattr(self, "_card_inner", None)
            canvas = getattr(self, "_card_canvas", None)
            if inner is None or canvas is None:
                return
            total_h = max(1, inner.winfo_reqheight())
            y = max(0, self.doc_frame.winfo_y() - 8)
            canvas.yview_moveto(min(1.0, y / total_h))
        except tk.TclError:
            pass

    @staticmethod
    def _guess_document_category(path):
        name = os.path.basename(path or "").casefold().replace("ё", "е")
        if "специф" in name:
            return "Спецификация"
        if "упд" in name or "наклад" in name:
            return "УПД / накладная"
        if "платеж" in name or "платежн" in name:
            return "Платёжный документ"
        if "счет" in name:
            return "Счёт"
        # «контракт» содержит последовательность «акт», поэтому эта проверка
        # обязательно должна идти раньше общего правила для актов.
        if "контракт" in name or "договор" in name:
            return "Контракт"
        if "акт" in name:
            return "Акт"
        if "переписк" in name or "письмо" in name:
            return "Переписка"
        return "Прочее"

    @staticmethod
    def _unique_attachment_destination(dest_dir, filename):
        base, ext = os.path.splitext(filename)
        candidate = os.path.join(dest_dir, filename)
        n = 2
        while os.path.exists(candidate):
            candidate = os.path.join(dest_dir, f"{base} ({n}){ext}")
            n += 1
        return candidate

    def _add_documents_from_paths(self, paths, source="picker"):
        paths = [os.path.abspath(str(p)) for p in paths if p and os.path.isfile(str(p))]
        if not paths:
            if source == "clipboard":
                messagebox.showinfo("Документы", "В буфере обмена нет скопированных файлов.", parent=self)
            return "break"
        self._ensure_draft_saved()
        purchase_id = self.existing["id"]
        dest_dir = os.path.join(db.attachments_dir(), str(purchase_id))
        os.makedirs(dest_dir, exist_ok=True)
        added = 0
        errors = []
        for path in paths:
            original_name = os.path.basename(path)
            dest_path = self._unique_attachment_destination(dest_dir, original_name)
            stored_name = os.path.basename(dest_path)
            try:
                shutil.copy2(path, dest_path)
                category = self._guess_document_category(original_name)
                # Имя документа — имя файла. Дополнительный заголовок не требуется.
                db.insert_attachment(self.conn, purchase_id, stored_name, dest_path, category=category)
                added += 1
            except OSError as exc:
                errors.append(f"{original_name}: {exc}")
        self._refresh_documents()
        if added:
            self.doc_expanded = True
            self._update_documents_visibility()
            try:
                last = self.doc_tree.get_children()[-1]
                self.doc_tree.selection_set(last); self.doc_tree.focus(last); self.doc_tree.see(last)
                self._on_document_select()
            except Exception:
                pass
        if errors:
            messagebox.showwarning("Документы", "Не удалось добавить некоторые файлы:\n" + "\n".join(errors[:8]), parent=self)
        return "break"

    def _paste_documents_from_clipboard(self, _event=None):
        return self._add_documents_from_paths(get_clipboard_file_paths(self, allow_text_fallback=True), source="clipboard")

    def _choose_document_category(self):
        win = tk.Toplevel(self)
        win.title("Категория документа")
        win.transient(self); win.resizable(False, False)
        frame = ttk.Frame(win, padding=12); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Выберите категорию документа:").pack(anchor="w", pady=(0, 6))
        var = tk.StringVar(value="Прочее")
        combo = ttk.Combobox(frame, textvariable=var, values=db.DOCUMENT_CATEGORIES,
                             state="readonly", width=28)
        combo.pack(fill="x")
        result = {"value": None}
        def ok(): result["value"] = var.get(); win.destroy()
        buttons = ttk.Frame(frame); buttons.pack(fill="x", pady=(10,0))
        ttk.Button(buttons, text="Выбрать", command=ok).pack(side="left")
        ttk.Button(buttons, text="Отмена", command=win.destroy).pack(side="left", padx=6)
        combo.bind("<Return>", lambda e: ok())
        win.grab_set(); combo.focus_set(); self.wait_window(win)
        return result["value"]

    def _document_stage_warnings(self, header=None):
        """Документы необязательны: отсутствие вложений не создаёт предупреждений."""
        return []

    def _refresh_document_advice(self):
        if not hasattr(self, "doc_advice_var"):
            return
        warnings = self._document_stage_warnings()
        self.doc_advice_var.set(("Подсказка: " + " ".join(warnings)) if warnings else "")

    def _show_history(self):
        if self.existing is None:
            messagebox.showinfo("История изменений", "История появится после первого сохранения контракта.", parent=self)
            return
        pid = self.existing["id"] if "id" in self.existing.keys() else None
        if not pid:
            messagebox.showinfo("История изменений", "История появится после первого сохранения контракта.", parent=self)
            return
        rows = db.fetch_audit(self.conn, int(pid))
        win = tk.Toplevel(self); win.title("История изменений контракта"); win.geometry("900x480"); win.transient(self)
        tree = ttk.Treeview(win, columns=("date","action","details"), show="headings")
        for key,label,width in (("date","Дата и время",170),("action","Действие",180),("details","Изменения",520)):
            tree.heading(key,text=label,anchor="center"); tree.column(key,width=width,anchor="center")
        tree.pack(fill="both",expand=True,padx=10,pady=10)
        for r in rows:
            dt=str(r["event_at"] or "").replace("T"," ")
            tree_insert_wrapped(tree,"","end",values=(dt,r["action"] or "—",r["details"] or "—"))
        ttk.Button(win,text="Закрыть",command=win.destroy).pack(pady=(0,10))

    def _on_document_click(self, event):
        """Явно выбирает строку вложения по клику и сохраняет визуальный фокус."""
        if self.doc_tree is None:
            return
        iid = self.doc_tree.identify_row(event.y)
        if iid:
            self.doc_tree.selection_set(iid)
            self.doc_tree.focus(iid)
            self.doc_tree.see(iid)
            self._on_document_select()

    def _on_document_select(self, _event=None):
        if not hasattr(self, "doc_selected_var"):
            return
        aid = self._selected_document_id()
        if not aid:
            self.doc_selected_var.set("Выбран файл: —")
            return
        att = self._attachment_by_id(aid)
        self.doc_selected_var.set(f"Выбран файл: {att['filename']}" if att else "Выбран файл: —")

    def _refresh_documents(self):
        if self.doc_tree is None:
            return
        for iid in self.doc_tree.get_children():
            self.doc_tree.delete(iid)
        if hasattr(self, "doc_selected_var"):
            self.doc_selected_var.set("Выбран файл: —")
        attachments = []
        if self.existing is not None:
            purchase_id = self.existing.get("id") if hasattr(self.existing, "get") else self.existing["id"]
            if purchase_id:
                attachments = list(db.fetch_attachments(self.conn, purchase_id))
                for a in attachments:
                    tree_insert_wrapped(self.doc_tree, "", "end", iid=str(a["id"]),
                                          values=(a["category"] or "Прочее", a["filename"], fmt_date(a["added_date"])))
        n = len(attachments)
        word = "файл" if n == 1 else ("файла" if 2 <= n <= 4 else "файлов")
        if hasattr(self, "doc_count_var"):
            self.doc_count_var.set(f"Документы — {n} {word}")
        if hasattr(self, "quick_docs_var"):
            self.quick_docs_var.set(f"Документы: {n} {word}")
        self._refresh_document_advice()

    def _ensure_draft_saved(self):
        """Создаёт технический черновик, если ID ещё нет, чтобы документы можно было добавить сразу."""
        if self.existing is not None:
            return self.existing["id"]
        header = {}
        for key, _label, kind, _options in self.HEADER_LABELS:
            w = self.widgets[key]
            header[key] = (w.get("1.0", "end").strip() if kind == "text" else w.get().strip()) or None
        for key in self.DATE_FIELD_KEYS:
            raw = header.get(key)
            if raw:
                header[key] = parse_date_ru(normalize_date_text(raw))
        for key in ("contract_sum", "purchase_cost", "logistics", "commission", "other_costs", "guarantee"):
            raw = header.get(key)
            if raw is not None:
                header[key] = parse_money(raw) if str(raw).strip() else None
        for key in ("resp_purchase_name", "resp_purchase_phone", "resp_purchase_email",
                    "resp_receiving_name", "resp_receiving_phone", "resp_receiving_email"):
            header[key] = self.widgets[key].get().strip() or None
        new_id = db.insert_purchase(self.conn, header, [])
        self.existing = db.fetch_by_id(self.conn, new_id)
        self._draft_created = True
        self.title(f"Контракт #{new_id}")
        _log(f"PurchaseDialog: создан технический черновик id={new_id}; последующее закрытие выполнит UPDATE, а не INSERT")
        return new_id

    def _add_document(self):
        paths = filedialog.askopenfilenames(title="Выберите документы для прикрепления")
        if not paths:
            return
        return self._add_documents_from_paths(paths, source="picker")

    def _selected_document_id(self):
        if self.doc_tree is None:
            return None
        sel = self.doc_tree.selection()
        return int(sel[0]) if sel else None

    def _attachment_by_id(self, attachment_id):
        if self.existing is None or not attachment_id:
            return None
        purchase_id = self.existing.get("id") if hasattr(self.existing, "get") else self.existing["id"]
        if not purchase_id:
            return None
        return next((a for a in db.fetch_attachments(self.conn, purchase_id)
                     if int(a["id"]) == int(attachment_id)), None)

    def _choose_document(self, title="Выберите документ"):
        """Показывает отдельный список вложений и возвращает выбранную запись.

        Кнопки «Открыть» и «Скачать» всегда дают пользователю явный выбор файла,
        даже если строка в основной таблице документов ранее не была выделена.
        """
        if self.existing is None:
            messagebox.showinfo("Документы", "В карточке пока нет прикрепленных файлов.", parent=self)
            return None
        purchase_id = self.existing.get("id") if hasattr(self.existing, "get") else self.existing["id"]
        attachments = list(db.fetch_attachments(self.conn, purchase_id)) if purchase_id else []
        if not attachments:
            messagebox.showinfo("Документы", "В карточке пока нет прикрепленных файлов.", parent=self)
            return None

        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.resizable(True, True)
        win.geometry("720x360")
        win.minsize(560, 280)

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Выберите файл:").pack(anchor="w", pady=(0, 6))
        tree = ttk.Treeview(frame, columns=("category", "filename", "added_date"), show="headings", selectmode="browse")
        tree.heading("category", text="Категория", anchor="center")
        tree.heading("filename", text="Файл", anchor="center")
        tree.heading("added_date", text="Добавлен", anchor="center")
        tree.column("category", width=160, anchor="center")
        tree.column("filename", width=430, anchor="center")
        tree.column("added_date", width=120, anchor="center")
        tree.pack(fill="both", expand=True)
        for a in attachments:
            tree_insert_wrapped(tree, "", "end", iid=str(a["id"]), values=(a["category"] or "Прочее", a["filename"], fmt_date(a["added_date"])))

        current = self._selected_document_id()
        if current is not None and tree.exists(str(current)):
            tree.selection_set(str(current))
            tree.focus(str(current))
            tree.see(str(current))
        elif tree.get_children():
            first = tree.get_children()[0]
            tree.selection_set(first)
            tree.focus(first)

        result = {"id": None}
        def accept(_event=None):
            sel = tree.selection()
            if not sel:
                return
            result["id"] = int(sel[0])
            win.destroy()
        def cancel():
            win.destroy()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Выбрать", command=accept).pack(side="left")
        ttk.Button(buttons, text="Отмена", command=cancel).pack(side="left", padx=(6, 0))
        tree.bind("<Double-1>", accept)
        win.protocol("WM_DELETE_WINDOW", cancel)
        win.grab_set()
        tree.focus_set()
        self.wait_window(win)
        return self._attachment_by_id(result["id"]) if result["id"] else None

    def _open_attachment_record(self, att):
        if not att:
            return
        source = db.resolve_attachment_path(
            att["stored_path"],
            att["purchase_id"] if "purchase_id" in att.keys() else None,
            att["filename"] if "filename" in att.keys() else None,
        )
        if source and os.path.exists(source):
            open_file_external(source, parent=self)
        else:
            messagebox.showerror(
                "Файл не найден",
                "Файл отсутствует в облачной папке attachments. Возможно, облачный диск ещё не закончил синхронизацию.",
                parent=self,
            )

    def _open_document(self):
        att = self._choose_document("Открыть документ")
        if att:
            self._open_attachment_record(att)

    def _open_selected_document(self):
        """Двойной клик открывает уже выбранную строку без второго диалога выбора."""
        aid = self._selected_document_id()
        if aid is None:
            return
        self._open_attachment_record(self._attachment_by_id(aid))

    def _download_document(self):
        att = self._choose_document("Скачать документ")
        if not att:
            return
        source = db.resolve_attachment_path(
            att["stored_path"],
            att["purchase_id"] if "purchase_id" in att.keys() else None,
            att["filename"] if "filename" in att.keys() else None,
        )
        if not source or not os.path.exists(source):
            messagebox.showerror(
                "Файл не найден",
                "Файл отсутствует в облачной папке attachments. Возможно, облачный диск ещё не закончил синхронизацию.",
                parent=self,
            )
            return
        ext = os.path.splitext(att["filename"] or "")[1]
        target = filedialog.asksaveasfilename(
            title="Сохранить документ как",
            initialfile=att["filename"] or os.path.basename(source),
            defaultextension=ext,
            parent=self,
        )
        if not target:
            return
        try:
            shutil.copy2(source, target)
            messagebox.showinfo("Готово", "Файл сохранен в выбранную папку.", parent=self)
        except OSError as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить файл:\n{e}", parent=self)

    def _change_document_category(self):
        aid = self._selected_document_id()
        if not aid:
            messagebox.showinfo("Документы", "Сначала выберите документ.", parent=self)
            return
        category = self._choose_document_category()
        if not category:
            return
        db.update_attachment_category(self.conn, aid, category)
        self._refresh_documents()

    def _delete_document(self):
        aid = self._selected_document_id()
        if aid is None:
            messagebox.showinfo("Выбор файла", "Сначала выберите документ в списке.", parent=self)
            return
        if messagebox.askyesno("Удаление", "Удалить документ безвозвратно?", parent=self):
            db.delete_attachment(self.conn, aid)
            self._refresh_documents()

    # ---- товары внутри контракта ----
    def _refresh_items_tree(self):
        for iid in self.items_tree.get_children():
            self.items_tree.delete(iid)
        for i, item in enumerate(self.items):
            qty = float(item.get("qty") or 0)
            mode = item.get("supply_mode") or "Со склада"
            stock_qty = float(item.get("stock_qty") if item.get("stock_qty") is not None else (qty if mode == "Со склада" else 0))
            future_qty = max(0.0, qty - stock_qty)
            status = item.get("procurement_status") or "Не начата"
            if mode == "Со склада" or future_qty <= 0:
                procurement_text = "—"
            elif status == "Заказано":
                procurement_text = "Заказано / ожидается поступление"
            else:
                procurement_text = "Не начата"
            tree_insert_wrapped(self.items_tree, "", "end", iid=str(i),
                                values=(item.get("product") or "—", fmt_qty(qty), mode,
                                        fmt_qty(stock_qty), fmt_qty(future_qty), procurement_text))

    def _toggle_selected_procurement_status(self):
        sel = self.items_tree.selection()
        if not sel:
            messagebox.showinfo("Закупка", "Сначала выберите товарную позицию.", parent=self)
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(self.items):
            return
        item = self.items[idx]
        mode = item.get("supply_mode") or "Со склада"
        qty = float(item.get("qty") or 0)
        stock_qty = float(item.get("stock_qty") or 0)
        if mode == "Со склада" or qty <= stock_qty:
            messagebox.showinfo("Закупка", "Для этой позиции закупка не требуется.", parent=self)
            return
        current = item.get("procurement_status") or "Не начата"
        item["procurement_status"] = "Не начата" if current == "Заказано" else "Заказано"
        self._refresh_items_tree()
        try:
            self.items_tree.selection_set(str(idx))
            self.items_tree.focus(str(idx))
        except Exception:
            pass

    def _bind_next_action_updates(self):
        keys = ("contract_status", "sign_deadline", "deadline", "exec_status",
                "payment_deadline", "payment_status", "handover_date")
        for key in keys:
            w = self.widgets.get(key)
            if w is None:
                continue
            try:
                w.bind("<KeyRelease>", lambda e: self._refresh_next_action(), add="+")
                w.bind("<FocusOut>", lambda e: self._refresh_next_action(), add="+")
                w.bind("<<ComboboxSelected>>", lambda e: self._refresh_next_action(), add="+")
            except Exception:
                pass

    def _refresh_next_action(self):
        if not hasattr(self, "next_action_info_var"):
            return

        def get(key):
            w = self.widgets.get(key)
            return (w.get().strip() if w is not None else "")

        def parse_ui(key):
            raw = get(key)
            if not raw:
                return None
            try:
                return parse_date_iso_to_date(parse_date_ru(normalize_date_text(raw)))
            except Exception:
                return None

        def deadline_info(prefix, d):
            days = (d - date.today()).days
            shown = d.strftime(DATE_FMT)
            if days < 0:
                return f"{prefix} {shown} · ПРОСРОЧЕНО на {abs(days)} дн.", "overdue"
            if days == 0:
                return f"{prefix} {shown} · сегодня", "today"
            if days == 1:
                return f"{prefix} {shown} · завтра", "soon"
            return f"{prefix} {shown} · осталось {days} дн.", ("soon" if days <= 3 else "normal")

        contract_status = get("contract_status")
        exec_status = get("exec_status")
        payment_status = get("payment_status")
        info_text = "—"
        action_text = "Действий не требуется"
        state = "normal"
        self._next_action_target = None

        if contract_status != "Заключен":
            d = parse_ui("sign_deadline")
            if d:
                info_text, state = deadline_info("Подписать до", d)
            else:
                info_text, state = "Срок подписания не указан", "soon"
            action_text = "Отметить контракт заключённым"
            self._next_action_target = ("contract_status", "Заключен", "contract_date", "Дата заключения")
        elif exec_status not in ("Вручен", "Исполнено"):
            d = parse_ui("deadline")
            if d:
                info_text, state = deadline_info("Исполнить до", d)
            else:
                info_text = "Срок исполнения не указан"
            if exec_status == "Отправлено":
                action_text = "Отметить товар врученным"
                self._next_action_target = ("exec_status", "Вручен", "handover_date", "Дата вручения")
            else:
                action_text = "Отметить товар отправленным"
                self._next_action_target = ("exec_status", "Отправлено", None, None)
        elif payment_status != "Оплачено":
            d = parse_ui("payment_deadline")
            if d:
                info_text, state = deadline_info("Оплата до", d)
            else:
                info_text = "Срок оплаты не указан"
            action_text = "Отметить контракт оплаченным"
            self._next_action_target = ("payment_status", "Оплачено", "payment_date", "Дата оплаты")
        else:
            info_text = "Контракт завершён — действий не требуется"

        self.next_action_info_var.set(info_text)
        self.next_action_button_var.set(action_text)
        style_by_state = {
            "overdue": "NextActionDanger.TButton",
            "today": "NextActionDanger.TButton",
            "soon": "NextActionWarning.TButton",
            "normal": "NextAction.TButton",
        }
        try:
            if self._next_action_target:
                self.next_action_button.configure(
                    style=style_by_state.get(state, "NextAction.TButton"),
                    state="normal",
                )
            else:
                self.next_action_button.configure(style="NextActionDone.TButton", state="disabled")
        except Exception:
            pass

    def _perform_next_action(self, _event=None):
        target = getattr(self, "_next_action_target", None)
        if not target:
            return "break"
        field, value, date_field, date_label = target
        contract_no = ""
        try:
            contract_no = self.widgets.get("contract_no").get().strip()
        except Exception:
            pass
        title = contract_no or "новый контракт"
        lines = [f"Отметить {title} как «{value}»?"]
        if date_field:
            current = ""
            try:
                current = self.widgets[date_field].get().strip()
            except Exception:
                pass
            shown = current or date.today().strftime(DATE_FMT)
            lines.append(f"{date_label}: {shown}" + ("" if current else " (сегодня)"))
        if not messagebox.askyesno("Следующее действие", "\n".join(lines), parent=self):
            return "break"
        self._set_field(field, value)
        if date_field:
            try:
                if not self.widgets[date_field].get().strip():
                    self._set_field(date_field, date.today().strftime(DATE_FMT))
            except Exception:
                self._set_field(date_field, date.today().strftime(DATE_FMT))
        self._refresh_next_action()
        # Для существующего контракта сохраняем только переход этапа и связанную
        # дату. Так действие остаётся действительно быстрым: одно подтверждение,
        # без повторной полной проверки карточки и без второго диалога. Остальные
        # несохранённые правки в открытой карточке остаются в полях и сохранятся
        # обычным Ctrl+S/закрытием.
        try:
            existing_id = None
            if self.existing is not None:
                try:
                    existing_id = self.existing.get("id") if hasattr(self.existing, "get") else self.existing["id"]
                except Exception:
                    existing_id = None
            if existing_id:
                pid = int(existing_id)
                latest = db.fetch_by_id(self.conn, pid)
                if latest is not None:
                    header = {f: latest[f] for f in db.HEADER_FIELDS}
                    header[field] = value
                    if date_field and not header.get(date_field):
                        header[date_field] = date.today().isoformat()
                    db.update_purchase(self.conn, pid, header, [dict(x) for x in latest["items"]])
                    self.existing = db.fetch_by_id(self.conn, pid)
                    owner = self.master
                    if owner is not None and hasattr(owner, "refresh_all"):
                        owner.after_idle(owner.refresh_all)
        except Exception:
            _log("Ошибка быстрого следующего действия:\n" + traceback.format_exc())
            messagebox.showerror("Следующее действие", "Не удалось сохранить новый статус.", parent=self)
        return "break"

    def _stock_row_for_product(self, product):
        if not product or self.conn is None:
            return None
        return next((r for r in db.stock_summary(self.conn) if r["product"] == product), None)

    def _show_full_product_name(self):
        sel = self.items_tree.selection()
        if not sel:
            self.full_name_var.set("—")
            if hasattr(self, "selected_stock_var"):
                self.selected_stock_var.set("Склад по выбранной позиции: —")
            return
        idx = int(sel[0])
        item = self.items[idx]
        product = item.get("product") or ""
        qty = float(item.get("qty") or 0)
        self.full_name_var.set(product or "—")
        row = self._stock_row_for_product(product)
        current_id = self.existing["id"] if self.existing is not None else None
        available_for_contract = db.available_for_contract(self.conn, product, current_id) if product and self.conn else 0.0
        if row is None:
            on_hand = reserved = available = 0.0
        else:
            on_hand = float(row["on_hand"] or 0)
            reserved = float(row["reserved"] or 0)
            available = float(row["available"] or 0)
        deficit = max(0.0, qty - available_for_contract)
        extra = f" | НЕ ХВАТАЕТ {fmt_qty(deficit)} шт." if deficit > 0 else " | остатка достаточно"
        self.selected_stock_var.set(
            f"Склад: физически {fmt_qty(on_hand)} шт. | резерв {fmt_qty(reserved)} | "
            f"свободно {fmt_qty(available)} | доступно для этого контракта {fmt_qty(available_for_contract)} | "
            f"позиция {fmt_qty(qty)} шт.{extra}"
        )

    def _update_product_stock_hint(self):
        product = self.new_product_var.get().strip()
        if not product or self.conn is None:
            self.product_stock_var.set("Склад: —")
            return
        row = self._stock_row_for_product(product)
        current_id = self.existing["id"] if self.existing is not None else None
        available_for_contract = db.available_for_contract(self.conn, product, current_id)
        if row is None:
            on_hand = reserved = available = 0.0
        else:
            on_hand = float(row["on_hand"] or 0)
            reserved = float(row["reserved"] or 0)
            available = float(row["available"] or 0)
        suffix = ""
        qty_text = self.new_qty_var.get().strip() if hasattr(self, "new_qty_var") else ""
        if qty_text:
            try:
                qty = parse_money(qty_text)
                if qty > available_for_contract:
                    suffix = f" | НЕ ХВАТАЕТ {fmt_qty(qty - available_for_contract)} шт."
                elif qty > 0:
                    suffix = " | остатка достаточно"
            except ValueError:
                pass
        self.product_stock_var.set(
            f"Склад: физически {fmt_qty(on_hand)} шт. | резерв {fmt_qty(reserved)} | "
            f"свободно {fmt_qty(available)} | доступно для этого контракта {fmt_qty(available_for_contract)}{suffix}"
        )

    def _add_item(self):
        product = self.new_product_var.get().strip()
        qty_text = self.new_qty_var.get().strip()
        if not product:
            messagebox.showerror("Ошибка ввода", "Укажите наименование товара.", parent=self)
            return
        try:
            qty = parse_money(qty_text) if qty_text else 0.0
        except ValueError:
            messagebox.showerror("Ошибка ввода", "Количество должно быть положительным числом.", parent=self)
            return
        if qty == 0:
            messagebox.showerror("Ошибка ввода", "Количество товара должно быть больше нуля.", parent=self)
            return
        product = self._canonical_product_input(product)
        mode = self.new_supply_mode_var.get() or "Со склада"
        try:
            stock_qty = parse_money(self.new_stock_qty_var.get()) if self.new_stock_qty_var.get().strip() else (qty if mode == "Со склада" else 0.0)
            reminder_days = int(self.new_procurement_days_var.get() or 30)
        except ValueError:
            messagebox.showerror("Ошибка ввода", "Проверьте количество со склада и срок напоминания.", parent=self)
            return
        if stock_qty < 0 or stock_qty > qty:
            messagebox.showerror("Ошибка ввода", "Количество со склада должно быть от 0 до общего количества.", parent=self)
            return
        self.items.append({"product": product, "qty": qty, "supply_mode": mode,
                           "stock_qty": stock_qty, "procurement_reminder_days": max(0, reminder_days),
                           "procurement_status": "Не начата"})
        self.new_product_var.set("")
        self.new_qty_var.set("")
        self.new_stock_qty_var.set("")
        self.new_supply_mode_var.set("Со склада")
        self._refresh_items_tree()

    def _auto_commit_pending_item(self):
        """
        Вызывается при попытке закрыть карточку. Если пользователь напечатал товар/количество
        в строке добавления, но забыл нажать «Добавить позицию» — фиксируем это автоматически,
        чтобы закрытие карточки не «съедало» уже введённые данные без явной причины.
        Возвращает (ok, error_message).
        """
        product = self.new_product_var.get().strip()
        qty_text = self.new_qty_var.get().strip()
        if not product and not qty_text:
            return True, None  # строка добавления пуста — добавлять нечего, это не ошибка
        # Один только выбранный/введённый товар без количества ещё не является
        # добавленной позицией. Такое состояние часто возникает просто после выбора
        # товара ради просмотра складского остатка и не должно блокировать закрытие.
        if product and not qty_text:
            _log("_auto_commit_pending_item: товар выбран, количество не задано -> незавершённую строку игнорирую")
            self.new_product_var.set("")
            self.new_qty_var.set("")
            self._update_product_stock_hint()
            return True, None
        if not product:
            return False, "В строке добавления товара указано количество, но не указано наименование."
        try:
            qty = parse_money(qty_text) if qty_text else 0.0
        except ValueError:
            return False, "Количество в строке добавления товара должно быть положительным числом."
        if qty < 0:
            return False, "Количество в строке добавления товара должно быть больше нуля."
        if qty == 0:
            _log("_auto_commit_pending_item: товар выбран, количество равно 0 -> незавершённую строку игнорирую")
            self.new_product_var.set("")
            self.new_qty_var.set("")
            self._update_product_stock_hint()
            return True, None
        product = self._canonical_product_input(product)
        mode = self.new_supply_mode_var.get() or "Со склада"
        try:
            stock_qty = parse_money(self.new_stock_qty_var.get()) if self.new_stock_qty_var.get().strip() else (qty if mode == "Со склада" else 0.0)
            reminder_days = int(self.new_procurement_days_var.get() or 30)
        except ValueError:
            return False, "Проверьте количество со склада и срок напоминания о закупке."
        if stock_qty < 0 or stock_qty > qty:
            return False, "Количество со склада должно быть от 0 до общего количества."
        self.items.append({"product": product, "qty": qty, "supply_mode": mode,
                           "stock_qty": stock_qty, "procurement_reminder_days": max(0, reminder_days)})
        self.new_product_var.set("")
        self.new_qty_var.set("")
        self.new_stock_qty_var.set("")
        self.new_supply_mode_var.set("Со склада")
        self._refresh_items_tree()
        return True, None

    def _canonical_product_input(self, product):
        product = " ".join(str(product or "").split()).strip()
        if not product or self.conn is None:
            return product
        existing = db.catalog_products(self.conn)
        key = db.normalize_product_key(product)
        for name in existing:
            if db.normalize_product_key(name) == key:
                return name
        if existing:
            best = max(existing, key=lambda n: difflib.SequenceMatcher(None, key, db.normalize_product_key(n)).ratio())
            score = difflib.SequenceMatcher(None, key, db.normalize_product_key(best)).ratio()
            if score >= 0.90:
                if messagebox.askyesno("Похожий товар",
                                       f"В справочнике уже есть похожий товар:\n\n{best}\n\nИспользовать его вместо «{product}»?",
                                       parent=self):
                    return best
        return db.ensure_product(self.conn, product)

    def _apply_auto_status_suggestions(self, header):
        # Дата вручения означает только «Вручен», но не «Исполнено».
        if header.get("handover_date") and (header.get("exec_status") or "") not in ("Вручен", "Исполнено"):
            header["exec_status"] = "Вручен"
            self.widgets["exec_status"].set("Вручен")

        # Оплата фиксируется отдельной датой. При быстром действии дата уже
        # подставлена, а при ручном выборе «Оплачено» без даты используем сегодня.
        if (header.get("payment_status") or "") == "Оплачено":
            if not header.get("payment_date"):
                header["payment_date"] = date.today().isoformat()
                try:
                    self.widgets["payment_date"].set(date.today().strftime(DATE_FMT))
                except Exception:
                    pass
        else:
            header["payment_date"] = None
            try:
                self.widgets["payment_date"].set("")
            except Exception:
                pass

        # Исполнение завершается только после оплаты уже вручённого контракта.
        delivered = bool(header.get("handover_date")) or (header.get("exec_status") or "") in ("Вручен", "Исполнено")
        if (header.get("payment_status") or "") == "Оплачено" and delivered:
            header["exec_status"] = "Исполнено"
            self.widgets["exec_status"].set("Исполнено")
        elif (header.get("exec_status") or "") == "Исполнено" and (header.get("payment_status") or "") != "Оплачено":
            header["exec_status"] = "Вручен" if delivered else "В процессе"
            self.widgets["exec_status"].set(header["exec_status"])
        return header

    def _smart_warnings(self, header):
        warnings=[]
        current_id = self.existing["id"] if self.existing is not None else None
        duplicates = db.duplicate_contracts(self.conn, header.get("contract_no"), exclude_id=current_id)
        if duplicates:
            d=duplicates[0]
            warnings.append(f"В базе уже есть контракт № {d['contract_no']} — {fmt_date(d['contract_date']) or 'без даты'}, {d['customer'] or 'без заказчика'}.")
        cdate=parse_date_iso_to_date(header.get("contract_date"))
        deadline=parse_date_iso_to_date(header.get("deadline"))
        handover=parse_date_iso_to_date(header.get("handover_date"))
        pay=parse_date_iso_to_date(header.get("payment_deadline"))
        paid_on=parse_date_iso_to_date(header.get("payment_date"))
        if cdate and deadline and deadline < cdate:
            warnings.append("Срок исполнения раньше даты заключения контракта.")
        if cdate and handover and handover < cdate:
            warnings.append("Дата вручения раньше даты заключения контракта.")
        if cdate and pay and pay < cdate:
            warnings.append("Крайний срок оплаты раньше даты заключения контракта.")
        if handover and paid_on and paid_on < handover:
            warnings.append("Дата оплаты раньше даты вручения.")
        total_cost=sum(float(header.get(k) or 0) for k in ("purchase_cost","logistics","commission","other_costs","guarantee"))
        if header.get("contract_sum") is not None and total_cost > float(header.get("contract_sum") or 0):
            warnings.append(f"Расходы ({fmt_money(total_cost)}) больше суммы контракта ({fmt_money(header.get('contract_sum'))}).")
        if (header.get("payment_status") or "") == "Оплачено" and not header.get("handover_date"):
            warnings.append("Контракт отмечен как оплаченный, но дата вручения не заполнена. Статус «Исполнено» будет установлен только после вручения.")
        # Проверка остатка по каждой позиции с исключением собственного текущего резерва.
        for item in self.items:
            product=item.get("product") or ""; qty=float(item.get("qty") or 0)
            if product and qty > 0:
                available=db.available_for_contract(self.conn, product, current_id)
                if qty > available:
                    warnings.append(f"{product}: требуется {fmt_qty(qty)} шт., доступно {fmt_qty(available)} шт.; не хватает {fmt_qty(qty-available)} шт.")
        warnings.extend(self._document_stage_warnings(header))
        return warnings

    def _remove_selected_item(self):
        sel = self.items_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        del self.items[idx]
        self._refresh_items_tree()
        self.full_name_var.set("—")

    # ---- буфер обмена ----
    def _bind_context_menu(self, entry):
        # Расширенное меню карточки: общий буфер обмена + проверка правописания.
        # Возвращаем break, чтобы не открывалось второе глобальное меню поверх этого.
        menu = tk.Menu(entry, tearoff=0)
        menu.add_command(label="Вырезать", command=lambda: _cut_from_text_widget(entry))
        menu.add_command(label="Копировать", command=lambda: _copy_from_text_widget(entry))
        menu.add_command(label="Вставить", command=lambda: _paste_into_text_widget(entry))
        menu.add_command(label="Выделить всё", command=lambda: _select_all_text_widget(entry))
        menu.add_separator()
        menu.add_command(label="Проверить правописание", command=lambda: self._spellcheck_widget(entry))
        def show_menu(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"
        entry.bind("<Button-3>", show_menu)

    def _enable_live_spellcheck(self, widget):
        """Автоматически подчёркивает возможные орфографические ошибки в tk.Text.

        Проверка запускается с небольшой задержкой после ввода, чтобы не блокировать
        интерфейс на каждом нажатии клавиши. Для ttk.Entry такая посимвольная
        разметка штатно недоступна, поэтому live-подчёркивание применяется только
        к многострочным текстовым полям (сейчас — «Примечание»).
        """
        if not isinstance(widget, tk.Text):
            return
        widget.tag_configure("spell_error", underline=True, foreground=app_theme.RED)
        widget._spell_after_id = None

        def refresh():
            widget._spell_after_id = None
            try:
                if not widget.winfo_exists():
                    return
                widget.tag_remove("spell_error", "1.0", "end")
                if check_text_spelling is None:
                    return
                text = widget.get("1.0", "end-1c")
                if not text.strip():
                    return
                mistakes = check_text_spelling(text)
                wrong = {word.lower() for word, _ in mistakes}
                _log(f"Автопроверка правописания: найдено ошибок: {len(wrong)}")
                if not wrong:
                    return
                # Ищем русские/латинские слова с сохранением точных позиций в Text.
                import re as _re
                for match in _re.finditer(r"[А-Яа-яЁёA-Za-z]{3,}", text):
                    if match.group(0).lower() not in wrong:
                        continue
                    start = f"1.0+{match.start()}c"
                    end = f"1.0+{match.end()}c"
                    widget.tag_add("spell_error", start, end)
            except Exception:
                # Live-проверка никогда не должна мешать вводу или закрытию карточки.
                _log("Ошибка автоматической проверки правописания:\n" + traceback.format_exc())

        def schedule(_event=None):
            try:
                if widget._spell_after_id is not None:
                    widget.after_cancel(widget._spell_after_id)
                widget._spell_after_id = widget.after(650, refresh)
            except tk.TclError:
                pass

        # <<Modified>> надёжнее KeyRelease на Windows: срабатывает и при
        # кириллической раскладке/IME, и при вставке мышью или Ctrl+V.
        # После каждого события флаг modified необходимо сбрасывать вручную.
        try:
            widget.edit_modified(False)
        except tk.TclError:
            pass

        def on_modified(_event=None):
            try:
                if not widget.edit_modified():
                    return
                widget.edit_modified(False)
                schedule()
            except tk.TclError:
                pass

        widget.bind("<<Modified>>", on_modified, add="+")
        widget.bind("<FocusOut>", lambda e: schedule(), add="+")
        # Проверяем и уже загруженный текст существующей карточки.
        widget.after(100, schedule)
        widget.after(100, refresh)

    def _spellcheck_widget(self, widget):
        if check_text_spelling is None:
            messagebox.showinfo(
                "Проверка правописания",
                "Для проверки русского правописания требуется пакет pyspellchecker.\n"
                "Он устанавливается автоматически при сборке из requirements.txt.",
                parent=self,
            )
            return
        try:
            text = widget.get("1.0", "end") if isinstance(widget, tk.Text) else widget.get()
            mistakes = check_text_spelling(text)
            if not mistakes:
                messagebox.showinfo("Проверка правописания", "Ошибок не найдено.", parent=self)
                return
            details = "\n".join(f"• {w} → {', '.join(s)}" for w, s in mistakes[:20])
            if len(mistakes) > 20:
                details += f"\n… и ещё {len(mistakes) - 20}"
            messagebox.showwarning("Возможные ошибки", details, parent=self)
        except Exception:
            _log("Ошибка проверки правописания:\n" + traceback.format_exc())
            messagebox.showerror("Проверка правописания", "Не удалось выполнить проверку. Подробности записаны в app_debug.log.", parent=self)

    def _set_field(self, key, value):
        kind = None
        for k, _, kd, _ in self.HEADER_LABELS:
            if k == key:
                kind = kd
                break
        w = self.widgets[key]
        if kind == "text":
            w.delete("1.0", "end")
            w.insert("1.0", value)
        elif kind == "combo":
            w.set(value)
        else:
            w.delete(0, "end")
            w.insert(0, value)

    def _fill_header_from_existing(self, row):
        """
        Заполняет поля карточки данными сохранённого контракта. Каждое поле обёрнуто
        ОТДЕЛЬНО: если конкретное поле не удаётся заполнить (например, старое значение
        статуса, сохранённое ещё до того как список статусов сузили) — это не должно
        обрывать заполнение ОСТАЛЬНЫХ полей и тем более не должно прерывать построение
        всей карточки (именно это, судя по всему, и происходило раньше).
        """
        for key, _, kind, _ in self.HEADER_LABELS:
            try:
                val = row[key]
                if key in self.DATE_FIELD_KEYS:
                    if key == "created_at" and val:
                        val = str(val).split(" ")[0]
                    val = fmt_date(val)
                self._set_field(key, "" if val is None else str(val))
            except Exception:
                _log(f"_fill_header_from_existing: не удалось заполнить поле '{key}' "
                     f"(значение из базы: {row[key]!r}), пропускаю его:\n" + traceback.format_exc())
        for key in ("resp_purchase_name", "resp_purchase_phone", "resp_purchase_email",
                    "resp_receiving_name", "resp_receiving_phone", "resp_receiving_email"):
            try:
                val = row[key]
                w = self.widgets[key]
                w.delete(0, "end")
                w.insert(0, "" if val is None else str(val))
            except Exception:
                _log(f"_fill_header_from_existing: не удалось заполнить поле '{key}', пропускаю:\n"
                     + traceback.format_exc())

    def _gather_header(self):
        """Возвращает (header, error) — error задан, если формат даты/чисел некорректен."""
        header = {}
        try:
            for key, label, kind, _ in self.HEADER_LABELS:
                w = self.widgets[key]
                raw = w.get("1.0", "end").strip() if kind == "text" else w.get().strip()
                if key in self.DATE_FIELD_KEYS:
                    # Разбираем дату терпимо: если введено не строго ДД.ММ.ГГГГ (например,
                    # 5.9.2026 или 2026-09-05), сначала пытаемся привести к нужному формату.
                    header[key] = parse_date_ru(normalize_date_text(raw)) if raw else None
                elif key in ("contract_sum", "purchase_cost", "logistics",
                             "commission", "other_costs", "guarantee"):
                    header[key] = parse_money(raw) if raw else None
                else:
                    header[key] = raw or None
            for key in ("resp_purchase_name", "resp_purchase_phone", "resp_purchase_email",
                        "resp_receiving_name", "resp_receiving_phone", "resp_receiving_email"):
                header[key] = self.widgets[key].get().strip() or None
        except ValueError as e:
            return None, f"Проверьте формат даты (ДД.ММ.ГГГГ) или чисел.\n{e}"
        return header, None

    def _has_meaningful_content(self, header):
        """True, если пользователь действительно ввёл данные в новую карточку.

        Служебные статусы не считаются содержимым: нажатие «Добавить» и немедленное
        закрытие не должно создавать пустую запись. Документ или товар считаются
        содержимым и сохраняют технический черновик.
        """
        if self.items:
            return True
        meaningful_keys = (
            "platform", "customer", "contract_no", "registry_record", "contract_date", "law",
            "contract_sum", "purchase_cost", "logistics", "commission",
            "other_costs", "guarantee", "sign_deadline", "deadline", "handover_date",
            "payment_deadline", "note", "resp_purchase_name",
            "resp_purchase_phone", "resp_purchase_email", "resp_receiving_name",
            "resp_receiving_phone", "resp_receiving_email",
        )
        if any(header.get(k) not in (None, "") for k in meaningful_keys):
            return True
        if self.existing is not None:
            try:
                if db.fetch_attachments(self.conn, self.existing["id"]):
                    return True
            except Exception:
                _log("_has_meaningful_content: не удалось проверить вложения:\n" + traceback.format_exc())
        return False

    def _save_without_close(self):
        try:
            self._try_close_inner(close_after=False)
        except Exception:
            tb = traceback.format_exc()
            _log(f"_save_without_close: ошибка:\n{tb}")
            messagebox.showerror("Ошибка сохранения", "Не удалось сохранить карточку. Подробности записаны в app_debug.log.", parent=self)

    def _try_close(self):
        """Тонкая обёртка: ловит АБСОЛЮТНО ЛЮБОЕ исключение из _try_close_inner и пишет
        полный traceback в лог. Без этого необработанное исключение просто проглатывалось
        бы Tkinter молча — окно выглядело «не реагирующим», хотя на деле падало на середине."""
        try:
            self._try_close_inner()
        except Exception:
            tb = traceback.format_exc()
            _log(f"_try_close: НЕПОЙМАННОЕ ИСКЛЮЧЕНИЕ (вот из-за чего окно не закрывалось):\n{tb}")
            if messagebox.askyesno(
                "Внутренняя ошибка",
                "При попытке закрыть карточку произошла непредвиденная ошибка.\n"
                "Подробности записаны в файл app_debug.log — пожалуйста, пришлите его "
                "разработчику.\n\nЗакрыть карточку без сохранения изменений?",
                parent=self,
            ):
                _log("_try_close: закрытие без сохранения после непойманного исключения -> self.destroy()")
                try:
                    self.destroy()
                except Exception:
                    _log("_try_close: self.destroy() ТОЖЕ вызвал исключение:\n" + traceback.format_exc())

    def _try_close_inner(self, close_after=True, notify=True):
        """
        Карточка сохраняет изменения при закрытии (кнопка «Закрыть» или системный крестик окна).
        Если что-то не заполнено — спрашиваем, закрыть без сохранения или вернуться и дозаполнить.
        Если при сохранении в базу данных произошла непредвиденная ошибка — показываем её и всё
        равно даём возможность закрыть карточку, чтобы она не «зависала» без объяснения причины.
        """
        _log("_try_close: старт")
        # 1) Если в строке добавления товара остался незафиксированный ввод — сохраняем его
        #    как позицию автоматически, чтобы не терять данные из-за забытой кнопки.
        ok, err = self._auto_commit_pending_item()
        if not ok:
            _log(f"_try_close: незафиксированная позиция товара некорректна -> {err}")
            messagebox.showerror("Ошибка ввода", err, parent=self)
            return

        header, err = self._gather_header()
        if err:
            _log(f"_try_close: ошибка разбора полей -> {err}")
            messagebox.showerror("Ошибка ввода", err, parent=self)
            return  # оставляем карточку открытой, чтобы можно было исправить

        header = self._apply_auto_status_suggestions(header)
        warnings = self._smart_warnings(header)
        if warnings:
            details = "\n• " + "\n• ".join(warnings)
            if not messagebox.askyesno(
                "Проверка карточки",
                "Обнаружены моменты, которые стоит проверить:" + details +
                "\n\nСохранить карточку несмотря на предупреждения?",
                parent=self,
            ):
                return

        # Полностью пустая НОВАЯ карточка не должна оставлять мусорную запись в БД.
        # Если технический черновик уже существует, но в нём нет ни данных, ни вложений,
        # удаляем его окончательно.
        if not self._has_meaningful_content(header):
            if self.existing is None:
                _log("_try_close: новая карточка полностью пустая -> без сохранения")
                if close_after:
                    self.destroy()
                else:
                    messagebox.showinfo("Сохранение", "В карточке пока нет данных для сохранения.", parent=self)
                return
            if self._draft_created:
                draft_id = self.existing["id"]
                _log(f"_try_close: технический черновик id={draft_id} пустой -> удаляю без сохранения")
                try:
                    db.purge_purchase(self.conn, draft_id)
                except Exception:
                    _log("_try_close: не удалось удалить пустой технический черновик:\n" + traceback.format_exc())
                if close_after:
                    self.destroy()
                return

        # Неполную карточку не отбрасываем: пользователь может заполнять контракт
        # постепенно. Если формат введённых значений корректен, сохраняем текущие
        # данные как черновик даже без суммы и/или товарных позиций.
        missing = []
        if not self.items:
            missing.append("товар")
        if header.get("contract_sum") is None:
            missing.append("сумма контракта")
        if missing:
            _log(f"_try_close: карточка неполная ({', '.join(missing)}), сохраняю как черновик")

        try:
            saved_id = None
            if self._draft_created and self.existing is not None:
                # Документ в новой карточке уже создал запись в purchases. Поэтому
                # повторный INSERT создал бы дубль: обновляем тот же id.
                draft_id = self.existing["id"]
                _log(f"_try_close: карточка уже имеет черновик id={draft_id}; выполняю UPDATE вместо второго INSERT")
                db.update_purchase(self.conn, draft_id, header, list(self.items))
                saved_id = draft_id
                self.existing = db.fetch_by_id(self.conn, draft_id)
                if self.existing is None:
                    raise RuntimeError(f"после UPDATE запись id={draft_id} не найдена в базе")
                self._draft_created = False
                if missing:
                    _log(f"_try_close: черновик id={draft_id} сохранён; карточка остаётся неполной ({', '.join(missing)})")
                else:
                    _log(f"_try_close: черновик id={draft_id} преобразован в заполненный контракт")
            else:
                _log("_try_close: вызываю штатный self.on_save(...)")
                result = self.on_save(header, list(self.items))
                saved_id = result if isinstance(result, int) else (self.existing["id"] if self.existing is not None else None)
                _log("_try_close: on_save выполнен успешно")

            # Контрольное чтение после сохранения. Для новых карточек callback теперь
            # возвращает id; для существующих id уже известен.
            if saved_id is not None:
                check = db.fetch_by_id(self.conn, saved_id)
                if check is None:
                    raise RuntimeError(f"контроль сохранения не пройден: запись id={saved_id} отсутствует в базе")
                _log(f"_try_close: сохранение подтверждено чтением из БД, id={saved_id}")
        except Exception as e:
            _log(f"_try_close: ИСКЛЮЧЕНИЕ при сохранении -> {e!r}")
            if messagebox.askyesno(
                "Ошибка сохранения",
                f"Не удалось сохранить контракт:\n{e}\n\nЗакрыть карточку без сохранения изменений?",
                parent=self,
            ):
                _log("_try_close: закрытие без сохранения после ошибки -> self.destroy()")
                self.destroy()
            return

        owner = self.master
        if saved_id is not None:
            first_persist = self.existing is None
            self.existing = db.fetch_by_id(self.conn, saved_id)
            self._draft_created = False
            self.title(f"Контракт #{saved_id}")
            if first_persist:
                # После первого Ctrl+S новая карточка уже стала существующей: последующие Ctrl+S должны UPDATE, а не INSERT.
                def _update_saved_card(h, it, sid=saved_id):
                    db.update_purchase(self.conn, sid, h, it)
                    return sid
                self.on_save = _update_saved_card
        if owner is not None and hasattr(owner, "refresh_all"):
            try:
                owner.after_idle(owner.refresh_all)
            except Exception:
                _log("_try_close: не удалось запланировать refresh_all:\n" + traceback.format_exc())
        if close_after:
            _log("_try_close: вызываю self.destroy()")
            self.destroy()
            _log("_try_close: self.destroy() выполнен")
        else:
            self._refresh_next_action()
            self._refresh_items_tree()
            if notify:
                messagebox.showinfo("Сохранение", "Контракт сохранён.", parent=self)


# ============================================================ Диалог прихода
class ReceiptDialog(tk.Toplevel):
    FIELDS_UI = [
        ("product", "Наименование товара", "combo"),
        ("qty", "Количество, шт.", "entry"),
        ("unit_cost", "Цена за единицу, руб.", "entry"),
        ("receipt_date", "Дата поступления (ДД.ММ.ГГГГ)", "entry"),
        ("supplier", "Поставщик", "entry"),
        ("note", "Примечание", "entry"),
    ]

    def __init__(self, parent, on_save, product_options, existing=None):
        super().__init__(parent)
        self.title("Приход товара" if existing is None else f"Приход #{existing['id']}")
        self.on_save = on_save
        self.existing = existing
        self.resizable(False, False)

        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)

        self.widgets = {}
        for row, (key, label, kind) in enumerate(self.FIELDS_UI):
            ttk.Label(form, text=label + ":").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            if kind == "combo":
                w = ttk.Combobox(form, values=product_options, width=40)
            elif key == "receipt_date":
                date_var = tk.StringVar()
                w = ttk.Entry(form, width=42, textvariable=date_var)
                bind_date_autodots(w, date_var)
            else:
                w = ttk.Entry(form, width=42)
            w.grid(row=row, column=1, sticky="w", pady=4)
            self.widgets[key] = w

        if existing is not None:
            for key, _, _ in self.FIELDS_UI:
                val = existing[key]
                if key == "receipt_date":
                    val = fmt_date(val)
                self.widgets[key].insert(0, "" if val is None else str(val))

        btn_frame = ttk.Frame(form)
        btn_frame.grid(row=len(self.FIELDS_UI), column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn_frame, text="Сохранить", command=self._save).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Отмена", command=self.destroy).pack(side="left", padx=4)

        # Модальность/фокус — в конце, когда окно уже построено (см. пояснение в PurchaseDialog)
        self.transient(parent)
        self.update_idletasks()
        self.lift()
        self.focus_force()
        self.grab_set()

    def _save(self):
        data = {}
        try:
            for key, _, _ in self.FIELDS_UI:
                raw = self.widgets[key].get().strip()
                if key == "receipt_date":
                    data[key] = parse_date_ru(raw)
                elif key in ("qty", "unit_cost"):
                    data[key] = parse_money(raw) if raw else None
                else:
                    data[key] = raw or None
        except ValueError as e:
            messagebox.showerror("Ошибка ввода", f"Проверьте формат даты или чисел.\n{e}", parent=self)
            return

        if not data.get("product"):
            messagebox.showerror("Ошибка ввода", "Укажите наименование товара.", parent=self)
            return
        if not data.get("qty"):
            messagebox.showerror("Ошибка ввода", "Укажите количество.", parent=self)
            return

        self.on_save(data, self.existing["id"] if self.existing is not None else None)
        self.destroy()


# ============================================================ Диалог ручного резерва склада
class ReservationDialog(tk.Toplevel):
    """
    Резерв товара под конкретную организацию — без привязки к контракту. Нужен для
    ситуации «выставили счёт потенциальному клиенту, оплаты пока нет, но товар на
    всякий случай отложен» — то есть до момента, когда (и если) появится контракт.
    """
    FIELDS_UI = [
        ("product", "Наименование товара", "combo"),
        ("qty", "Количество, шт.", "entry"),
        ("organization", "Организация (на кого резерв)", "entry"),
        ("reserved_date", "Дата резерва (ДД.ММ.ГГГГ)", "entry"),
        ("note", "Примечание", "entry"),
    ]

    def __init__(self, parent, on_save, product_options, existing=None):
        super().__init__(parent)
        self.title("Резерв товара" if existing is None else f"Резерв #{existing['id']}")
        self.on_save = on_save
        self.existing = existing
        self.resizable(False, False)

        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)

        self.widgets = {}
        for row, (key, label, kind) in enumerate(self.FIELDS_UI):
            ttk.Label(form, text=label + ":").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            if kind == "combo":
                w = ttk.Combobox(form, values=product_options, width=40)
            elif key == "reserved_date":
                date_var = tk.StringVar()
                w = ttk.Entry(form, width=42, textvariable=date_var)
                bind_date_autodots(w, date_var)
            else:
                w = ttk.Entry(form, width=42)
            w.grid(row=row, column=1, sticky="w", pady=4)
            self.widgets[key] = w

        if existing is not None:
            for key, _, _ in self.FIELDS_UI:
                val = existing[key]
                if key == "reserved_date":
                    val = fmt_date(val)
                self.widgets[key].insert(0, "" if val is None else str(val))

        btn_frame = ttk.Frame(form)
        btn_frame.grid(row=len(self.FIELDS_UI), column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn_frame, text="Сохранить", command=self._save).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Отмена", command=self.destroy).pack(side="left", padx=4)

        self.transient(parent)
        self.update_idletasks()
        self.lift()
        self.focus_force()
        self.grab_set()

    def _save(self):
        data = {}
        try:
            for key, _, _ in self.FIELDS_UI:
                raw = self.widgets[key].get().strip()
                if key == "reserved_date":
                    data[key] = parse_date_ru(raw)
                elif key == "qty":
                    data[key] = parse_money(raw) if raw else None
                else:
                    data[key] = raw or None
        except ValueError as e:
            messagebox.showerror("Ошибка ввода", f"Проверьте формат даты или чисел.\n{e}", parent=self)
            return

        if not data.get("product"):
            messagebox.showerror("Ошибка ввода", "Укажите наименование товара.", parent=self)
            return
        if not data.get("qty"):
            messagebox.showerror("Ошибка ввода", "Укажите количество.", parent=self)
            return
        if not data.get("organization"):
            messagebox.showerror("Ошибка ввода", "Укажите организацию, на кого резервируется товар.",
                                  parent=self)
            return

        self.on_save(data, self.existing["id"] if self.existing is not None else None)
        self.destroy()


# ============================================================ Диалог записи конкурента
class CompetitorDialog(tk.Toplevel):
    FIELDS_UI = [
        ("competitor", "Конкурент", "entry"),
        ("competitor_inn", "ИНН", "entry"),
        ("product", "Товар", "entry"),
        ("trade_type", "Вид торгов", "entry"),
        ("qty", "Количество", "entry"),
        ("unit_price", "Цена за единицу, руб.", "entry"),
        ("purchase_date", "Дата закупки (ДД.ММ.ГГГГ)", "entry"),
    ]

    def __init__(self, parent, on_save, existing=None):
        super().__init__(parent)
        self.title("Запись о конкуренте" if existing is None else f"Запись #{existing['id']}")
        self.on_save = on_save
        self.existing = existing
        self.resizable(False, False)

        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)

        self.widgets = {}
        for row, (key, label, kind) in enumerate(self.FIELDS_UI):
            ttk.Label(form, text=label + ":").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            if key == "purchase_date":
                date_var = tk.StringVar()
                w = ttk.Entry(form, width=36, textvariable=date_var)
                bind_date_autodots(w, date_var)
            else:
                w = ttk.Entry(form, width=36)
            w.grid(row=row, column=1, sticky="w", pady=4)
            self.widgets[key] = w

        if existing is not None:
            for key, _, _ in self.FIELDS_UI:
                val = existing[key]
                if key == "purchase_date":
                    val = fmt_date(val)
                self.widgets[key].insert(0, "" if val is None else str(val))

        btn_frame = ttk.Frame(form)
        btn_frame.grid(row=len(self.FIELDS_UI), column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn_frame, text="Сохранить", command=self._save).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Отмена", command=self.destroy).pack(side="left", padx=4)

        # Модальность/фокус — в конце, когда окно уже построено (см. пояснение в PurchaseDialog)
        self.transient(parent)
        self.update_idletasks()
        self.lift()
        self.focus_force()
        self.grab_set()

    def _save(self):
        data = {}
        try:
            for key, _, _ in self.FIELDS_UI:
                raw = self.widgets[key].get().strip()
                if key == "purchase_date":
                    data[key] = parse_date_ru(raw)
                elif key in ("qty", "unit_price"):
                    data[key] = parse_money(raw) if raw else None
                else:
                    data[key] = raw or None
        except ValueError as e:
            messagebox.showerror("Ошибка ввода", f"Проверьте формат даты или чисел.\n{e}", parent=self)
            return

        if not data.get("competitor") or not data.get("product"):
            messagebox.showerror("Ошибка ввода", "Укажите конкурента и товар.", parent=self)
            return
        if data.get("competitor_inn"):
            inn = "".join(ch for ch in str(data["competitor_inn"]) if ch.isdigit())
            if len(inn) not in (10, 12):
                messagebox.showerror("Ошибка ввода", "ИНН должен содержать 10 или 12 цифр.", parent=self)
                return
            data["competitor_inn"] = inn

        self.on_save(data, self.existing["id"] if self.existing is not None else None)
        self.destroy()


# ============================================================ Диалог позиции калькулятора цены
class CalculatorRowDialog(tk.Toplevel):
    """
    Позиция калькулятора цены для контракта: по себестоимости, расходам на партию
    (логистика, доп. расходы, комиссия площадки), наценке и налогу считает, по какой
    цене продавать. Не сохраняется в базу — рабочий инструмент для прикидки цены
    ДО заключения контракта (как и исходный HTML-калькулятор).
    """
    FIELDS_UI = [
        ("name", "Наименование / артикул", "entry", ""),
        ("qty", "Кол-во, шт.", "entry", ""),
        ("cost", "Себестоимость за шт., руб.", "entry", ""),
        ("logistics", "Логистика на партию, руб.", "entry", ""),
        ("extra", "Доп. расходы на партию, руб.", "entry", ""),
        ("commission", "Комиссия площадки, руб.", "entry", ""),
        ("markup", "Наценка, %", "entry", "20"),
        ("tax", "Налог, %", "entry", "7"),
    ]

    def __init__(self, parent, on_save, existing=None):
        super().__init__(parent)
        self.title("Позиция расчёта" if existing is None else f"Позиция расчёта #{existing.get('id')}")
        self.on_save = on_save
        self.existing = existing
        self.resizable(False, False)

        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)

        self.widgets = {}
        for row, (key, label, kind, default) in enumerate(self.FIELDS_UI):
            ttk.Label(form, text=label + ":").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            w = ttk.Entry(form, width=32)
            w.grid(row=row, column=1, sticky="w", pady=4)
            value = str(existing[key]) if existing is not None and existing.get(key) not in (None, "") else default
            w.insert(0, value)
            self.widgets[key] = w

        btn_frame = ttk.Frame(form)
        btn_frame.grid(row=len(self.FIELDS_UI), column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn_frame, text="Сохранить", command=self._save).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Отмена", command=self.destroy).pack(side="left", padx=4)

        self.transient(parent)
        self.update_idletasks()
        self.lift()
        self.focus_force()
        self.grab_set()

    def _save(self):
        data = {}
        try:
            data["name"] = self.widgets["name"].get().strip()
            for key in ("qty", "cost", "logistics", "extra", "commission", "markup", "tax"):
                raw = self.widgets[key].get().strip()
                data[key] = parse_money(raw) if raw else 0.0
        except ValueError as e:
            messagebox.showerror("Ошибка ввода", f"Проверьте, что все числовые поля — числа.\n{e}", parent=self)
            return

        if not data["name"]:
            messagebox.showerror("Ошибка ввода", "Укажите наименование / артикул.", parent=self)
            return
        if not data["qty"] or not data["cost"]:
            messagebox.showerror("Ошибка ввода", "Укажите количество и себестоимость за штуку — "
                                                   "без них цену не посчитать.", parent=self)
            return

        self.on_save(data, self.existing["id"] if self.existing is not None else None)
        self.destroy()



# ============================================================ Диалог «Корзина»
class TrashDialog(tk.Toplevel):
    """Список удалённых (но ещё не стёртых окончательно) контрактов: восстановить
    или удалить навсегда прямо сейчас, не дожидаясь автоматической очистки."""

    def __init__(self, parent, conn, on_change=None):
        super().__init__(parent)
        self.title("Корзина")
        self.conn = conn
        self.on_change = on_change
        self.resizable(True, True)

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, wraplength=650, justify="left",
                  text=f"Удалённые контракты хранятся здесь {db.TRASH_KEEP_DAYS} дней, затем "
                       f"удаляются автоматически и окончательно (вместе с документами). "
                       f"Можно восстановить контракт или удалить его навсегда прямо сейчас."
                  ).pack(anchor="w", pady=(0, 10))

        top = ttk.Frame(outer)
        top.pack(fill="x", pady=(0, 8))
        ttk.Button(top, text="Восстановить", command=self._restore_selected).pack(side="left", padx=4)
        ttk.Button(top, text="Удалить навсегда", command=self._purge_selected).pack(side="left", padx=4)

        cols = ["customer", "contract_no", "product", "deleted_at"]
        labels = ["Заказчик", "№ контракта", "Товары", "Удалён"]
        self.tree = ttk.Treeview(outer, columns=cols, show="headings", height=12)
        for key, label in zip(cols, labels):
            self.tree.heading(key, text=label, anchor="center")
            self.tree.column(key, width=170 if key != "product" else 220, anchor="center")
        self.tree.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(outer)
        btn_frame.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_frame, text="Закрыть", command=self.destroy).pack(side="left")

        self._refresh()

        self.transient(parent)
        self.update_idletasks()
        self.lift()
        self.focus_force()
        self.grab_set()

    def _refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for r in db.fetch_deleted_purchases(self.conn):
            deleted_display = fmt_date(r["deleted_at"][:10]) if r["deleted_at"] else ""
            values = [r["customer"] or "", r["contract_no"] or "", r["product"] or "",
                      deleted_display]
            tree_insert_wrapped(self.tree, "", "end", iid=str(r["id"]), values=values)

    def _selected_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _restore_selected(self):
        rid = self._selected_id()
        if rid is None:
            messagebox.showinfo("Выбор контракта", "Сначала выберите строку в списке.", parent=self)
            return
        db.restore_purchase(self.conn, rid)
        self._refresh()
        if self.on_change:
            self.on_change()

    def _purge_selected(self):
        rid = self._selected_id()
        if rid is None:
            messagebox.showinfo("Выбор контракта", "Сначала выберите строку в списке.", parent=self)
            return
        if messagebox.askyesno("Удаление навсегда",
                                "Удалить контракт и все его документы БЕЗВОЗВРАТНО? "
                                "Отменить это действие будет нельзя.", parent=self):
            db.purge_purchase(self.conn, rid)
            self._refresh()
            if self.on_change:
                self.on_change()


class MailSettingsDialog(tk.Toplevel):
    """Настройки Gmail и фонового Планировщика Windows."""
    def __init__(self, parent, conn, on_saved=None):
        super().__init__(parent)
        self.title("Настройки почты")
        self.conn = conn
        self.on_saved = on_saved
        self.resizable(False, False)
        self.transient(parent)

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)

        self.enabled_var = tk.BooleanVar(value=db.get_setting(conn, "email_enabled", "0") == "1")
        self.sender_var = tk.StringVar(value=db.get_setting(conn, "email_sender", "") or "")
        self.recipients_var = tk.StringVar(value=db.get_setting(conn, "email_recipients", "") or "")
        self.schedule_time_var = tk.StringVar(value=db.get_setting(conn, "email_schedule_time", "09:00") or "09:00")
        self.password_var = tk.StringVar()
        self.scheduler_status_var = tk.StringVar(value="Планировщик: проверка...")

        ttk.Checkbutton(frame, text="Включить ежедневную e-mail сводку «Требует внимания»",
                        variable=self.enabled_var).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(frame, text="Gmail отправителя:").grid(row=1, column=0, sticky="e", padx=(0, 10), pady=5)
        ttk.Entry(frame, textvariable=self.sender_var, width=42).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="Получатель:").grid(row=2, column=0, sticky="e", padx=(0, 10), pady=5)
        ttk.Entry(frame, textvariable=self.recipients_var, width=42).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="Время ежедневной проверки:").grid(row=3, column=0, sticky="e", padx=(0, 10), pady=5)
        ttk.Entry(frame, textvariable=self.schedule_time_var, width=12).grid(row=3, column=1, sticky="w", pady=5)
        ttk.Label(frame, text="Gmail App Password:").grid(row=4, column=0, sticky="e", padx=(0, 10), pady=5)
        ttk.Entry(frame, textvariable=self.password_var, show="•", width=42).grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Label(
            frame,
            text=("App Password хранится в облачной базе data/zakupki.db и синхронизируется между компьютерами. "
                  "Если пароль уже сохранён, поле можно оставить пустым. Доступ к облачной папке необходимо защищать."),
            wraplength=590, justify="left", foreground=app_theme.MUTED
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(5, 8))
        ttk.Label(
            frame,
            text=("Планировщик запускает только фоновую проверку — основное окно программы не открывается. "
                  "Если компьютер был выключен/спал, Windows запустит пропущенную проверку при первой возможности."),
            wraplength=590, justify="left", foreground=app_theme.MUTED
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(frame, textvariable=self.scheduler_status_var, foreground="#2f5f75").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(0, 10))

        scheduler_buttons = ttk.Frame(frame)
        scheduler_buttons.grid(row=8, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Button(scheduler_buttons, text="Установить/обновить задачу", command=self._install_scheduler).pack(side="left", padx=(0, 6))
        ttk.Button(scheduler_buttons, text="Запустить проверку сейчас", command=self._run_scheduler_now).pack(side="left", padx=6)
        ttk.Button(scheduler_buttons, text="Удалить задачу", command=self._remove_scheduler).pack(side="left", padx=6)

        buttons = ttk.Frame(frame)
        buttons.grid(row=9, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Отправить тест", command=self._test).pack(side="left", padx=4)
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="left", padx=4)

        self._refresh_scheduler_status()
        self.update_idletasks()
        self.lift()
        self.focus_force()
        self.grab_set()

    def _values(self):
        sender = email_notify.normalize_email(self.sender_var.get())
        recipients = email_notify.parse_recipients(self.recipients_var.get())
        schedule_time = scheduler_win.normalize_time(self.schedule_time_var.get())
        if sender and not email_notify.validate_email(sender):
            raise ValueError("Некорректный адрес Gmail отправителя.")
        if recipients and any(not email_notify.validate_email(x) for x in recipients):
            raise ValueError("Некорректный адрес получателя.")
        if self.enabled_var.get() and (not sender or not recipients):
            raise ValueError("Для включения уведомлений укажите отправителя и получателя.")
        return sender, recipients, schedule_time

    def _save_values(self, require_password=False):
        sender, recipients, schedule_time = self._values()
        password = self.password_var.get().strip().replace(" ", "")
        stored_password = (db.get_setting(self.conn, "email_app_password", "") or "").strip()
        effective_password = password or stored_password
        if require_password and sender and not effective_password:
            raise ValueError("Укажите Gmail App Password.")
        db.set_setting(self.conn, "email_enabled", "1" if self.enabled_var.get() else "0")
        db.set_setting(self.conn, "email_sender", sender)
        db.set_setting(self.conn, "email_recipients", ", ".join(recipients))
        db.set_setting(self.conn, "email_schedule_time", schedule_time)
        if password:
            db.set_setting(self.conn, "email_app_password", password)
        if self.on_saved:
            self.on_saved()
        return sender, recipients, schedule_time, effective_password or None

    def _sync_settings_now(self):
        try:
            db.sync_working_to_cloud(self.conn)
        except Exception as exc:
            _log(f"Почта: настройки сохранены локально, но не удалось сразу синхронизировать: {exc}")

    def _refresh_scheduler_status(self):
        if not scheduler_win.is_windows():
            self.scheduler_status_var.set("Планировщик: доступен только в Windows")
            return
        info = scheduler_win.task_info()
        if not info.get("installed"):
            if info.get("error"):
                self.scheduler_status_var.set(f"Планировщик: ошибка проверки — {info['error']}")
            else:
                self.scheduler_status_var.set("Планировщик: задача не установлена")
            return
        next_run = info.get("next_run") or "—"
        last_result = info.get("last_result")
        self.scheduler_status_var.set(
            f"Планировщик: установлен | следующий запуск: {next_run} | код последнего запуска: {last_result}"
        )

    def _install_scheduler(self, show_message=True):
        try:
            _sender, _recipients, schedule_time, _password = self._save_values(require_password=True)
            if not self.enabled_var.get():
                raise ValueError("Сначала включите ежедневную e-mail сводку «Требует внимания».")
            scheduler_win.install_daily_task(schedule_time)
            db.set_setting(self.conn, "email_scheduler_host", socket.gethostname())
            db.set_setting(self.conn, "email_scheduler_enabled", "1")
            self._sync_settings_now()
            self._refresh_scheduler_status()
            if show_message:
                messagebox.showinfo(
                    "Планировщик Windows",
                    f"Ежедневная сводка «Требует внимания» установлена на каждый день в {schedule_time}.\n\n"
                    "Основное окно программы для отправки письма открывать не нужно.",
                    parent=self,
                )
            return True
        except Exception as exc:
            if show_message:
                messagebox.showerror("Планировщик Windows", f"Не удалось установить задачу:\n{exc}", parent=self)
            return False

    def _remove_scheduler(self):
        try:
            scheduler_win.remove_task()
            db.set_setting(self.conn, "email_scheduler_enabled", "0")
            host = (db.get_setting(self.conn, "email_scheduler_host", "") or "").strip()
            if not host or host.lower() == socket.gethostname().lower():
                db.set_setting(self.conn, "email_scheduler_host", "")
            self._sync_settings_now()
            self._refresh_scheduler_status()
            messagebox.showinfo("Планировщик Windows", "Фоновая задача удалена.", parent=self)
        except Exception as exc:
            messagebox.showerror("Планировщик Windows", f"Не удалось удалить задачу:\n{exc}", parent=self)

    def _run_scheduler_now(self):
        """Немедленная проверка с понятным результатом.

        Это явное действие пользователя, поэтому ограничение "активный компьютер"
        Планировщика здесь не применяется. Защита от повторной отправки за день
        сохраняется общей для GUI и фоновой задачи.
        """
        try:
            self._save_values(require_password=True)
            self._sync_settings_now()
            if not self.enabled_var.get():
                raise ValueError("Ежедневная e-mail сводка выключена. Включите галочку сверху и сохраните настройки.")

            items = reminder_worker.attention_items(self.conn)
            result = reminder_worker.run_attention_digest(
                self.conn,
                respect_scheduler_host=False,
            )
            status = result.get("status")
            if status == "sent":
                text = f"Сводка отправлена. Задач в письме: {result.get('count', 0)}."
            elif status == "already_sent":
                text = "Сегодня сводка уже отправлялась. Повторное письмо не отправлено."
            elif status == "nothing_due":
                text = ("Сегодня нет задач, требующих внимания по настроенным правилам.\n\n"
                        f"Задач сейчас: {len(items)}.")
            elif status == "disabled":
                text = "Ежедневная e-mail сводка выключена."
            elif status == "not_configured":
                text = "Почта настроена не полностью: проверьте Gmail, получателя и App Password."
            elif status == "busy":
                text = "Проверка уже выполняется другим процессом. Повторите через несколько секунд."
            elif status == "error":
                raise RuntimeError(result.get("error") or "неизвестная ошибка отправки")
            else:
                text = f"Проверка выполнена. Результат: {status or 'неизвестно'}."
            messagebox.showinfo("Проверка сводки", text, parent=self)
            self._refresh_scheduler_status()
        except Exception as exc:
            messagebox.showerror("Проверка сводки", f"Не удалось выполнить проверку:\n{exc}", parent=self)

    def _save(self):
        try:
            self._save_values(require_password=self.enabled_var.get())
            self._sync_settings_now()
            scheduler_note = ""
            if self.enabled_var.get() and scheduler_win.is_windows():
                if self._install_scheduler(show_message=False):
                    scheduler_note = "\nФоновая задача Windows установлена/обновлена."
                else:
                    scheduler_note = "\nНастройки сохранены, но фоновую задачу установить не удалось. Используйте кнопку «Установить/обновить задачу»."
        except Exception as exc:
            messagebox.showerror("Настройки почты", str(exc), parent=self)
            return
        messagebox.showinfo("Настройки почты", "Настройки сохранены." + scheduler_note, parent=self)
        self.destroy()

    def _test(self):
        try:
            sender, recipients, schedule_time = self._values()
            if not sender or not recipients:
                raise ValueError("Укажите отправителя и получателя.")
            password = self.password_var.get().strip().replace(" ", "") or (db.get_setting(self.conn, "email_app_password", "") or "").strip()
            if not password:
                raise ValueError("Укажите Gmail App Password.")
            email_notify.send_gmail(
                sender, recipients,
                "Тест уведомлений — Учет заключенных контрактов",
                "Это тестовое письмо. Интеграция Gmail в приложении работает.",
                app_password=password,
            )
            # Успешный тест всегда сохраняет введённые реквизиты, чтобы не было
            # ситуации "тест пришёл, а после перезапуска настройки пустые".
            db.set_setting(self.conn, "email_sender", sender)
            db.set_setting(self.conn, "email_recipients", ", ".join(recipients))
            db.set_setting(self.conn, "email_schedule_time", schedule_time)
            db.set_setting(self.conn, "email_app_password", password)
            self._sync_settings_now()
            if self.enabled_var.get():
                msg = "Тестовое письмо отправлено. Ежедневная сводка включена."
            else:
                msg = ("Тестовое письмо отправлено, но ежедневная сводка сейчас ВЫКЛЮЧЕНА.\n\n"
                       "Чтобы получать ежедневную сводку, включите галочку «Включить ежедневную e-mail сводку „Требует внимания“» и нажмите «Сохранить».")
            messagebox.showinfo("Почта", msg, parent=self)
        except Exception as exc:
            messagebox.showerror("Почта", f"Не удалось отправить тестовое письмо:\n{exc}", parent=self)


class ProductCatalogDialog(tk.Toplevel):
    def __init__(self, parent, conn, on_change=None):
        super().__init__(parent); self.conn=conn; self.on_change=on_change
        self.title("Справочник товаров"); self.geometry("720x480"); self.transient(parent)
        frame=ttk.Frame(self,padding=10); frame.pack(fill="both",expand=True)
        ttk.Label(frame,text="Закреплённые товары: «Рутокен Lite 1010» и «Рутокен ЭЦП 3.0 3120». Они всегда остаются в складе. Другие товары можно добавлять и переименовывать.",
                  wraplength=680,justify="left").pack(fill="x",pady=(0,8))
        self.tree=ttk.Treeview(frame,columns=("name",),show="headings",selectmode="browse")
        self.tree.heading("name",text="Наименование товара",anchor="center"); self.tree.column("name",width=650,anchor="center")
        self.tree.pack(fill="both",expand=True)
        btn=ttk.Frame(frame); btn.pack(fill="x",pady=(8,0))
        ttk.Button(btn,text="Добавить",command=self._add).pack(side="left")
        ttk.Button(btn,text="Переименовать",command=self._rename).pack(side="left",padx=6)
        ttk.Button(btn,text="Закрыть",command=self.destroy).pack(side="right")
        self._refresh()
    def _refresh(self):
        for x in self.tree.get_children(): self.tree.delete(x)
        for i,name in enumerate(db.catalog_products(self.conn)):
            tree_insert_wrapped(self.tree,"","end",iid=str(i),values=(name,))
    def _add(self):
        name=simpledialog.askstring("Новый товар","Наименование товара:",parent=self)
        if name:
            db.ensure_product(self.conn,name); self._refresh();
            if self.on_change: self.on_change()
    def _rename(self):
        sel=self.tree.selection()
        if not sel: return
        old=self.tree.set(sel[0],"name")
        if db.is_fixed_product(old):
            messagebox.showinfo("Справочник товаров", "Этот товар закреплён и не может быть переименован.", parent=self)
            return
        new=simpledialog.askstring("Переименовать товар","Новое наименование:",initialvalue=old,parent=self)
        if new and new.strip()!=old:
            try:
                db.rename_product(self.conn,old,new); self._refresh()
                if self.on_change: self.on_change()
            except Exception as exc:
                messagebox.showerror("Справочник товаров",str(exc),parent=self)

class App(tk.Tk):
    AUTOSYNC_MS = 2 * 60 * 1000
    DAILY_SIGNING_CHECK_MS = 60 * 60 * 1000

    def __init__(self):
        super().__init__()
        self.withdraw()
        self.title(f"Учет заключенных контрактов — v{__version__}")
        self.geometry("1450x780")
        self._closing = False
        self._lock_token = None
        self.conn = None
        self._sync_after_id = None
        self._startup_sync_error = None
        self._health_scheduler_checked_at = 0.0
        self._health_scheduler_installed = None
        self._update_check_running = False
        self._update_download_running = False
        self.storage_status_var = tk.StringVar(value="Хранилище: подготовка...")
        self.health_status_var = tk.StringVar(value="Система: проверка...")

        self._initialize_storage()
        self.conn = db.get_connection()
        self._configure_connection_autosync()
        try:
            migrated = db.migrate_attachment_paths(self.conn)
            if migrated:
                _log(f"Хранилище: преобразовано путей вложений в переносимый формат: {migrated}")
                db.sync_working_to_cloud(self.conn)
        except Exception as e:
            _log(f"Хранилище: не удалось мигрировать/синхронизировать пути вложений: {e}")
            self._startup_sync_error = str(e)

        # Если предыдущий сеанс завершился аварийно и локальная БД новее облачной,
        # сразу выгружаем сохранённые локальные изменения в облачную мастер-копию.
        try:
            if getattr(self, "_storage_action", "") in ("local_unsynced", "local_only", "new"):
                result = db.sync_working_to_cloud(self.conn)
                _log(f"Хранилище: первичная синхронизация выполнена, changed={result.get('changed')}")
        except Exception as e:
            _log(f"Хранилище: ошибка первичной синхронизации: {e}")
            self._startup_sync_error = str(e)

        try:
            db.auto_backup_if_needed(db.cloud_db_path(), min_interval_hours=24)
        except Exception as e:
            _log(f"Автобэкап при старте не выполнен: {e}")

        try:
            db.purge_old_trash(self.conn)
        except Exception:
            pass

        self._apply_fonts()
        install_global_clipboard_bindings(self)
        self._build_menu()
        self._build_brand_header()
        self._build_storage_status()
        self._build_tabs()
        self._bind_app_hotkeys()
        self.refresh_all()
        if self._startup_sync_error:
            self._update_storage_status("НЕ синхронизировано", self._startup_sync_error)
        else:
            self._update_storage_status("синхронизировано")

        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self.deiconify()
        self.after(800, self._send_attention_email_if_due)
        # v2.17.2: обновления устанавливаются только вручную.\n        # Автоматическая проверка и замена EXE отключены.\n        self.after(self.DAILY_SIGNING_CHECK_MS, self._daily_signing_tick)
        self.after(self.AUTOSYNC_MS, self._autosync_tick)

    def _bind_app_hotkeys(self):
        # Горячие клавиши главной таблицы не должны перехватывать редактирование
        # Entry/Text/Combobox/Spinbox. Delete/F2/Ctrl+D разрешаем только когда
        # фокус находится в таблице контрактов или на самом главном окне.
        text_classes = {"Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox"}

        def focus_is_text(event):
            try:
                w = self.focus_get() or event.widget
                return w is not None and w.winfo_class() in text_classes
            except Exception:
                return False

        def table_action(event, action):
            if focus_is_text(event):
                return None
            try:
                w = self.focus_get() or event.widget
                if w not in (self, self.tree) and getattr(w, "winfo_toplevel", lambda: None)() is not self:
                    return None
            except Exception:
                pass
            action()
            return "break"

        # Ctrl+N допускаем из любого нетекстового места главного окна.
        self.bind("<Control-n>", lambda e: None if focus_is_text(e) else (self._add_purchase(), "break")[1], add="+")
        self.bind("<Control-N>", lambda e: None if focus_is_text(e) else (self._add_purchase(), "break")[1], add="+")
        self.bind("<F2>", lambda e: table_action(e, self._edit_purchase), add="+")
        self.bind("<Delete>", lambda e: table_action(e, self._delete_purchase), add="+")
        self.bind("<Control-d>", lambda e: table_action(e, self._duplicate_purchase), add="+")
        self.bind("<Control-D>", lambda e: table_action(e, self._duplicate_purchase), add="+")

    def _initialize_storage(self):
        # 1) Блокировка от одновременного запуска на двух компьютерах.
        try:
            self._lock_token = db.acquire_app_lock()
        except db.CloudLockError as e:
            details = db.describe_lock(e.info)
            force = messagebox.askyesno(
                "Программа уже открыта",
                "В облачной папке найден признак другого активного запуска.\n\n"
                f"{details}\n\n"
                "Если программа действительно открыта на другом компьютере — нажмите «Нет».\n"
                "Если предыдущий запуск уже закрыт или завершился аварийно — можно открыть принудительно.",
                parent=self,
            )
            if not force:
                self.destroy()
                raise SystemExit
            try:
                self._lock_token = db.acquire_app_lock(force=True)
            except Exception as ex:
                messagebox.showerror("Хранилище", f"Не удалось создать файл блокировки в папке программы:\n{ex}", parent=self)
                self.destroy()
                raise SystemExit
            _log(f"Хранилище: lock принудительно заменён; прежний: {details}")
        except Exception as ex:
            messagebox.showerror(
                "Хранилище недоступно",
                "Не удалось получить доступ на запись к папке data рядом с программой.\n\n"
                f"Ошибка: {ex}",
                parent=self,
            )
            self.destroy()
            raise SystemExit

        # 2) Выбор актуальной копии БД и защита от незавершённой облачной синхронизации.
        try:
            status = db.prepare_working_storage()
        except Exception as ex:
            messagebox.showerror(
                "Ошибка хранилища",
                f"Не удалось подготовить локальную/облачную базу:\n{ex}",
                parent=self,
            )
            db.release_app_lock(self._lock_token)
            self.destroy()
            raise SystemExit
        if status.get("status") == "cloud_sync_incomplete":
            messagebox.showerror(
                "Облачная папка ещё синхронизируется",
                "Файл базы и служебный маркер имеют разные версии. Это обычно означает, что облачный диск ещё не закончил синхронизацию.\n\n"
                "Закройте программу, дождитесь окончания синхронизации облачной папки и запустите снова.",
                parent=self,
            )
            db.release_app_lock(self._lock_token)
            self.destroy()
            raise SystemExit

        if status.get("status") == "cloud_corrupt":
            repaired = False
            if status.get("local_valid"):
                use_local = messagebox.askyesno(
                    "Повреждена облачная база",
                    "Облачная мастер-копия не прошла проверку целостности, но на этом компьютере найдена исправная локальная рабочая база.\n\n"
                    "Использовать локальную базу для восстановления облачной копии? Повреждённый файл будет отдельно сохранён в папке recovery.",
                    parent=self,
                )
                if use_local:
                    try:
                        db.repair_cloud_from_local()
                        status = db.prepare_working_storage()
                        repaired = True
                        messagebox.showinfo("Восстановление", "Облачная база восстановлена из локальной рабочей копии.", parent=self)
                    except Exception as ex:
                        _log(f"Не удалось восстановить облачную базу из локальной: {ex}")

            if not repaired:
                backup = db.find_latest_valid_backup()
                if not backup:
                    messagebox.showerror(
                        "Повреждена база данных",
                        "Облачная база не прошла проверку целостности, а подходящей резервной копии не найдено.\n\n"
                        f"Причина: {status.get('detail')}",
                        parent=self,
                    )
                    db.release_app_lock(self._lock_token)
                    self.destroy()
                    raise SystemExit
                restore = messagebox.askyesno(
                    "Восстановление базы",
                    "Найдена исправная резервная копия:\n"
                    f"{os.path.basename(backup)}\n\nВосстановить базу из неё? Документы в attachments удаляться не будут.",
                    parent=self,
                )
                if not restore:
                    db.release_app_lock(self._lock_token)
                    self.destroy()
                    raise SystemExit
                try:
                    db.recover_database_from_backup(backup)
                    status = db.prepare_working_storage()
                    messagebox.showinfo("Восстановление", "База успешно восстановлена из резервной копии.", parent=self)
                except Exception as ex:
                    messagebox.showerror("Восстановление", f"Не удалось восстановить базу:\n{ex}", parent=self)
                    db.release_app_lock(self._lock_token)
                    self.destroy()
                    raise SystemExit

        self._storage_action = status.get("action", "ready")
        recovery = status.get("recovery")
        if self._storage_action == "conflict_cloud_wins" and recovery:
            messagebox.showwarning(
                "Конфликт копий базы",
                "Изменения обнаружены одновременно в локальной и облачной копии. Для безопасности открыта облачная версия, а локальная сохранена отдельно:\n"
                f"{recovery}",
                parent=self,
            )

    def _build_storage_status(self):
        frame = ttk.Frame(self, padding=(12, 5), style="Status.TFrame")
        frame.pack(side="bottom", fill="x")
        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=(0, 3))
        self.storage_status_label = ttk.Label(
            frame,
            textvariable=self.storage_status_var,
            style="StatusBusy.TLabel",
        )
        self.storage_status_label.pack(side="left")

    def _update_storage_status(self, state, error=None):
        # В нормальном режиме пользователь видит только короткое подтверждение.
        # Технические подробности выводятся лишь при проблеме.
        if error or str(state).upper().startswith("НЕ "):
            detail = str(error or state).strip()
            text = "⚠ Данные не синхронизированы"
            if detail and detail.upper() != "НЕ СИНХРОНИЗИРОВАНО":
                text += f" — {detail}"
            style = "StatusError.TLabel"
        elif state in ("сохранение...", "резервная копия перед обновлением..."):
            text = "Сохранение данных…"
            style = "StatusBusy.TLabel"
        else:
            text = "● Данные сохранены"
            style = "StatusOk.TLabel"
        self.storage_status_var.set(text)
        try:
            self.storage_status_label.configure(style=style)
        except Exception:
            pass
        self._update_health_status()

    def _update_health_status(self):
        # Техническая диагностика сохраняется для логики приложения, но не занимает
        # постоянное место в рабочем интерфейсе.
        parts = []
        backups = db.list_backups()
        if backups:
            age = (datetime.now() - backups[0][1]).total_seconds() / 3600
            parts.append("Backup: ✓" if age <= 36 else f"Backup: ⚠ {int(age)}ч")
        else:
            parts.append("Backup: ⚠ нет")
        enabled = db.get_setting(self.conn, "email_enabled", "0") == "1" if self.conn else False
        sender = (db.get_setting(self.conn, "email_sender", "") or "").strip() if self.conn else ""
        password = (db.get_setting(self.conn, "email_app_password", "") or "").strip() if self.conn else ""
        if enabled and sender and password:
            parts.append("Почта: ✓")
        elif enabled:
            parts.append("Почта: ⚠")
        else:
            parts.append("Почта: выкл")
        if scheduler_win.is_windows():
            try:
                now = time.time()
                if (self._health_scheduler_installed is None or
                        now - self._health_scheduler_checked_at > 60):
                    info = scheduler_win.task_info()
                    self._health_scheduler_installed = bool(info.get("installed"))
                    self._health_scheduler_checked_at = now
                parts.append("Планировщик: ✓" if self._health_scheduler_installed else "Планировщик: ⚠")
            except Exception:
                parts.append("Планировщик: ⚠")
        self.health_status_var.set(" | ".join(parts))


    def _configure_connection_autosync(self):
        if self.conn is None:
            return
        try:
            self.conn.set_trace_callback(self._db_trace_for_autosync)
        except Exception:
            pass

    def _db_trace_for_autosync(self, statement):
        # Любое реальное изменение БД автоматически ставит облачную синхронизацию
        # через несколько секунд. Повторные изменения объединяются в один запуск.
        text = (statement or "").lstrip().upper()
        if not text.startswith(("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")):
            return
        try:
            if self._sync_after_id is not None:
                self.after_cancel(self._sync_after_id)
            self._sync_after_id = self.after(5000, self._sync_after_change)
        except tk.TclError:
            pass

    def _sync_after_change(self):
        self._sync_after_id = None
        if self._closing or self.conn is None:
            return
        try:
            result = db.sync_working_to_cloud(self.conn)
            if self._lock_token:
                db.refresh_app_lock(self._lock_token)
            self._update_storage_status("синхронизировано")
            if result.get("changed"):
                _log("Хранилище: изменения автоматически синхронизированы после сохранения")
        except Exception as e:
            _log(f"Хранилище: ошибка быстрой синхронизации: {e}")
            self._update_storage_status("НЕ синхронизировано", str(e))

    def _autosync_tick(self):
        if self._closing or self.conn is None:
            return
        try:
            result = db.sync_working_to_cloud(self.conn)
            if self._lock_token:
                db.refresh_app_lock(self._lock_token)
            self._update_storage_status("синхронизировано")
            if result.get("changed"):
                _log("Хранилище: периодическая синхронизация базы выполнена")
        except Exception as e:
            _log(f"Хранилище: ошибка периодической синхронизации: {e}")
            self._update_storage_status("НЕ синхронизировано", str(e))
        finally:
            if not self._closing:
                self.after(self.AUTOSYNC_MS, self._autosync_tick)

    def _on_app_close(self):
        if self._closing:
            return
        self._closing = True
        if self._sync_after_id is not None:
            try:
                self.after_cancel(self._sync_after_id)
            except tk.TclError:
                pass
            self._sync_after_id = None
        sync_ok = True
        sync_error = None
        try:
            self._update_storage_status("сохранение...")
            self.update_idletasks()
            if self.conn is not None:
                db.sync_working_to_cloud(self.conn)
            # Суточный ZIP-бэкап хранится в облаке и включает документы.
            db.auto_backup_if_needed(db.cloud_db_path(), min_interval_hours=24)
        except Exception as e:
            sync_ok = False
            sync_error = str(e)
            _log(f"Хранилище: ошибка синхронизации при закрытии: {e}")

        if not sync_ok:
            leave = messagebox.askyesno(
                "Не удалось сохранить копию в облако",
                "Локальная база на этом компьютере сохранена, но обновить облачную копию не удалось.\n\n"
                f"Ошибка: {sync_error}\n\n"
                "Если выйти сейчас, изменения можно будет восстановить на ЭТОМ компьютере при следующем запуске. Выйти всё равно?",
                parent=self,
            )
            if not leave:
                self._closing = False
                self._update_storage_status("НЕ синхронизировано", sync_error)
                return

        try:
            if self.conn is not None:
                self.conn.close()
                self.conn = None
        finally:
            if self._lock_token:
                try:
                    db.release_app_lock(self._lock_token)
                except Exception:
                    pass
                self._lock_token = None
            self.destroy()

    # ---------------- Фирменный стиль ----------------
    def _apply_fonts(self):
        # Единая дизайн-система синхронизирована с КП и сопроводительным письмом.
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont", "TkFixedFont"):
            try:
                f = tkfont.nametofont(name)
                f.configure(family=app_theme.FONT, size=BASE_FONT_SIZE)
            except tk.TclError:
                pass
        style = app_theme.apply(self)
        style.configure("Treeview", font=(app_theme.FONT, BASE_FONT_SIZE), rowheight=TREE_ROW_HEIGHT)
        style.configure("Treeview.Heading", font=(app_theme.FONT, BASE_FONT_SIZE, "bold"))
        style.configure("Purchases.Treeview", font=(app_theme.FONT, 13), rowheight=92)
        style.configure("Documents.Treeview", rowheight=TREE_ROW_HEIGHT)
        # Выделение остаётся спокойным светло-синим, а не системным ярко-синим.
        for style_name in ("Treeview", "Purchases.Treeview", "Documents.Treeview"):
            style.map(style_name, background=[("selected", app_theme.SOFT_BLUE)],
                      foreground=[("selected", app_theme.INK)])

    def _resource_path(self, relative):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, relative)

    def _build_brand_header(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(24, 14))
        header.pack(side="top", fill="x")

        left = ttk.Frame(header, style="Header.TFrame")
        left.pack(side="left", fill="y")
        title_box = ttk.Frame(left, style="Header.TFrame")
        title_box.pack(side="left", anchor="center")
        ttk.Label(title_box, text="Учет заключенных контрактов",
                  style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="ИП ПЕТРУШКИН А. А.",
                  style="HeaderOwner.TLabel").pack(anchor="w", pady=(5, 0))

        right = ttk.Frame(header, style="Header.TFrame")
        right.pack(side="right", fill="y")
        ttk.Label(
            right,
            text="«Единственный разумный способ обучить людей — это подавать им пример». — А. Эйнштейн",
            style="HeaderQuote.TLabel",
            justify="right",
            wraplength=640,
        ).pack(side="right", padx=(18, 3))

    # ---------------- Меню ----------------
    def _build_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Экспорт в Excel...", command=self._export_excel)
        file_menu.add_separator()
        file_menu.add_command(label="Корзина...", command=self._open_trash)
        file_menu.add_separator()
        file_menu.add_command(label="Создать резервную копию сейчас", command=self._backup_now)
        file_menu.add_command(label="Восстановить из резервной копии...", command=self._restore_backup)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self._on_app_close)
        menubar.add_cascade(label="Файл", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Обновления...", command=self._open_update_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="Почта...", command=self._open_mail_settings)
        settings_menu.add_command(label="Отправить тестовое письмо", command=self._send_test_email_from_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="Справочник товаров...", command=self._open_product_catalog)
        menubar.add_cascade(label="Настройки", menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="О программе", command=lambda: messagebox.showinfo(
            "О программе", f"Учет заключенных контрактов v{__version__}\nПерсональный рабочий инструмент на Python/Tkinter/SQLite."))
        menubar.add_cascade(label="Справка", menu=help_menu)
        self.config(menu=menubar)

    def _open_update_settings(self):
        """v2.17.2: обновления выполняются вручную, вне приложения."""
        win = tk.Toplevel(self)
        win.title("Обновления")
        win.transient(self)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=f"Установленная версия: v{__version__}",
            font=(app_theme.FONT, BASE_FONT_SIZE, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            frame,
            text=(
                "Автоматическое обновление отключено. Новую проверенную сборку "
                "UchetZakupok.exe устанавливайте вручную при закрытом приложении."
            ),
            wraplength=500,
            justify="left",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 12))
        ttk.Button(frame, text="Закрыть", command=win.destroy).pack(anchor="w")

    def _start_update_check(self, manual=False, parent=None, status_var=None):
        """Проверяет latest GitHub Release в отдельном потоке."""
        if self._update_check_running:
            if status_var is not None:
                status_var.set("Проверка уже выполняется...")
            return
        if not manual and db.get_setting(self.conn, "update_auto_enabled", "1") == "0":
            return
        # Репозиторий обновлений является частью приложения и не редактируется пользователем.
        repo = DEFAULT_UPDATE_REPO
        if not getattr(sys, "frozen", False) and not manual:
            # При разработке из main.py не показываем релизы конечному пользователю.
            return
        self._update_check_running = True

        result = {}

        def worker():
            try:
                result["info"] = auto_update.check_latest(repo)
                result["error"] = None
            except Exception as exc:
                result["info"] = None
                result["error"] = str(exc)
            result["done"] = True

        def poll():
            if not result.get("done"):
                try:
                    self.after(100, poll)
                except tk.TclError:
                    pass
                return
            self._finish_update_check(result.get("info"), result.get("error"), manual, parent, status_var)

        threading.Thread(target=worker, name="update-check", daemon=True).start()
        self.after(100, poll)

    def _finish_update_check(self, info, error, manual=False, parent=None, status_var=None):
        self._update_check_running = False
        if error:
            _log(f"Автообновление: проверка не выполнена: {error}")
            if manual:
                if status_var is not None:
                    status_var.set("Не удалось проверить обновления.")
                messagebox.showwarning("Обновления", error, parent=parent or self)
            return
        if info is None:
            if manual:
                if status_var is not None:
                    status_var.set(f"Установлена актуальная версия v{__version__}.")
                messagebox.showinfo("Обновления", f"Установлена актуальная версия v{__version__}.", parent=parent or self)
            return
        if status_var is not None:
            status_var.set(f"Доступна версия v{info.version}.")
        notes = (info.notes or "").strip()
        if len(notes) > 600:
            notes = notes[:600].rstrip() + "…"
        extra = f"\n\nИзменения:\n{notes}" if notes else ""
        install = messagebox.askyesno(
            "Доступно обновление",
            f"Установлена версия v{__version__}.\nДоступна версия v{info.version}.{extra}\n\n"
            "Перед установкой будет создана резервная копия базы и документов.\nУстановить обновление сейчас?",
            parent=parent or self,
        )
        if install:
            self._download_update(info, parent=parent, status_var=status_var)

    def _download_update(self, info, parent=None, status_var=None):
        if self._update_download_running:
            return
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(
                "Обновления",
                "Проверка работает, но автоматическая установка доступна в собранном UchetZakupok.exe.",
                parent=parent or self,
            )
            return
        self._update_download_running = True
        if status_var is not None:
            status_var.set(f"Скачиваю v{info.version} и проверяю SHA-256...")
        try:
            self.storage_status_var.set(f"Обновление v{info.version}: загрузка...")
        except Exception:
            pass
        update_root = os.path.join(db.local_data_dir(), "updates")

        result = {}

        def worker():
            try:
                result["path"] = auto_update.download_update(info, update_root)
                result["error"] = None
            except Exception as exc:
                result["path"] = None
                result["error"] = str(exc)
            result["done"] = True

        def poll():
            if not result.get("done"):
                try:
                    self.after(100, poll)
                except tk.TclError:
                    pass
                return
            self._finish_update_download(info, result.get("path"), result.get("error"), parent, status_var, update_root)

        threading.Thread(target=worker, name="update-download", daemon=True).start()
        self.after(100, poll)

    def _finish_update_download(self, info, path, error, parent, status_var, update_root):
        self._update_download_running = False
        if error:
            _log(f"Автообновление: загрузка не выполнена: {error}")
            if status_var is not None:
                status_var.set("Ошибка загрузки обновления.")
            self._update_storage_status("синхронизировано")
            messagebox.showerror("Обновление", error, parent=parent or self)
            return
        if status_var is not None:
            status_var.set("Файл проверен. Создаю резервную копию и перезапускаю приложение...")
        self._install_downloaded_update(info, path, update_root, parent=parent)

    def _install_downloaded_update(self, info, new_exe, update_root, parent=None):
        """Синхронизирует данные, делает полный бэкап и запускает внешний updater."""
        try:
            self._update_storage_status("резервная копия перед обновлением...")
            self.update_idletasks()
            if self.conn is not None:
                db.sync_working_to_cloud(self.conn)
            backup = db.create_backup(db.cloud_db_path())
            if not backup:
                raise RuntimeError("Не удалось создать резервную копию перед обновлением.")
            _log(f"Автообновление: создан бэкап {backup}")
            auto_update.launch_windows_installer(new_exe, update_root, db.local_log_path())
        except Exception as exc:
            _log(f"Автообновление: установка отменена: {exc}")
            self._update_storage_status("синхронизировано")
            messagebox.showerror("Обновление не установлено", str(exc), parent=parent or self)
            return

        # После запуска внешнего updater нельзя использовать обычный _on_app_close:
        # он способен показать вопрос при проблеме облака уже после запуска замены EXE.
        self._closing = True
        try:
            if self.conn is not None:
                self.conn.close()
                self.conn = None
        finally:
            if self._lock_token:
                try:
                    db.release_app_lock(self._lock_token)
                except Exception:
                    pass
                self._lock_token = None
            self.destroy()

    def _open_product_catalog(self):
        ProductCatalogDialog(self, self.conn, on_change=self.refresh_all)

    def _open_mail_settings(self):
        MailSettingsDialog(self, self.conn)

    def _mail_config(self):
        enabled = db.get_setting(self.conn, "email_enabled", "0") == "1"
        sender = email_notify.normalize_email(db.get_setting(self.conn, "email_sender", "") or "")
        recipients = email_notify.parse_recipients(db.get_setting(self.conn, "email_recipients", "") or "")
        return enabled, sender, recipients

    def _send_test_email_from_settings(self):
        enabled, sender, recipients = self._mail_config()
        try:
            if not sender or not recipients:
                raise email_notify.EmailConfigError("Сначала заполните «Настройки → Почта…».")
            password = (db.get_setting(self.conn, "email_app_password", "") or "").strip()
            if not password:
                raise email_notify.EmailConfigError("Gmail App Password не сохранён в облачных настройках.")
            email_notify.send_gmail(
                sender, recipients,
                "Тест уведомлений — Учет заключенных контрактов",
                "Это тестовое письмо. Интеграция Gmail в приложении работает.",
                app_password=password,
            )
            messagebox.showinfo("Почта", "Тестовое письмо отправлено.", parent=self)
        except Exception as exc:
            messagebox.showerror("Почта", f"Не удалось отправить тестовое письмо:\n{exc}", parent=self)

    def _signing_email_candidates(self):
        """Только 3, 2 и 1 день до подписания — ровно по новому ТЗ."""
        rows = db.deadline_rows(self.conn)
        result = []
        for r in rows:
            d = parse_date_iso_to_date(r["sign_deadline"])
            state, days = signing_reminder_state(d, r["contract_status"])
            if state == "red" and days in (3, 2, 1):
                result.append((r, days, d))
        result.sort(key=lambda x: (x[2] or date.max, x[0]["contract_no"] or ""))
        return result

    def _send_attention_email_if_due(self):
        """Единый отправитель для GUI и фоновой задачи; защита от дублей общая."""
        if self._closing or self.conn is None:
            return
        # Обычный запуск приложения — явное действие пользователя. Он должен иметь
        # право выполнить проверку независимо от того, на каком ПК установлен
        # фоновый Планировщик. Сам фоновый worker по-прежнему уважает scheduler_host.
        result = reminder_worker.run_attention_digest(
            self.conn,
            respect_scheduler_host=False,
        )
        if result.get("status") != "error":
            status = result.get("status")
            _log(
                "Почта: результат ежедневной сводки при открытом приложении: "
                f"status={status}, sent={result.get('sent')}, count={result.get('count', 0)}"
            )
            # При запуске приложения не показываем служебные предупреждения о почте:
            # пользователь видит задачи во вкладке «Требует внимания», а детали
            # отправки остаются в журнале и настройках почты.
            if status in ("disabled", "not_configured"):
                due = reminder_worker.attention_items(self.conn)
                if due:
                    _log(
                        "Почта: сводка не отправлена при запуске: "
                        + ("отключена" if status == "disabled" else "не настроена полностью")
                    )
            return
        exc = result.get("error") or "неизвестная ошибка"
        # Ошибка фоновой почтовой сводки не должна блокировать работу стартовым окном.
        # Она остаётся в журнале и доступна через настройки/проверку почты.
        _log(f"Почта: ошибка ежедневной сводки: {exc}")

    def _open_trash(self):
        TrashDialog(self, self.conn, on_change=self.refresh_all)

    def _backup_now(self):
        try:
            db.sync_working_to_cloud(self.conn)
            path = db.create_backup(db.cloud_db_path())
            self._update_storage_status("синхронизировано")
        except Exception as e:
            messagebox.showerror("Резервная копия", f"Не удалось подготовить резервную копию:\n{e}", parent=self)
            return
        if path:
            messagebox.showinfo("Резервная копия",
                                 f"Резервная копия (база данных + все документы) создана:\n{path}",
                                 parent=self)
        else:
            messagebox.showwarning("Резервная копия", "Файл базы данных не найден.", parent=self)

    def _restore_backup(self):
        backups = db.list_backups()
        if not backups:
            messagebox.showinfo("Резервные копии", "Резервных копий пока нет.", parent=self)
            return
        win = tk.Toplevel(self)
        win.title("Восстановить из резервной копии")
        win.transient(self)
        win.grab_set()
        ttk.Label(win, text="Выберите резервную копию (данные и документы будут заменены):",
                  padding=10).pack(anchor="w")
        listbox = tk.Listbox(win, width=55, height=min(10, len(backups)))
        for path, mtime in backups:
            suffix = "" if path.lower().endswith(".zip") else "  (старый формат, без документов)"
            listbox.insert("end", f"{mtime.strftime('%d.%m.%Y %H:%M:%S')} — {os.path.basename(path)}{suffix}")
        listbox.pack(padx=10, pady=(0, 10), fill="both", expand=True)

        def do_restore():
            sel = listbox.curselection()
            if not sel:
                return
            path, _ = backups[sel[0]]
            if messagebox.askyesno("Восстановление",
                                    "Текущие данные и документы будут заменены содержимым "
                                    "резервной копии. Продолжить?",
                                    parent=win):
                self.conn.close()
                db.restore_backup(path)
                self.conn = db.get_connection()
                self._configure_connection_autosync()
                db.migrate_attachment_paths(self.conn)
                db.sync_working_to_cloud(self.conn)
                self._update_storage_status("синхронизировано")
                self.refresh_all()
                win.destroy()
                messagebox.showinfo("Готово", "База данных и документы восстановлены из резервной копии.",
                                     parent=self)

        ttk.Button(win, text="Восстановить", command=do_restore).pack(pady=(0, 10))

    # ---------------- Вкладки ----------------
    def _make_attention_tab_icon(self, color):
        """Небольшой цветной круг для вкладки «Требует внимания»."""
        size = 12
        img = tk.PhotoImage(width=size, height=size)
        cx = cy = (size - 1) / 2
        radius2 = 25.0
        for y in range(size):
            for x in range(size):
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius2:
                    img.put(color, (x, y))
        return img

    def _set_attention_tab_state(self, counts):
        if not (hasattr(self, "nb") and hasattr(self, "tab_attention")):
            return
        overdue = int(counts.get("overdue", 0) or 0)
        total = int(counts.get("total", 0) or 0)
        icon = self._attention_tab_icon_overdue if overdue else self._attention_tab_icon
        self.nb.tab(self.tab_attention, text=f"ТРЕБУЕТ ВНИМАНИЯ ({total})",
                    image=icon, compound="left")

    def _build_tabs(self):
        nb = ttk.Notebook(self)
        self.nb = nb
        nb.pack(fill="both", expand=True)

        self.tab_purchases = ttk.Frame(nb)
        self.tab_attention = ttk.Frame(nb)
        self.tab_summary = ttk.Frame(nb)
        self.tab_stock = ttk.Frame(nb)
        self.tab_competitors = ttk.Frame(nb)
        self.tab_calculator = ttk.Frame(nb)

        # Названия вкладок — обычный текст, без эмодзи-квадратов. Пробовали цветные
        # квадраты-эмодзи, но на реальном скриншоте пользователя они отрисовались как
        # серые «шашечки» — символ «глиф отсутствует в шрифте» — то есть шрифт Windows
        # у пользователя просто не знает такие символы (это не про цвет и не про тему
        # оформления, а про отсутствие глифа). Кириллический текст рендерится нормально
        # (виден на том же скриншоте), поэтому опираемся только на него.
        # Единственная точка входа в центр внимания — сама вкладка. Цветной
        # индикатор сделан как изображение, а не символ шрифта: так он одинаково
        # отображается в Windows при любой установленной гарнитуре.
        self._attention_tab_icon = self._make_attention_tab_icon(app_theme.ACCENT)
        self._attention_tab_icon_overdue = self._make_attention_tab_icon(app_theme.RED)

        nb.add(self.tab_purchases, text="Контракты")
        nb.add(self.tab_attention, text="ТРЕБУЕТ ВНИМАНИЯ (0)",
               image=self._attention_tab_icon, compound="left")
        nb.add(self.tab_summary, text="Итоги")
        nb.add(self.tab_stock, text="Склад")
        nb.add(self.tab_competitors, text="Анализ конкурентов")
        nb.add(self.tab_calculator, text="Калькулятор цены")

        self._build_purchases_tab()
        self._build_attention_tab()
        self._build_summary_tab()
        self._build_stock_tab()
        self._build_competitors_tab()
        self._build_calculator_tab()

    # ---- Вкладка "Контракты" ----
    PURCHASE_COLS = [
        ("num", "№ / Дата контракта", 175),
        ("platform", "Площадка", 130), ("customer", "Заказчик", 380),
        ("product", "Товары / Кол-во", 260), ("contract_sum", "Сумма контракта", 130),
        ("deadline", "Срок исполнения", 150),
        ("payment_status", "Оплата", 130),
    ]

    def _build_purchases_tab(self):
        top = ttk.Frame(self.tab_purchases, padding=8)
        top.pack(fill="x")

        kpi = ttk.Frame(top, padding=(0, 0, 0, 8))
        kpi.pack(fill="x")
        self.kpi_total_var = tk.StringVar(value="Всего контрактов — 0")
        self.kpi_work_var = tk.StringVar(value="Контрактов в работе — 0")
        self.kpi_sum_var = tk.StringVar(value="Сумма контрактов — 0 ₽")
        self.kpi_reserve_var = tk.StringVar(value="В резерве — 0 шт.")
        self.kpi_payment_var = tk.StringVar(value="Ожидают оплаты — 0 ₽")
        for var in (self.kpi_total_var, self.kpi_work_var, self.kpi_sum_var, self.kpi_reserve_var, self.kpi_payment_var):
            ttk.Label(kpi, textvariable=var, style="KPI.TLabel").pack(side="left", padx=(0, 8))
        row1 = ttk.Frame(top)
        row1.pack(fill="x")
        ttk.Label(row1, text="Год:").pack(side="left")
        self.year_var = tk.StringVar(value=str(date.today().year))
        self.year_combo = ttk.Combobox(row1, textvariable=self.year_var, width=8, state="readonly")
        self.year_combo.pack(side="left", padx=(2, 10))
        self.year_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_purchases())

        ttk.Label(row1, text="Месяц:").pack(side="left")
        self.month_var = tk.StringVar(value=MONTHS_RU[date.today().month])
        self.month_combo = ttk.Combobox(row1, textvariable=self.month_var, width=12, state="readonly",
                                         values=["Все"] + MONTHS_RU[1:])
        self.month_combo.pack(side="left", padx=(2, 10))
        self.month_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_purchases())

        row3 = ttk.Frame(top)
        row3.pack(fill="x", pady=(6, 0))
        ttk.Button(row3, text="+ Новый контракт", command=self._add_purchase, style="Primary.TButton").pack(side="left", padx=4)
        ttk.Button(row3, text="На основе текущего", command=self._duplicate_purchase).pack(side="left", padx=4)
        ttk.Button(row3, text="Удалить", command=self._delete_purchase, style="Danger.TButton").pack(side="left", padx=4)
        ttk.Separator(row3, orient="vertical").pack(side="left", fill="y", padx=8)
        self.main_next_action_var = tk.StringVar(value="Выберите контракт")
        self.main_next_action_button = ttk.Button(
            row3,
            textvariable=self.main_next_action_var,
            command=self._perform_main_next_action,
            style="Primary.TButton",
            state="disabled",
        )
        self.main_next_action_button.pack(side="left", padx=3)


        cols = [c[0] for c in self.PURCHASE_COLS]
        # Многострочный вывод товаров: одна товарная позиция = одна строка в ячейке.
        # Treeview использует единую высоту строки, поэтому задаём запас для нескольких
        # позиций, чтобы текст не обрезался.
        style = ttk.Style(self)
        style.configure("Purchases.Treeview", font=(app_theme.FONT, 13), rowheight=92)
        # Общий стиль Treeview выше сохраняет цвета тегов и поэтому отключает
        # системную карту выделения. Для списка контрактов задаём её явно:
        # выбранная строка должна быть хорошо видна пользователю.
        style.map("Purchases.Treeview",
                  background=[("selected", app_theme.SOFT_BLUE)],
                  foreground=[("selected", app_theme.INK)])
        self.tree = ttk.Treeview(self.tab_purchases, columns=cols, show="headings",
                                 selectmode="browse", style="Purchases.Treeview")
        for key, label, width in self.PURCHASE_COLS:
            self.tree.heading(key, text=label, anchor="center")
            cell_anchor = "w" if key in ("platform", "customer", "product") else "center"
            self.tree.column(key, width=width, anchor=cell_anchor)
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        def open_row_on_double_click(event):
            """Двойной щелчок открывает только строку непосредственно под курсором."""
            try:
                if self.tree.identify_region(event.x, event.y) != "cell":
                    return "break"
                row = self.tree.identify_row(event.y)
            except tk.TclError:
                return "break"
            if not row:
                return "break"
            # Не полагаемся на состояние selection от первого клика: это защищает
            # от открытия ранее выбранной строки при быстром двойном щелчке.
            self.tree.selection_set(row)
            self.tree.focus(row)
            self.tree.see(row)
            self._edit_purchase()
            return "break"

        self.tree.bind("<Double-1>", open_row_on_double_click)
        def copy_full_contract_row_event(event=None):
            # Новое ТЗ: Ctrl+C в любой таблице копирует выбранную строку целиком.
            return copy_treeview_rows(self.tree)

        self.tree.bind("<Control-c>", copy_full_contract_row_event)
        self.tree.bind("<Control-C>", copy_full_contract_row_event)
        self.tree.bind("<Control-Insert>", copy_full_contract_row_event)

        def select_row_on_left_click(event):
            # Один обычный щелчок выполняет ровно одно действие: выбирает строку.
            # Никакого копирования/открытия карточки на single-click больше нет.
            try:
                if self.tree.identify_region(event.x, event.y) != "cell":
                    return
                row = self.tree.identify_row(event.y)
            except tk.TclError:
                return
            if row:
                self.tree.selection_set(row)
                self.tree.focus(row)
                self.tree.see(row)
                self._update_main_next_action()

        self.tree.bind("<Button-1>", select_row_on_left_click, add="+")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_main_next_action(), add="+")

        row_menu = tk.Menu(self.tree, tearoff=0)
        row_menu.add_command(label="Копировать строку (Ctrl+C)", command=lambda: copy_treeview_rows(self.tree))
        row_menu.add_command(label="Копировать номер и дату", command=self._copy_contract_ref_to_clipboard)
        row_menu.add_separator()
        row_menu.add_command(label="Отметить: Заключен", command=lambda: self._quick_set_status("contract_status", "Заключен"))
        row_menu.add_command(label="Отметить: Исполнено", command=lambda: self._quick_set_status("exec_status", "Исполнено"))
        row_menu.add_command(label="Отметить: Оплачено", command=lambda: self._quick_set_status("payment_status", "Оплачено"))
        row_menu.add_separator()
        row_menu.add_command(label="Редактировать", command=self._edit_purchase)
        row_menu.add_command(label="Создать на основе текущего (Ctrl+D)", command=self._duplicate_purchase)
        row_menu.add_command(label="Удалить", command=self._delete_purchase)

        def show_row_menu(event):
            item = self.tree.identify_row(event.y)
            if item:
                self.tree.selection_set(item)
                self.tree.focus(item)
                row_menu.tk_popup(event.x_root, event.y_root)
            return "break"
        self.tree.bind("<Button-3>", show_row_menu)


    def _selected_id(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    @staticmethod
    def _main_next_action_spec(existing):
        if existing is None:
            return None
        if (existing["contract_status"] or "") != "Заключен":
            return ("contract_status", "Заключен", "Следующее действие: заключить")
        if (existing["exec_status"] or "") != "Исполнено":
            return ("exec_status", "Исполнено", "Следующее действие: исполнить")
        if (existing["payment_status"] or "") != "Оплачено":
            return ("payment_status", "Оплачено", "Следующее действие: отметить оплаченным")
        return None

    def _update_main_next_action(self):
        if not hasattr(self, "main_next_action_button"):
            return
        pid = self._selected_id()
        if pid is None:
            self.main_next_action_var.set("Выберите контракт")
            self.main_next_action_button.configure(state="disabled")
            return
        existing = db.fetch_by_id(self.conn, pid)
        spec = self._main_next_action_spec(existing)
        if spec is None:
            self.main_next_action_var.set("Контракт завершён")
            self.main_next_action_button.configure(state="disabled")
            return
        self.main_next_action_var.set(spec[2])
        self.main_next_action_button.configure(state="normal")

    def _perform_main_next_action(self):
        pid = self._selected_id()
        if pid is None:
            self._update_main_next_action()
            return
        existing = db.fetch_by_id(self.conn, pid)
        spec = self._main_next_action_spec(existing)
        if spec is None:
            self._update_main_next_action()
            return
        self._quick_set_status(spec[0], spec[1])

    def _current_filters(self):
        def none_if_all(v):
            return None if v in ("Все", "") else v
        year = None if self.year_var.get() in ("Все", "") else int(self.year_var.get())
        month = None
        if self.month_var.get() not in ("Все", ""):
            month = MONTHS_RU.index(self.month_var.get())
        return dict(year=year, month=month, search=None)

    def refresh_purchases(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        filters = self._current_filters()
        rows = db.fetch_all(self.conn, **filters, operational_period=True)

        # Исходный текст не переносим заранее: tree_insert_wrapped измеряет
        # фактическую ширину каждой колонки в пикселях. При ручном расширении или
        # сужении заголовка текст автоматически пересчитывается — лишние переносы
        # исчезают, а при сужении появляются снова.
        prepared_rows = []
        for r in rows:
            customer_text = " ".join(str(r["customer"] or "—").split())
            # Каждая товарная позиция остаётся отдельной логической строкой, но
            # длинное наименование внутри неё переносится только если реально не
            # помещается в текущую ширину столбца.
            items_text = "\n".join(
                f"{it['product'] or '—'} — {fmt_qty(it['qty'])} шт."
                for it in r.get("items", [])
            ) or "—"
            prepared_rows.append((r, customer_text, items_text))

        # Сбрасываем сохранённые исходные значения перед полной перерисовкой.
        self.tree._raw_tree_values = {}
        self.tree._wrap_max_lines = 1
        for r, customer_text, items_text in prepared_rows:
            contract_sum = r["contract_sum"] or 0.0
            contract_date_str = fmt_date(r["contract_date"])
            contract_no = r["contract_no"] or "—"
            num_cell = f"{contract_no} от {contract_date_str}" if contract_date_str else contract_no
            values = [num_cell or "—", r["platform"] or "—", customer_text, items_text,
                      fmt_money(contract_sum), fmt_date(r["deadline"]) or "—", r["payment_status"] or "—"]
            tree_insert_wrapped(self.tree, "", "end", iid=str(r["id"]), values=values)

        _schedule_tree_rewrap(self.tree, 20)

        # KPI используют тот же выбранный год/месяц, что и таблица контрактов.
        kpi = db.dashboard_kpis(self.conn, year=filters["year"], month=filters["month"])
        self.kpi_total_var.set(f"Всего контрактов — {kpi['total_count']}")
        self.kpi_work_var.set(f"Контрактов в работе — {kpi['work_count']}")
        self.kpi_sum_var.set(f"Сумма контрактов — {fmt_money(kpi['total_sum'])}")
        self.kpi_reserve_var.set(f"В резерве — {fmt_qty(kpi['reserve_qty'])} шт.")
        self.kpi_payment_var.set(f"Ожидают оплаты — {fmt_money(kpi['awaiting'])}")
        counts = reminder_worker.attention_counts(reminder_worker.attention_items(self.conn))
        self._set_attention_tab_state(counts)
        self._update_main_next_action()

    def _add_purchase(self):
        _log("Клик «Добавить» в таблице контрактов -> открываю PurchaseDialog")

        def on_save(header, items):
            pid = db.insert_purchase(self.conn, header, items)
            # Новый контракт после сохранения показываем в календарном текущем месяце,
            # даже если пользователь до нажатия «Добавить» просматривал архивный период.
            self.year_var.set(str(date.today().year))
            self.month_var.set(MONTHS_RU[date.today().month])
            return pid
        PurchaseDialog(self, on_save, conn=self.conn)

    def _edit_purchase(self):
        pid = self._selected_id()
        if pid is None:
            messagebox.showinfo("Выбор контракта", "Сначала выберите строку в таблице.")
            return
        _log(f"Открытие контракта двойным кликом/командой редактирования id={pid} -> открываю PurchaseDialog")
        existing = db.fetch_by_id(self.conn, pid)

        def on_save(header, items):
            db.update_purchase(self.conn, pid, header, items)
            return pid
        PurchaseDialog(self, on_save, existing=existing, conn=self.conn)

    def _duplicate_purchase(self):
        pid = self._selected_id()
        if pid is None:
            messagebox.showinfo("Выбор контракта", "Сначала выберите контракт, на основе которого создать новый.", parent=self)
            return
        src = db.fetch_by_id(self.conn, pid)
        if src is None:
            return
        # Копируем только повторно используемые данные. Номер, даты, суммы, документы и статусы не переносим.
        prefill = {f: None for f in db.HEADER_FIELDS}
        for key in ("platform", "customer", "law", "note",
                    "resp_purchase_name", "resp_purchase_phone", "resp_purchase_email",
                    "resp_receiving_name", "resp_receiving_phone", "resp_receiving_email"):
            if key in src:
                prefill[key] = src.get(key)
        prefill["contract_status"] = "Формирование"
        prefill["payment_status"] = "Не оплачено"
        prefill["exec_status"] = "В процессе"
        prefill["items"] = [dict(x) for x in src.get("items", [])]

        def on_save(header, items):
            new_id = db.insert_purchase(self.conn, header, items)
            self.year_var.set(str(date.today().year))
            self.month_var.set(MONTHS_RU[date.today().month])
            return new_id

        _log(f"Создание нового контракта на основе id={pid}")
        PurchaseDialog(self, on_save, conn=self.conn, prefill=prefill)

    def _quick_set_status(self, field, value):
        pid = self._selected_id()
        if pid is None:
            messagebox.showinfo("Выбор контракта", "Сначала выберите строку в таблице.", parent=self)
            return
        existing = db.fetch_by_id(self.conn, pid)
        if existing is None:
            return
        header = {f: existing[f] for f in db.HEADER_FIELDS}
        items = [dict(x) for x in existing["items"]]
        contract_no = existing["contract_no"] or "—"
        date_field = None
        date_caption = None
        if field == "contract_status" and value == "Заключен":
            date_field, date_caption = "contract_date", "Дата заключения"
        elif field == "exec_status" and value == "Исполнено":
            date_field, date_caption = "handover_date", "Дата вручения"

        lines = [f"Отметить контракт № {contract_no} как «{value}»?"]
        if date_field:
            raw = header.get(date_field)
            if raw:
                shown = fmt_date(raw)
                lines.append(f"{date_caption}: {shown}")
            else:
                shown = date.today().strftime(DATE_FMT)
                lines.append(f"{date_caption}: {shown} (будет установлена автоматически)")
        if not messagebox.askyesno("Быстрое действие", "\n".join(lines), parent=self):
            return
        if date_field and not header.get(date_field):
            header[date_field] = date.today().isoformat()
        header[field] = value
        db.update_purchase(self.conn, pid, header, items)
        self.refresh_all()

    def _delete_purchase(self):
        pid = self._selected_id()
        if pid is None:
            messagebox.showinfo("Выбор контракта", "Сначала выберите строку в таблице.")
            return
        if messagebox.askyesno("Удаление",
                                "Переместить контракт в корзину? Документы и данные не удалятся "
                                f"физически — контракт можно будет восстановить в течение "
                                f"{db.TRASH_KEEP_DAYS} дней (Файл → «Корзина...»), после чего он "
                                "будет удалён окончательно."):
            db.delete_purchase(self.conn, pid)
            self.refresh_all()

    def _copy_contract_ref_to_clipboard(self):
        """Копирует только номер контракта и дату из выбранной строки."""
        sel = self.tree.selection()
        if not sel:
            return
        row = sel[0]
        text = self.tree.set(row, "num")
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        _log(f"Главная таблица: скопированы номер и дата контракта id={row}: {text}")

    def _export_excel(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx", filetypes=[("Excel файл", "*.xlsx")],
            initialfile="Контракты.xlsx")
        if not filepath:
            return
        from excel_export import export_to_excel
        rows = db.fetch_all(self.conn, year=None, month=None)
        export_to_excel(rows, filepath)
        messagebox.showinfo("Экспорт", f"Данные выгружены в файл:\n{filepath}")

    # ---- Вкладка "Требует внимания" ----
    def _build_attention_tab(self):
        top = ttk.Frame(self.tab_attention, padding=8)
        top.pack(fill="x")
        self.attention_total_var = tk.StringVar(value="Всего — 0")
        self.attention_overdue_var = tk.StringVar(value="Просрочено — 0")
        self.attention_contracts_var = tk.StringVar(value="Сроки — 0")
        self.attention_procurement_var = tk.StringVar(value="Пора закупать — 0")
        self.attention_payment_var = tk.StringVar(value="Ожидают оплаты — 0")
        self.attention_stock_var = tk.StringVar(value="Склад — 0")
        ttk.Label(top, textvariable=self.attention_total_var, style="AttentionKPI.TLabel").pack(side="left", padx=(0, 8))
        ttk.Label(top, textvariable=self.attention_overdue_var, style="OverdueKPI.TLabel").pack(side="left", padx=(0, 8))
        ttk.Label(top, textvariable=self.attention_contracts_var, style="KPI.TLabel").pack(side="left", padx=(0, 8))
        ttk.Label(top, textvariable=self.attention_procurement_var, style="KPI.TLabel").pack(side="left", padx=(0, 8))
        ttk.Label(top, textvariable=self.attention_payment_var, style="KPI.TLabel").pack(side="left", padx=(0, 8))
        ttk.Label(top, textvariable=self.attention_stock_var, style="KPI.TLabel").pack(side="left", padx=(0, 8))
        ttk.Button(top, text="Обновить", command=self.refresh_attention).pack(side="right", padx=4)
        ttk.Label(
            self.tab_attention,
            text=("Единый рабочий список: подписание, исполнение, оплата и критические остатки. "
                  "Срок уже содержит информацию о срочности; двойной щелчок открывает нужный объект."),
            padding=(8, 0, 8, 8), wraplength=1300, justify="left", foreground=app_theme.MUTED
        ).pack(fill="x")

        cols = ("event", "customer", "object", "contract_no", "deadline", "action")
        labels = ("Событие", "Заказчик", "Товар / объект", "№ контракта", "Срок", "Действие")
        widths = (150, 300, 300, 140, 230, 180)
        self.attention_tree = ttk.Treeview(self.tab_attention, columns=cols, show="headings")
        for key, label, width in zip(cols, labels, widths):
            self.attention_tree.heading(key, text=label, anchor="center")
            anchor = "w" if key in ("customer", "object", "action") else "center"
            self.attention_tree.column(key, width=width, anchor=anchor)
        self.attention_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.attention_tree.tag_configure("overdue", background=app_theme.SOFT_RED, foreground=app_theme.RED)
        self.attention_tree.tag_configure("critical", background=app_theme.SOFT_RED, foreground=app_theme.RED)
        self.attention_tree.tag_configure("warning", background=app_theme.SOFT_YELLOW, foreground=app_theme.AMBER)
        self.attention_tree.bind("<Double-1>", lambda e: self._open_attention_item())

    def refresh_attention(self):
        for row in self.attention_tree.get_children():
            self.attention_tree.delete(row)
        self.attention_tree._attention_items = {}

        items = reminder_worker.attention_items(self.conn)
        counts = reminder_worker.attention_counts(items)
        self.attention_total_var.set(f"Всего — {counts['total']}")
        self.attention_overdue_var.set(f"Просрочено — {counts['overdue']}")
        self.attention_contracts_var.set(
            f"Сроки — {counts['signing'] + counts['execution']}"
        )
        self.attention_procurement_var.set(f"Пора закупать — {counts.get('procurement', 0)}")
        self.attention_payment_var.set(f"Ожидают оплаты — {counts['payment']}")
        self.attention_stock_var.set(f"Склад — {counts['stock']}")
        self._set_attention_tab_state(counts)

        for idx, item in enumerate(items):
            days = item.get("days")
            if item.get("purchase_id") is None:
                deadline_text = f"Доступно {fmt_qty(item.get('available', 0))} шт."
                action_text = "Открыть склад"
            else:
                deadline = item["date"].strftime(DATE_FMT) if item.get("date") else "Срок не указан"
                if days is None:
                    deadline_text = deadline
                elif days < 0:
                    deadline_text = f"{deadline} · просрочено {abs(days)} дн."
                elif days == 0:
                    deadline_text = f"{deadline} · сегодня"
                elif days == 1:
                    deadline_text = f"{deadline} · завтра"
                else:
                    deadline_text = f"{deadline} · осталось {days} дн."
                action_text = "Открыть контракт"

            iid = (f"contract_{item['purchase_id']}_{item['kind']}_{idx}"
                   if item.get("purchase_id") is not None else f"stock_{idx}")
            values = [
                item["type"],
                item.get("customer") or "—",
                item.get("product") or "—",
                item.get("contract_no") or "—",
                deadline_text,
                action_text,
            ]
            tree_insert_wrapped(
                self.attention_tree, "", "end", iid=iid, values=values,
                tags=(item["severity"],),
            )
            self.attention_tree._attention_items[iid] = item

    def _open_attention_item(self):
        sel = self.attention_tree.selection()
        if not sel:
            return
        item = getattr(self.attention_tree, "_attention_items", {}).get(sel[0])
        if not item:
            return
        pid = item.get("purchase_id")
        if pid is not None:
            self.nb.select(self.tab_purchases)
            row_id = str(pid)
            if self.tree.exists(row_id):
                self.tree.selection_set(row_id)
                self.tree.focus(row_id)
                self.tree.see(row_id)
            existing = db.fetch_by_id(self.conn, int(pid))
            if existing is None:
                return
            def on_save(header, items):
                db.update_purchase(self.conn, int(pid), header, items)
                return int(pid)
            PurchaseDialog(self, on_save, existing=existing, conn=self.conn)
            return
        product = item.get("product")
        if product:
            self.nb.select(self.tab_stock)
            try:
                self.stock_notebook.select(self.stock_balances_tab)
            except Exception:
                pass
            for row_id in self.stock_summary_tree.get_children():
                vals = self.stock_summary_tree.item(row_id, "values")
                if vals and vals[0] == product:
                    self.stock_summary_tree.selection_set(row_id)
                    self.stock_summary_tree.focus(row_id)
                    self.stock_summary_tree.see(row_id)
                    break

    # ---- Вкладка "Итоги" ----
    def _build_summary_tab(self):
        top = ttk.Frame(self.tab_summary, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Год:").pack(side="left")
        self.summary_year_var = tk.StringVar(value=str(date.today().year))
        self.summary_year_combo = ttk.Combobox(top, textvariable=self.summary_year_var, width=8, state="readonly")
        self.summary_year_combo.pack(side="left", padx=(2, 10))
        self.summary_year_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_summary())

        ttk.Button(top, text="+ Прочий расход", command=self._add_monthly_expense).pack(side="left", padx=(4, 4))
        ttk.Button(top, text="Расходы месяца", command=self._manage_monthly_expenses).pack(side="left", padx=(4, 10))
        ttk.Button(top, text="Налоговый режим", command=self._edit_tax_profile).pack(side="left", padx=(4, 10))
        ttk.Label(
            top,
            text="Двойной щелчок по месяцу — подробная расшифровка",
            foreground=app_theme.MUTED,
        ).pack(side="left", padx=(10, 0))

        cols = ["month", "contracts", "contract_sum", "expenses", "monthly_expenses", "profit", "margin", "products", "qty_total"]
        labels = [
            "Месяц", "Контрактов", "Сумма контрактов", "Все расходы",
            "Прочие расходы", "Чистая прибыль", "Рентабельность",
            "Товары по контрактам", "Реализовано, шт."
        ]
        widths = [145, 85, 155, 145, 135, 145, 120, 360, 125]
        self.summary_tree = ttk.Treeview(self.tab_summary, columns=cols, show="headings", selectmode="browse")
        for key, label, width in zip(cols, labels, widths):
            self.summary_tree.heading(key, text=label, anchor="center")
            self.summary_tree.column(key, width=width, anchor="center")
        self.summary_tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.summary_tree.tag_configure(
            "total", background=app_theme.SOFT_BLUE,
            font=("TkDefaultFont", BASE_FONT_SIZE, "bold")
        )
        self._last_summary_period = None

        def remember_summary_period(_event=None):
            period = self._selected_summary_period(require_selection=False, allow_last=False)
            if period is not None:
                self._last_summary_period = period

        self.summary_tree.bind("<<TreeviewSelect>>", remember_summary_period, add="+")
        self.summary_tree.bind("<ButtonRelease-1>", remember_summary_period, add="+")
        self.summary_tree.bind("<Double-1>", lambda e: self._open_summary_month_contracts())

    def refresh_summary(self):
        for item in self.summary_tree.get_children():
            self.summary_tree.delete(item)
        year = None if self.summary_year_var.get() in ("Все", "") else int(self.summary_year_var.get())
        summary = db.monthly_summary(self.conn, year=year, month=None)
        total_sum = total_expenses = total_monthly_expenses = total_profit = total_qty = 0.0
        total_contracts = 0
        total_products = {}
        self.summary_tree._raw_tree_values = {}
        for row in summary:
            iid = f"month_{row['year']}_{row['month']:02d}"
            self.summary_tree._raw_tree_values[iid] = row
            month_label = f"{MONTHS_RU[row['month']]} {row['year']}"
            tree_insert_wrapped(
                self.summary_tree, "", "end", iid=iid,
                values=[
                    month_label,
                    row.get("contracts_count", 0),
                    fmt_money(row["contract_sum"]),
                    fmt_money(row.get("total_expenses", 0)),
                    fmt_money(row.get("monthly_expenses", 0)),
                    fmt_money(row["profit"]),
                    fmt_pct(row["margin_pct"]),
                    fmt_product_quantities(row.get("product_quantities", {})),
                    fmt_qty(row["qty_total"]),
                ],
            )
            total_contracts += int(row.get("contracts_count", 0))
            total_sum += row["contract_sum"]
            total_expenses += row.get("total_expenses", 0)
            total_monthly_expenses += row.get("monthly_expenses", 0)
            total_profit += row["profit"]
            total_qty += row["qty_total"]
            for product, qty in row.get("product_quantities", {}).items():
                total_products[product] = total_products.get(product, 0.0) + float(qty or 0)
        if len(summary) > 1:
            margin_total = total_profit / total_sum if total_sum else None
            tree_insert_wrapped(
                self.summary_tree, "", "end", iid="summary_total",
                values=[
                    "ИТОГО", total_contracts, fmt_money(total_sum), fmt_money(total_expenses),
                    fmt_money(total_monthly_expenses), fmt_money(total_profit),
                    fmt_pct(margin_total), fmt_product_quantities(total_products), fmt_qty(total_qty)
                ],
                tags=("total",),
            )

    def _selected_summary_period(self, require_selection=False, allow_last=True):
        """Возвращает период выбранной строки итогов.

        Для действий над месяцем запоминается последний реально выбранный месяц,
        чтобы Treeview не терял период при смене focus после нажатия кнопки.
        """
        candidates = list(self.summary_tree.selection())
        focused = self.summary_tree.focus()
        if focused and focused not in candidates:
            candidates.append(focused)
        for raw_iid in candidates:
            iid = str(raw_iid)
            if not iid.startswith("month_"):
                continue
            try:
                _, year_s, month_s = iid.split("_", 2)
                period = (int(year_s), int(month_s))
                self._last_summary_period = period
                return period
            except (ValueError, TypeError):
                continue

        if allow_last and getattr(self, "_last_summary_period", None):
            return self._last_summary_period

        if require_selection:
            return None

        try:
            year = int(self.summary_year_var.get())
        except (TypeError, ValueError):
            year = date.today().year
        month = date.today().month if year == date.today().year else 1
        return year, month


    def _select_summary_period(self, year, month):
        iid = f"month_{int(year)}_{int(month):02d}"
        if self.summary_tree.exists(iid):
            self.summary_tree.selection_set(iid)
            self.summary_tree.focus(iid)
            self.summary_tree.see(iid)
            self._last_summary_period = (int(year), int(month))
            return True
        return False

    def _add_monthly_expense(self):
        period = self._selected_summary_period(require_selection=True)
        if period is None:
            messagebox.showinfo(
                "Прочий расход",
                "Сначала выделите месяц в таблице «Итоги», затем нажмите «+ Прочий расход».",
                parent=self,
            )
            return

        year, month = period
        win = tk.Toplevel(self)
        win.title(f"Добавить прочий расход — {MONTHS_RU[month]} {year}")
        win.transient(self)
        win.geometry("520x210")
        win.minsize(520, 210)
        win.resizable(False, False)

        body = ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(
            body,
            text=f"Прочий расход за {MONTHS_RU[month]} {year}",
            font=("TkDefaultFont", BASE_FONT_SIZE, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))

        ttk.Label(body, text="Сумма, руб.:").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=6)
        amount_var = tk.StringVar()
        amount_entry = ttk.Entry(body, textvariable=amount_var, width=30)
        amount_entry.grid(row=1, column=1, sticky="ew", pady=6)
        self._bind_context_menu(amount_entry)

        ttk.Label(
            body,
            text="Расход будет отнесён к выделенному месяцу. Дата не требуется.",
            foreground=app_theme.MUTED,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 10))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(6, 0))

        def save():
            try:
                data = prepare_selected_month_expense(amount_var.get(), year, month)
                db.insert_monthly_expense(self.conn, data)
            except ValueError as exc:
                messagebox.showerror("Ошибка ввода", str(exc), parent=win)
                amount_entry.focus_set()
                return
            except Exception as exc:
                _log(f"Итоги: не удалось сохранить прочий расход: {exc}")
                messagebox.showerror(
                    "Ошибка сохранения",
                    f"Не удалось добавить прочий расход:\n{exc}",
                    parent=win,
                )
                return

            win.destroy()
            self.refresh_summary()
            self._select_summary_period(year, month)
            messagebox.showinfo(
                "Прочий расход",
                f"Расход {fmt_money(data['amount'])} добавлен в {MONTHS_RU[month]} {year}.\n"
                "Чистая прибыль пересчитана.",
                parent=self,
            )

        ttk.Button(buttons, text="Сохранить", command=save, style="Primary.TButton").pack(side="left", padx=4)
        ttk.Button(buttons, text="Отмена", command=win.destroy).pack(side="left", padx=4)

        try:
            win.update_idletasks()
            screen_w, screen_h = win.winfo_screenwidth(), win.winfo_screenheight()
            width, height = 520, 210
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2)
            win.geometry(f"{width}x{height}+{x}+{y}")
            win.lift()
            win.focus_force()
            win.grab_set()
        except tk.TclError:
            pass
        amount_entry.focus_set()


    def _edit_tax_profile(self):
        year, month = self._selected_summary_period()
        profile = db.get_tax_profile(self.conn, year, month)
        win = tk.Toplevel(self)
        win.title(f"Налоговый режим — {MONTHS_RU[month]} {year}")
        win.transient(self)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=14)
        body.pack(fill="both", expand=True)

        regime_var = tk.StringVar(value=profile.get("regime") or "С доходов")
        rate_var = tk.StringVar(value=str(profile.get("rate", 7.0)).replace(".", ","))

        ttk.Label(body, text="Режим:").grid(row=0, column=0, sticky="w", padx=(0,10), pady=5)
        regime = ttk.Combobox(body, textvariable=regime_var, state="readonly", width=28,
                              values=["С доходов", "Доходы минус расходы"])
        regime.grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(body, text="Ставка, %:").grid(row=1, column=0, sticky="w", padx=(0,10), pady=5)
        rate_entry = ttk.Entry(body, textvariable=rate_var, width=18)
        rate_entry.grid(row=1, column=1, sticky="w", pady=5)
        self._bind_context_menu(rate_entry)

        ttk.Label(
            body,
            text=("Настройка действует с первого числа выбранного месяца и далее, "
                  "пока не будет задан новый режим для более позднего периода."),
            wraplength=430, justify="left", foreground=app_theme.MUTED
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8,10))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e")
        def save():
            try:
                rate = parse_money(rate_var.get())
                db.set_tax_profile(self.conn, f"{year:04d}-{month:02d}-01", regime_var.get(), rate)
            except ValueError as exc:
                messagebox.showerror("Ошибка ввода", str(exc), parent=win)
                return
            win.destroy()
            self.refresh_summary()
        ttk.Button(buttons, text="Сохранить", command=save, style="Primary.TButton").pack(side="left", padx=4)
        ttk.Button(buttons, text="Отмена", command=win.destroy).pack(side="left", padx=4)

    def _manage_monthly_expenses(self):
        period = self._selected_summary_period(require_selection=True)
        if period is None:
            messagebox.showinfo(
                "Расходы месяца",
                "Сначала выделите нужный месяц в таблице «Итоги».",
                parent=self,
            )
            return
        year, month = period
        rows = list(db.fetch_monthly_expenses(self.conn, year=year, month=month))
        if not rows:
            messagebox.showinfo(
                "Расходы месяца",
                f"За {MONTHS_RU[month]} {year} прочих расходов не внесено.",
                parent=self,
            )
            return

        win = tk.Toplevel(self)
        win.title(f"Все прочие расходы — {MONTHS_RU[month]} {year}")
        win.geometry("920x500")
        win.transient(self)

        header = ttk.Frame(win, padding=(10, 10, 10, 0))
        header.pack(fill="x")
        ttk.Label(
            header,
            text=f"{MONTHS_RU[month]} {year} · все внесённые прочие расходы",
            font=("TkDefaultFont", BASE_FONT_SIZE, "bold"),
        ).pack(side="left")

        cols = ("date", "category", "amount", "description")
        labels = ("Дата расхода", "Категория", "Сумма, руб.", "Описание")
        widths = (130, 200, 170, 390)
        tree = ttk.Treeview(win, columns=cols, show="headings", selectmode="browse")
        for key, label, width in zip(cols, labels, widths):
            tree.heading(key, text=label, anchor="center")
            tree.column(key, width=width, anchor="e" if key == "amount" else ("w" if key == "description" else "center"))
        tree.pack(fill="both", expand=True, padx=10, pady=10)

        total_var = tk.StringVar()
        footer = ttk.Frame(win, padding=(10, 0, 10, 10))
        footer.pack(fill="x")
        ttk.Label(
            footer,
            textvariable=total_var,
            font=("TkDefaultFont", BASE_FONT_SIZE, "bold"),
        ).pack(side="left")

        def reload_rows():
            for iid in tree.get_children():
                tree.delete(iid)
            current_rows = list(db.fetch_monthly_expenses(self.conn, year=year, month=month))
            total = 0.0
            for r in current_rows:
                amount = float(r["amount"] or 0)
                total += amount
                tree_insert_wrapped(
                    tree, "", "end", iid=str(r["id"]),
                    values=(
                        fmt_date(r["expense_date"]),
                        r["category"] or "Прочее",
                        fmt_money(amount),
                        r["description"] or "—",
                    ),
                )
            total_var.set(f"Итого за {MONTHS_RU[month]}: {fmt_money(total)}")
            if not current_rows:
                win.destroy()
                self.refresh_summary()
                self._select_summary_period(year, month)
                messagebox.showinfo(
                    "Расходы месяца",
                    f"За {MONTHS_RU[month]} {year} прочих расходов больше нет.",
                    parent=self,
                )

        def remove_selected():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Удаление", "Выберите расход в таблице.", parent=win)
                return
            if not messagebox.askyesno("Удаление", "Удалить выбранный расход?", parent=win):
                return
            db.delete_monthly_expense(self.conn, int(sel[0]))
            self.refresh_summary()
            self._select_summary_period(year, month)
            reload_rows()

        ttk.Button(
            footer, text="Удалить выбранный", command=remove_selected,
            style="Danger.TButton"
        ).pack(side="right", padx=4)
        ttk.Button(footer, text="Закрыть", command=win.destroy).pack(side="right", padx=4)
        reload_rows()

    def _open_summary_month_contracts(self):
        """Подробная расшифровка финансовых итогов выбранного месяца."""
        sel = self.summary_tree.selection()
        if not sel:
            return
        iid = str(sel[0])
        if not iid.startswith("month_"):
            return
        try:
            _, year_s, month_s = iid.split("_", 2)
            year = int(year_s)
            month = int(month_s)
        except (ValueError, TypeError):
            return

        rows = db.summary_contracts(self.conn, year=year, month=month)
        summary_rows = db.monthly_summary(self.conn, year=year, month=month)
        sm = summary_rows[0] if summary_rows else {
            "contract_sum": 0, "purchase_cost": 0, "logistics": 0, "commission": 0,
            "other_costs": 0, "guarantee": 0, "tax": 0, "tax_base": 0,
            "tax_regime": "С доходов", "tax_rate": 7.0, "monthly_expenses": 0,
            "total_expenses": 0, "profit": 0,
        }

        win = tk.Toplevel(self)
        win.title(f"Итоги — {MONTHS_RU[month]} {year}")
        win.geometry("1480x760")
        win.minsize(1100, 600)

        header = ttk.Frame(win, padding=(10, 10, 10, 4))
        header.pack(fill="x")
        ttk.Label(
            header, text=f"{MONTHS_RU[month]} {year}",
            font=("TkDefaultFont", BASE_FONT_SIZE, "bold")
        ).pack(side="left")
        ttk.Label(
            header,
            text=("Обычные контракты — по месяцу заведения; отложенная закупка — "
                  "только после исполнения, по месяцу даты вручения."),
            foreground=app_theme.MUTED,
        ).pack(side="left", padx=(14, 0))

        breakdown = ttk.LabelFrame(win, text="Из чего сложился результат месяца", padding=8)
        breakdown.pack(fill="x", padx=10, pady=(4, 8))
        parts = [
            ("Сумма контрактов", sm.get("contract_sum", 0)),
            ("Себестоимость", sm.get("purchase_cost", 0)),
            ("Логистика", sm.get("logistics", 0)),
            ("Комиссии площадок", sm.get("commission", 0)),
            ("Другие расходы в контрактах", sm.get("other_costs", 0)),
            ("Обеспечение / гарантии", sm.get("guarantee", 0)),
            (f"Налог · {sm.get('tax_regime', 'С доходов')} · {sm.get('tax_rate', 7):g}%", sm.get("tax", 0)),
            ("Прочие расходы месяца", sm.get("monthly_expenses", 0)),
            ("ВСЕ РАСХОДЫ", sm.get("total_expenses", 0)),
            ("ЧИСТАЯ ПРИБЫЛЬ", sm.get("profit", 0)),
        ]
        for idx, (label, value) in enumerate(parts):
            col = idx % 5
            row = (idx // 5) * 2
            ttk.Label(breakdown, text=label, foreground=app_theme.MUTED).grid(row=row, column=col, padx=10, sticky="w")
            ttk.Label(
                breakdown, text=fmt_money(value),
                font=("TkDefaultFont", BASE_FONT_SIZE, "bold")
            ).grid(row=row + 1, column=col, padx=10, pady=(0, 5), sticky="w")

        product_breakdown = ttk.LabelFrame(win, text="Товары по контрактам месяца", padding=8)
        product_breakdown.pack(fill="x", padx=10, pady=(0, 8))
        quantities = sm.get("product_quantities", {}) or {}
        if quantities:
            for idx, (product, qty) in enumerate(sorted(quantities.items(), key=lambda kv: str(kv[0]).casefold())):
                ttk.Label(
                    product_breakdown,
                    text=f"{product} — {fmt_qty(qty)} шт.",
                    font=("TkDefaultFont", BASE_FONT_SIZE, "bold"),
                ).grid(row=idx // 3, column=idx % 3, sticky="w", padx=12, pady=3)
        else:
            ttk.Label(product_breakdown, text="Товаров по контрактам этого месяца нет.", foreground=app_theme.MUTED).pack(anchor="w")

        cols = ("num", "period", "customer", "products", "sum", "exec", "payment")
        labels = ("№ контракта", "Период учёта", "Заказчик", "Товары / Кол-во",
                  "Сумма контракта", "Исполнение", "Оплата")
        widths = (140, 135, 360, 330, 150, 135, 135)
        tree = ttk.Treeview(win, columns=cols, show="headings", selectmode="browse")
        for key, label, width in zip(cols, labels, widths):
            tree.heading(key, text=label, anchor="center")
            tree.column(key, width=width, anchor="center")
        tree.pack(fill="both", expand=True, padx=10, pady=6)

        for r in rows:
            items_text = "\n".join(
                f"{it['product'] or '—'} — {fmt_qty(it['qty'])} шт."
                for it in r.get("items", [])
            ) or "—"
            period_note = (
                "Исполнение " + fmt_date(r.get("summary_period"))
                if r.get("deferred_purchase")
                else "Заведение " + fmt_date(r.get("summary_period"))
            )
            values = (
                r.get("contract_no") or "—", period_note, r.get("customer") or "—",
                items_text, fmt_money(r.get("contract_sum") or 0.0),
                r.get("exec_status") or "—", r.get("payment_status") or "—"
            )
            tree_insert_wrapped(tree, "", "end", iid=str(r["id"]), values=values)

        footer = ttk.Frame(win, padding=(10, 4, 10, 10))
        footer.pack(fill="x")
        ttk.Label(
            footer,
            text=f"Контрактов в итогах: {len(rows)} | Чистая прибыль: {fmt_money(sm.get('profit', 0))}",
            font=("TkDefaultFont", BASE_FONT_SIZE, "bold")
        ).pack(side="left")
        ttk.Button(footer, text="Закрыть", command=win.destroy).pack(side="right")

        def open_selected_contract(_event=None):
            selected = tree.selection()
            if not selected:
                return
            try:
                pid = int(selected[0])
            except (TypeError, ValueError):
                return
            existing = db.fetch_by_id(self.conn, pid)
            if not existing:
                return
            def on_save(header_data, item_data):
                db.update_purchase(self.conn, pid, header_data, item_data)
                self.refresh_all()
                return pid
            PurchaseDialog(self, on_save, existing=existing, conn=self.conn)

        tree.bind("<Double-1>", open_selected_contract)

    # ---- Вкладка "Ближайшие дедлайны" ----
    def _build_deadlines_tab(self):
        cols = ["type", "customer", "product", "contract_no", "deadline", "days", "state"]
        labels = ["Событие", "Заказчик", "Товар", "№ контракта", "Дата", "Осталось дней", "Состояние"]
        self.deadlines_tree = ttk.Treeview(self.tab_deadlines, columns=cols, show="headings")
        for key, label in zip(cols, labels):
            self.deadlines_tree.heading(key, text=label, anchor="center")
            self.deadlines_tree.column(key, width=180 if key in ("customer", "product") else 130, anchor="center")
        self.deadlines_tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.deadlines_tree.bind("<Double-1>", lambda e: self._open_deadline_contract())
        self.deadlines_tree.tag_configure("overdue", background=app_theme.SOFT_RED, foreground=app_theme.RED)
        self.deadlines_tree.tag_configure("critical", background=app_theme.SOFT_RED, foreground=app_theme.RED)
        self.deadlines_tree.tag_configure("near", background=app_theme.SOFT_YELLOW, foreground=app_theme.AMBER)

    def _open_deadline_contract(self):
        sel = self.deadlines_tree.selection()
        if not sel:
            return
        try:
            pid = int(str(sel[0]).split("_")[0])
            self.tree.selection_set(str(pid))
            self._edit_purchase()
        except Exception:
            return

    def _pending_signing_reminders(self, rows=None):
        """Подписание: 3/2/1 день, день срока и просрочка; дальние даты не показываем."""
        result = []
        for r in (rows if rows is not None else db.deadline_rows(self.conn)):
            d = parse_date_iso_to_date(r["sign_deadline"])
            state, days = signing_reminder_state(d, r["contract_status"])
            if state in ("overdue", "red"):
                result.append((r, "Подписание", state, days, d))
        return result

    def _pending_reminders(self, rows=None):
        result = []
        for r in (rows if rows is not None else db.deadline_rows(self.conn)):
            # Завершённый контракт не должен оставаться в контрольных сроках,
            # даже если пользователь отметил «Исполнено», но не заполнил дату вручения.
            if (r.get("exec_status") if hasattr(r, "get") else r["exec_status"]) == "Исполнено":
                continue
            d = parse_date_iso_to_date(r["deadline"])
            state, days = reminder_state(d, r["handover_date"])
            if state in ("red", "yellow"):
                # reminder_state исторически возвращает red и для уже прошедшей
                # даты. Для вкладки дедлайнов нормализуем это в отдельное
                # состояние overdue, чтобы просрочка не отображалась как
                # просто «Подходит срок».
                normalized_state = "overdue" if days is not None and days < 0 else state
                result.append((r, "Исполнение", normalized_state, days, d))
        return result

    def _pending_payment_reminders(self, rows=None):
        result = []
        for r in (rows if rows is not None else db.deadline_rows(self.conn)):
            d = parse_date_iso_to_date(r["payment_deadline"])
            state, days = payment_reminder_state(d, r["payment_status"])
            if state in ("red", "yellow", "overdue"):
                result.append((r, "Оплата", state, days, d))
        return result

    def refresh_deadlines(self):
        for item in self.deadlines_tree.get_children():
            self.deadlines_tree.delete(item)
        deadline_rows = db.deadline_rows(self.conn)
        events = (self._pending_signing_reminders(deadline_rows) +
                  self._pending_reminders(deadline_rows) +
                  self._pending_payment_reminders(deadline_rows))
        events.sort(key=lambda x: (x[4] is None, x[4] or date.max))
        for r, event_type, state, days, d in events:
            if state == "overdue":
                label = "Срок истёк"
                tag = "overdue"
            elif state == "red":
                label = "Подходит срок"
                tag = "critical"
            elif state == "yellow":
                label = "Подходит срок"
                tag = "near"
            else:
                label = "Требуется подписание" if event_type == "Подписание" else "Контроль"
                tag = ""
            if event_type == "Оплата":
                event_date = r["payment_deadline"]
            elif event_type == "Подписание":
                event_date = r["sign_deadline"]
            else:
                event_date = r["deadline"]
            values = [event_type, r["customer"] or "", r["product"] or "",
                      r["contract_no"] or "", fmt_date(event_date),
                      str(days) if days is not None else "", label]
            tree_insert_wrapped(self.deadlines_tree, "", "end", iid=f"{r['id']}_{event_type}", values=values, tags=(tag,) if tag else ())

    # ---- Вкладка "Склад" ----
    def _build_stock_tab(self):
        # Внутренние вкладки уменьшают вертикальную перегрузку склада.
        self.stock_notebook = ttk.Notebook(self.tab_stock)
        self.stock_notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.stock_balances_tab = ttk.Frame(self.stock_notebook)
        self.stock_receipts_tab = ttk.Frame(self.stock_notebook)
        self.stock_reservations_tab = ttk.Frame(self.stock_notebook)

        self.stock_notebook.add(self.stock_balances_tab, text="Остатки")
        self.stock_notebook.add(self.stock_receipts_tab, text="Приходы")
        self.stock_notebook.add(self.stock_reservations_tab, text="Резервы")

        # --- Остатки ---
        balances_header = ttk.Frame(self.stock_balances_tab, padding=(8, 8, 8, 4))
        balances_header.pack(fill="x")
        ttk.Label(
            balances_header,
            text="Остатки по товарам",
            font=("TkDefaultFont", BASE_FONT_SIZE, "bold"),
        ).pack(side="left")
        self.stock_movement_button = ttk.Button(
            balances_header,
            text="Движение товара",
            command=self._show_stock_movement,
            state="disabled",
        )
        self.stock_movement_button.pack(side="left", padx=(12, 0))
        self.stock_total_var = tk.StringVar(value="Итого по складу: 0 ₽")
        ttk.Label(
            balances_header,
            textvariable=self.stock_total_var,
            font=("TkDefaultFont", BASE_FONT_SIZE, "bold"),
            foreground=app_theme.ACCENT,
        ).pack(side="right")

        ttk.Label(
            self.stock_balances_tab,
            padding=(8, 0, 8, 8),
            wraplength=1300,
            justify="left",
            text=("«Резерв» = товар в незавершённых контрактах + ручной резерв "
                  "под потенциальных клиентов."),
            foreground=app_theme.MUTED,
        ).pack(fill="x")

        sum_cols = ["product", "on_hand", "reserved", "available", "future", "need_buy", "warning", "value"]
        sum_labels = ["Товар", "Всего, шт.", "Резерв, шт.", "Доступно, шт.", "Будущая потребность", "Нужно закупить, шт.", "Внимание", "Стоимость остатка"]
        self.stock_summary_tree = ttk.Treeview(
            self.stock_balances_tab,
            columns=sum_cols,
            show="headings",
        )
        for key, label in zip(sum_cols, sum_labels):
            self.stock_summary_tree.heading(key, text=label, anchor="center")
            self.stock_summary_tree.column(
                key,
                width=260 if key == "product" else 160,
                anchor="center",
            )
        self.stock_summary_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.stock_summary_tree.bind("<Double-1>", lambda e: self._show_stock_movement())
        self.stock_summary_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_stock_movement_button())
        self.stock_summary_tree.tag_configure("negative", background=app_theme.SOFT_RED, foreground=app_theme.RED)
        self.stock_summary_tree.tag_configure("positive", background=app_theme.SOFT_GREEN, foreground=app_theme.GREEN)
        self.stock_summary_tree.tag_configure("zero", background=app_theme.WASH, foreground=app_theme.MUTED)
        self.stock_summary_tree.tag_configure("low", background=app_theme.SOFT_YELLOW, foreground=app_theme.AMBER)

        # --- Приходы ---
        receipts_top = ttk.Frame(self.stock_receipts_tab, padding=8)
        receipts_top.pack(fill="x")
        ttk.Button(receipts_top, text="+ Добавить приход", command=self._add_receipt,
                   style="Primary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(receipts_top, text="Редактировать", command=self._edit_receipt).pack(side="left", padx=4)
        ttk.Button(receipts_top, text="Удалить", command=self._delete_receipt,
                   style="Danger.TButton").pack(side="left", padx=4)

        log_cols = ["receipt_date", "product", "qty", "unit_cost", "supplier", "line_total"]
        log_labels = ["Дата", "Товар", "Кол-во", "Цена за ед.", "Поставщик", "Сумма позиции"]
        self.stock_log_tree = ttk.Treeview(
            self.stock_receipts_tab,
            columns=log_cols,
            show="headings",
        )
        for key, label in zip(log_cols, log_labels):
            self.stock_log_tree.heading(key, text=label, anchor="center")
            self.stock_log_tree.column(key, width=180, anchor="center")
        self.stock_log_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.stock_log_tree.bind("<Double-1>", lambda e: self._edit_receipt())

        # --- Резервы ---
        reservations_top = ttk.Frame(self.stock_reservations_tab, padding=8)
        reservations_top.pack(fill="x")
        ttk.Button(reservations_top, text="+ Добавить резерв", command=self._add_reservation,
                   style="Primary.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(reservations_top, text="Редактировать", command=self._edit_reservation).pack(side="left", padx=4)
        ttk.Button(reservations_top, text="Удалить", command=self._delete_reservation,
                   style="Danger.TButton").pack(side="left", padx=4)

        ttk.Label(
            self.stock_reservations_tab,
            text=("Ручной резерв под потенциальных клиентов без привязки к контракту."),
            padding=(8, 0, 8, 8),
            foreground=app_theme.MUTED,
        ).pack(fill="x")

        res_cols = ["reserved_date", "product", "qty", "organization", "note"]
        res_labels = ["Дата", "Товар", "Кол-во", "Организация", "Примечание"]
        self.reservation_tree = ttk.Treeview(
            self.stock_reservations_tab,
            columns=res_cols,
            show="headings",
        )
        for key, label in zip(res_cols, res_labels):
            self.reservation_tree.heading(key, text=label, anchor="center")
            self.reservation_tree.column(
                key,
                width=230 if key in ("organization", "note") else 170,
                anchor="center",
            )
        self.reservation_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.reservation_tree.bind("<Double-1>", lambda e: self._edit_reservation())

    def refresh_stock(self):
        for item in self.stock_summary_tree.get_children():
            self.stock_summary_tree.delete(item)
        for row in db.stock_summary(self.conn):
            available = row["available"]
            if available <= 0:
                tag, warning = "negative", "Нет в наличии"
            elif available < 50:
                tag, warning = "low", "Осталось < 50 шт."
            else:
                tag, warning = "positive", "—"
            need_to_buy = float(row.get("need_to_buy", 0) or 0)
            if need_to_buy > 0:
                warning = f"Закупить {fmt_qty(need_to_buy)} шт."
                tag = "negative" if available <= 0 else "low"
            values = [row["product"] or "—", fmt_qty(row["on_hand"]), fmt_qty(row["reserved"]), fmt_qty(available),
                      fmt_qty(row.get("future_demand", 0)), fmt_qty(need_to_buy), warning,
                      fmt_money(self._stock_product_value(row["product"]))]
            tree_insert_wrapped(self.stock_summary_tree, "", "end", values=values, tags=(tag,))

        for item in self.stock_log_tree.get_children():
            self.stock_log_tree.delete(item)
        for r in db.fetch_receipts(self.conn):
            qty = r["qty"] or 0.0
            price = r["unit_cost"] or 0.0
            values = [fmt_date(r["receipt_date"]), r["product"] or "—", fmt_qty(qty),
                      fmt_money(price) if r["unit_cost"] else "", r["supplier"] or "—",
                      fmt_money(qty * price)]
            tree_insert_wrapped(self.stock_log_tree, "", "end", iid=str(r["id"]), values=values)

        self.stock_total_var.set(f"Итого по складу: {fmt_money(db.stock_total_value(self.conn))}")
        self._update_stock_movement_button()

        for item in self.reservation_tree.get_children():
            self.reservation_tree.delete(item)
        for r in db.fetch_manual_reservations(self.conn):
            values = [fmt_date(r["reserved_date"]), r["product"] or "—", fmt_qty(r["qty"]),
                      r["organization"] or "—", r["note"] or "—"]
            tree_insert_wrapped(self.reservation_tree, "", "end", iid=str(r["id"]), values=values)

    def _update_stock_movement_button(self):
        if not hasattr(self, "stock_movement_button"):
            return
        state = "normal" if self.stock_summary_tree.selection() else "disabled"
        self.stock_movement_button.configure(state=state)

    def _stock_product_value(self, product):
        # Стоимость текущего физического остатка по средневзвешенной себестоимости.
        rows = [r for r in db.fetch_receipts(self.conn) if r["product"] == product]
        qty = sum((r["qty"] or 0.0) for r in rows)
        value = sum((r["qty"] or 0.0) * (r["unit_cost"] or 0.0) for r in rows)
        shipped = 0.0
        for r in db.fetch_all(self.conn):
            if int(r.get("stock_written_off") or 0):
                for it in r["items"]:
                    if it["product"] == product:
                        shipped += float(it["stock_qty"] or 0.0)
        remaining = max(0.0, qty - shipped)
        return remaining * (value / qty) if qty else 0.0

    def _show_stock_movement(self):
        sel = self.stock_summary_tree.selection()
        if not sel:
            return
        product = self.stock_summary_tree.item(sel[0], "values")[0]
        events = db.stock_product_movement(self.conn, product)
        history = db.fetch_stock_audit(self.conn, product)

        win = tk.Toplevel(self)
        win.title(f"Движение и история товара: {product}")
        win.geometry("1050x620")
        ttk.Label(
            win, text=product,
            font=("TkDefaultFont", BASE_FONT_SIZE, "bold"), padding=8
        ).pack(anchor="w")

        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        current_tab = ttk.Frame(notebook)
        history_tab = ttk.Frame(notebook)
        notebook.add(current_tab, text="Текущее движение")
        notebook.add(history_tab, text="История событий")

        cols = ("date", "type", "qty", "balance", "counterparty", "details")
        labels = ("Дата", "Операция", "Кол-во", "Баланс", "Контрагент", "Основание")
        tree = ttk.Treeview(current_tab, columns=cols, show="headings")
        for key, label in zip(cols, labels):
            tree.heading(key, text=label, anchor="center")
            tree.column(key, width=140 if key not in ("details", "counterparty") else 220, anchor="center")
        tree.pack(fill="both", expand=True, padx=6, pady=6)
        for e in events:
            tree_insert_wrapped(
                tree, "", "end",
                values=(
                    fmt_date(e["date"]), e["type"], fmt_qty(e["qty"]),
                    fmt_qty(e["balance"]), e["counterparty"], e["details"]
                ),
            )

        hcols = ("datetime", "action", "qty", "contract", "counterparty", "details")
        hlabels = ("Дата и время", "Событие", "Кол-во", "Контракт", "Контрагент", "Подробности")
        htree = ttk.Treeview(history_tab, columns=hcols, show="headings")
        widths = (165, 190, 100, 100, 210, 320)
        for key, label, width in zip(hcols, hlabels, widths):
            htree.heading(key, text=label, anchor="center")
            htree.column(key, width=width, anchor="center" if key != "details" else "w")
        htree.pack(fill="both", expand=True, padx=6, pady=6)
        for r in history:
            dt = str(r["event_at"] or "").replace("T", " ")
            qty = "—" if r["qty"] is None else fmt_qty(r["qty"])
            contract = f"#{r['purchase_id']}" if r["purchase_id"] else "—"
            tree_insert_wrapped(
                htree, "", "end",
                values=(
                    dt, r["action"] or "—", qty, contract,
                    r["counterparty"] or "—", r["details"] or "—"
                ),
            )

        ttk.Button(win, text="Закрыть", command=win.destroy).pack(pady=(0, 8))

    def _selected_receipt_id(self):
        sel = self.stock_log_tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def _add_receipt(self):
        products = db.distinct_products(self.conn)

        def on_save(data, _id):
            db.insert_receipt(self.conn, data)
            self.refresh_stock()
        ReceiptDialog(self, on_save, products)

    def _edit_receipt(self):
        rid = self._selected_receipt_id()
        if rid is None:
            messagebox.showinfo("Выбор прихода", "Сначала выберите строку в журнале прихода.")
            return
        existing = db.fetch_receipt_by_id(self.conn, rid)
        products = db.distinct_products(self.conn)

        def on_save(data, _id):
            db.update_receipt(self.conn, rid, data)
            self.refresh_stock()
        ReceiptDialog(self, on_save, products, existing=existing)

    def _delete_receipt(self):
        rid = self._selected_receipt_id()
        if rid is None:
            messagebox.showinfo("Выбор прихода", "Сначала выберите строку в журнале прихода.")
            return
        if messagebox.askyesno("Удаление", "Удалить запись о приходе товара?"):
            db.delete_receipt(self.conn, rid)
            self.refresh_stock()

    def _selected_reservation_id(self):
        sel = self.reservation_tree.selection()
        return int(sel[0]) if sel else None

    def _add_reservation(self):
        products = db.distinct_products(self.conn)

        def on_save(data, _id):
            db.insert_manual_reservation(self.conn, data)
            self.refresh_stock()
        ReservationDialog(self, on_save, products)

    def _edit_reservation(self):
        rid = self._selected_reservation_id()
        if rid is None:
            messagebox.showinfo("Выбор резерва", "Сначала выберите строку в списке резерва.")
            return
        existing = db.fetch_manual_reservation_by_id(self.conn, rid)
        products = db.distinct_products(self.conn)

        def on_save(data, _id):
            db.update_manual_reservation(self.conn, rid, data)
            self.refresh_stock()
        ReservationDialog(self, on_save, products, existing=existing)

    def _delete_reservation(self):
        rid = self._selected_reservation_id()
        if rid is None:
            messagebox.showinfo("Выбор резерва", "Сначала выберите строку в списке резерва.")
            return
        if messagebox.askyesno("Удаление", "Удалить резерв товара?"):
            db.delete_manual_reservation(self.conn, rid)
            self.refresh_stock()

    # ---- Вкладка "Анализ конкурентов" ----
    def _build_competitors_tab(self):
        top = ttk.Frame(self.tab_competitors, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Добавить запись", command=self._add_competitor).pack(side="left", padx=4)
        ttk.Button(top, text="Редактировать", command=self._edit_competitor).pack(side="left", padx=4)
        ttk.Button(top, text="Удалить", command=self._delete_competitor).pack(side="left", padx=4)

        ttk.Label(self.tab_competitors, text="Записи о закупках конкурентов", padding=(8, 8, 8, 0),
                  font=("TkDefaultFont", BASE_FONT_SIZE, "bold")).pack(fill="x")
        log_cols = ["competitor", "competitor_inn", "product", "trade_type", "qty", "unit_price", "purchase_date"]
        log_labels = ["Конкурент", "ИНН", "Товар", "Вид торгов", "Кол-во", "Цена за ед.", "Дата закупки"]
        self.competitor_log_tree = ttk.Treeview(self.tab_competitors, columns=log_cols,
                                                 show="headings", height=6)
        for key, label in zip(log_cols, log_labels):
            self.competitor_log_tree.heading(key, text=label, anchor="center")
            self.competitor_log_tree.column(key, width=150, anchor="center")
        self.competitor_log_tree.pack(fill="x", padx=8, pady=(0, 8))
        self.competitor_log_tree.bind("<Double-1>", lambda e: self._edit_competitor())

        ttk.Label(self.tab_competitors, text="Анализ цен по товару", padding=(8, 8, 8, 0),
                  font=("TkDefaultFont", BASE_FONT_SIZE, "bold")).pack(fill="x")
        p_cols = ["product", "min_price", "max_price", "avg_price", "median_price", "count", "change_pct"]
        p_labels = ["Товар", "Мин. цена", "Макс. цена", "Средняя цена", "Медиана", "Наблюдений", "Изменение"]
        self.competitor_product_tree = ttk.Treeview(self.tab_competitors, columns=p_cols,
                                                      show="headings", height=5)
        for key, label in zip(p_cols, p_labels):
            self.competitor_product_tree.heading(key, text=label, anchor="center")
            self.competitor_product_tree.column(key, width=150, anchor="center")
        self.competitor_product_tree.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Label(self.tab_competitors, text="Анализ по конкурентам", padding=(8, 8, 8, 0),
                  font=("TkDefaultFont", BASE_FONT_SIZE, "bold")).pack(fill="x")
        c_cols = ["competitor", "competitor_inn", "wins", "avg_price", "min_price", "max_price", "relative_pct"]
        c_labels = ["Конкурент", "ИНН", "Кол-во побед", "Средняя цена", "Мин. цена", "Макс. цена",
                    "Цена отн. рынка"]
        self.competitor_stats_tree = ttk.Treeview(self.tab_competitors, columns=c_cols,
                                                    show="headings", height=5)
        for key, label in zip(c_cols, c_labels):
            self.competitor_stats_tree.heading(key, text=label, anchor="center")
            self.competitor_stats_tree.column(key, width=150, anchor="center")
        self.competitor_stats_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _selected_competitor_id(self):
        sel = self.competitor_log_tree.selection()
        return int(sel[0]) if sel else None

    def refresh_competitors(self):
        for item in self.competitor_log_tree.get_children():
            self.competitor_log_tree.delete(item)
        for r in db.fetch_competitor_records(self.conn):
            values = [r["competitor"] or "", r["competitor_inn"] or "", r["product"] or "", r["trade_type"] or "",
                      fmt_qty(r["qty"]), fmt_money(r["unit_price"]), fmt_date(r["purchase_date"])]
            tree_insert_wrapped(self.competitor_log_tree, "", "end", iid=str(r["id"]), values=values)

        for item in self.competitor_product_tree.get_children():
            self.competitor_product_tree.delete(item)
        for p in db.competitor_product_stats(self.conn):
            change = fmt_pct(p["change_pct"]) if p["change_pct"] is not None else "—"
            values = [p["product"], fmt_money(p["min_price"]), fmt_money(p["max_price"]),
                      fmt_money(p["avg_price"]), fmt_money(p["median_price"]), p["count"], change]
            tree_insert_wrapped(self.competitor_product_tree, "", "end", values=values)

        for item in self.competitor_stats_tree.get_children():
            self.competitor_stats_tree.delete(item)
        for c in db.competitor_stats(self.conn):
            rel = fmt_pct(c["relative_pct"]) if c["relative_pct"] is not None else "—"
            values = [c["competitor"], c.get("competitor_inn") or "—", c["wins"], fmt_money(c["avg_price"]),
                      fmt_money(c["min_price"]), fmt_money(c["max_price"]), rel]
            tree_insert_wrapped(self.competitor_stats_tree, "", "end", values=values)

    def _add_competitor(self):
        def on_save(data, _id):
            db.insert_competitor_record(self.conn, data)
            self.refresh_competitors()
        CompetitorDialog(self, on_save)

    def _edit_competitor(self):
        rid = self._selected_competitor_id()
        if rid is None:
            messagebox.showinfo("Выбор записи", "Сначала выберите строку в таблице.")
            return
        existing = db.fetch_competitor_record_by_id(self.conn, rid)

        def on_save(data, _id):
            db.update_competitor_record(self.conn, rid, data)
            self.refresh_competitors()
        CompetitorDialog(self, on_save, existing=existing)

    def _delete_competitor(self):
        rid = self._selected_competitor_id()
        if rid is None:
            messagebox.showinfo("Выбор записи", "Сначала выберите строку в таблице.")
            return
        if messagebox.askyesno("Удаление", "Удалить запись?"):
            db.delete_competitor_record(self.conn, rid)
            self.refresh_competitors()

    # ---- Вкладка "Калькулятор цены" ----
    def _build_calculator_tab(self):
        note = ttk.Label(self.tab_calculator, padding=8, wraplength=1300, justify="left",
                          text=("Рабочий расчёт цены ДО заключения контракта: по себестоимости, "
                                "расходам на партию (логистика, доп. расходы, комиссия площадки), "
                                "желаемой наценке и налогу считает цену за штуку и итоговую сумму. "
                                "Расчёт сохраняется автоматически и остаётся между запусками "
                                "программы — как и остальные данные."))
        note.pack(fill="x")

        top = ttk.Frame(self.tab_calculator, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="+ Добавить позицию", command=self._add_calc_row).pack(side="left", padx=4)
        ttk.Button(top, text="Редактировать", command=self._edit_calc_row).pack(side="left", padx=4)
        ttk.Button(top, text="Удалить", command=self._delete_calc_row).pack(side="left", padx=4)
        ttk.Button(top, text="Экспорт в CSV", command=self._export_calc_csv).pack(side="left", padx=4)
        ttk.Button(top, text="Очистить всё", command=self._clear_calc_rows).pack(side="left", padx=4)

        cols = ["num", "name", "qty", "cost", "logistics", "extra", "commission",
                "markup", "tax", "price", "sum", "profit"]
        labels = ["№", "Наименование / артикул", "Кол-во", "Себестоимость/шт", "Логистика",
                   "Доп. расходы", "Комиссия", "Наценка %", "Налог %", "Цена за шт",
                   "Сумма контракта", "Чистая прибыль"]
        self.calc_tree = ttk.Treeview(self.tab_calculator, columns=cols, show="headings")
        for key, label in zip(cols, labels):
            self.calc_tree.heading(key, text=label, anchor="center")
            width = 200 if key == "name" else 105
            self.calc_tree.column(key, width=width, anchor="center")
        self.calc_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.calc_tree.bind("<Double-1>", lambda e: self._edit_calc_row())
        self.calc_tree.tag_configure("profit_row", background="#F2EEFA")

        totals = ttk.Frame(self.tab_calculator, padding=8)
        totals.pack(fill="x")
        self.calc_totals_var = tk.StringVar(value="Позиций: 0   Общее кол-во: 0   "
                                                    "Сумма контрактов: 0 ₽   Чистая прибыль: 0 ₽")
        ttk.Label(totals, textvariable=self.calc_totals_var,
                  font=("TkDefaultFont", BASE_FONT_SIZE, "bold")).pack(anchor="w")

    def refresh_calculator(self):
        for item in self.calc_tree.get_children():
            self.calc_tree.delete(item)

        total_items = 0
        total_qty = 0.0
        total_sum = 0.0
        total_profit = 0.0

        rows = db.fetch_calculator_rows(self.conn)
        for i, row in enumerate(rows, start=1):
            try:
                price, r_sum, profit = calc_contract_price(
                    row["qty"], row["cost"], row["logistics"], row["extra"],
                    row["commission"], row["markup"], row["tax"])
            except ValueError:
                price, r_sum, profit = None, None, None

            if price is not None:
                total_items += 1
                total_qty += row["qty"]
                total_sum += r_sum
                total_profit += profit

            values = [i, row["name"], fmt_qty(row["qty"]), fmt_money(row["cost"]),
                      fmt_money(row["logistics"]), fmt_money(row["extra"]), fmt_money(row["commission"]),
                      f"{row['markup']:g}%", f"{row['tax']:g}%",
                      fmt_money(price) if price is not None else "",
                      fmt_money(r_sum) if r_sum is not None else "",
                      fmt_money(profit) if profit is not None else ""]
            tree_insert_wrapped(self.calc_tree, "", "end", iid=str(row["id"]), values=values)

        self.calc_totals_var.set(
            f"Позиций: {total_items}   Общее кол-во: {fmt_qty(total_qty)}   "
            f"Сумма контрактов: {fmt_money(total_sum)}   Чистая прибыль: {fmt_money(total_profit)}"
        )

    def _selected_calc_id(self):
        sel = self.calc_tree.selection()
        return int(sel[0]) if sel else None

    def _add_calc_row(self):
        def on_save(data, _id):
            db.insert_calculator_row(self.conn, data)
            self.refresh_calculator()
        self._update_health_status()
        CalculatorRowDialog(self, on_save)

    def _edit_calc_row(self):
        rid = self._selected_calc_id()
        if rid is None:
            messagebox.showinfo("Выбор позиции", "Сначала выберите строку в таблице.")
            return
        existing = db.fetch_calculator_row_by_id(self.conn, rid)
        if existing is None:
            return

        def on_save(data, _id):
            db.update_calculator_row(self.conn, rid, data)
            self.refresh_calculator()
        CalculatorRowDialog(self, on_save, existing=existing)

    def _delete_calc_row(self):
        rid = self._selected_calc_id()
        if rid is None:
            messagebox.showinfo("Выбор позиции", "Сначала выберите строку в таблице.")
            return
        if messagebox.askyesno("Удаление", "Удалить позицию из расчёта?"):
            db.delete_calculator_row(self.conn, rid)
            self.refresh_calculator()

    def _clear_calc_rows(self):
        if not messagebox.askyesno("Очистить всё", "Удалить все позиции расчёта?"):
            return
        db.clear_calculator_rows(self.conn)
        self.refresh_calculator()

    def _export_calc_csv(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV файл", "*.csv")],
            initialfile="raschet_kontrakta.csv")
        if not filepath:
            return
        import csv
        rows = db.fetch_calculator_rows(self.conn)
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["№", "Наименование", "Кол-во", "Себестоимость", "Логистика",
                              "Доп.расходы", "Комиссия", "Наценка %", "Налог %",
                              "Цена за шт", "Сумма контракта", "Чистая прибыль"])
            for i, row in enumerate(rows, start=1):
                try:
                    price, r_sum, profit = calc_contract_price(
                        row["qty"], row["cost"], row["logistics"], row["extra"],
                        row["commission"], row["markup"], row["tax"])
                except ValueError:
                    price, r_sum, profit = None, None, None
                writer.writerow([
                    i, row["name"], row["qty"], row["cost"], row["logistics"], row["extra"],
                    row["commission"], row["markup"], row["tax"],
                    round(price, 2) if price is not None else "",
                    round(r_sum, 2) if r_sum is not None else "",
                    round(profit, 2) if profit is not None else "",
                ])
        messagebox.showinfo("Экспорт", f"Расчёт выгружен в файл:\n{filepath}", parent=self)

    # ---------------- Общие ----------------
    def refresh_all(self):
        current_year = date.today().year
        db_years = set(db.distinct_years(self.conn))
        future_years = set(range(current_year, current_year + YEARS_AHEAD + 1))
        all_years = sorted(db_years | future_years | {current_year})
        self.year_combo["values"] = ["Все"] + [str(y) for y in all_years]
        self.summary_year_combo["values"] = [str(y) for y in all_years]

        self.refresh_purchases()
        self.refresh_attention()
        self.refresh_summary()
        self.refresh_stock()
        self.refresh_competitors()
        self.refresh_calculator()

    def _show_daily_attention_popup(self):
        """Одно компактное окно «Требует внимания» не чаще раза в день."""
        items = reminder_worker.attention_items(self.conn)
        if not items:
            return
        today_key = date.today().isoformat()
        try:
            if db.get_setting(self.conn, "last_attention_popup_date", "") == today_key:
                return
        except Exception:
            pass
        counts = reminder_worker.attention_counts(items)
        lines = [
            (f"Всего: {counts['total']} | Просрочено: {counts['overdue']} | "
             f"Подписание: {counts['signing']} | Исполнение: {counts['execution']} | "
             f"Оплата: {counts['payment']} | Склад: {counts['stock']}"),
            "",
        ]
        for item in items[:12]:
            if item["kind"] == "stock":
                lines.append(
                    f"— Склад: {item['product']} — доступно {fmt_qty(item.get('available', 0))} шт."
                )
                continue
            days = item.get("days")
            if days is None:
                remain = ""
            elif days < 0:
                remain = f"просрочено на {abs(days)} дн."
            elif days == 0:
                remain = "сегодня"
            else:
                remain = f"осталось {days} дн."
            lines.append(
                f"— {item['type']}: {item['customer']} / № {item['contract_no']} — {remain}"
            )
        if len(items) > 12:
            lines.append(f"… и ещё {len(items) - 12}. Полный список — во вкладке «Требует внимания».")
        messagebox.showwarning("Требует внимания", "\n".join(lines), parent=self)
        try:
            db.set_setting(self.conn, "last_attention_popup_date", today_key)
        except Exception as exc:
            _log(f"Требует внимания: не удалось сохранить дату показа: {exc}")

    def _show_daily_signing_reminder(self):
        """Напоминание о подписании не чаще одного раза в календарный день."""
        signing = self._pending_signing_reminders()
        if not signing:
            return
        today_key = date.today().isoformat()
        try:
            if db.get_setting(self.conn, "last_signing_popup_date") == today_key:
                return
        except Exception:
            pass

        lines = ["Необходимо подписать контракты:"]
        for r, _etype, state, days, d in signing[:20]:
            if state == "overdue":
                remain = f"срок истёк на {abs(days)} дн."
            elif days == 0:
                remain = "крайний срок сегодня"
            else:
                remain = f"осталось {days} дн."
            lines.append(
                f"  — {r['customer'] or 'Без заказчика'} / № {r['contract_no'] or '—'} "
                f"— подписать до {d.strftime(DATE_FMT) if d else '—'} ({remain})"
            )
        if len(signing) > 20:
            lines.append(f"  … и ещё {len(signing) - 20}")
        messagebox.showwarning("Подписание контрактов", "\n".join(lines), parent=self)
        try:
            db.set_setting(self.conn, "last_signing_popup_date", today_key)
        except Exception as e:
            _log(f"Напоминание о подписании: не удалось сохранить дату показа: {e}")

    def _daily_signing_tick(self):
        if self._closing or self.conn is None:
            return
        try:
            self._send_attention_email_if_due()
        finally:
            if not self._closing:
                self.after(self.DAILY_SIGNING_CHECK_MS, self._daily_signing_tick)

    def _show_startup_reminders(self):
        execution = [t for t in self._pending_reminders() if t[2] in ("red", "yellow", "overdue")]
        payment = self._pending_payment_reminders()
        events = execution + payment
        if not events:
            return
        lines = []
        if execution:
            lines.append("Срок исполнения подходит:")
            for r, _etype, state, days, _d in execution[:10]:
                label = "срок истёк" if days is not None and days < 0 else f"осталось {days} дн."
                lines.append(f"  — {r['customer'] or 'Без заказчика'} / № {r['contract_no'] or '—'} — {label}")
        if payment:
            if lines:
                lines.append("")
            lines.append("Срок оплаты подходит:")
            for r, _etype, state, days, _d in payment[:10]:
                label = "срок истёк" if state == "overdue" else f"осталось {days} дн."
                lines.append(f"  — {r['customer'] or 'Без заказчика'} / № {r['contract_no'] or '—'} — {label}")
        messagebox.showwarning("Напоминания", "\n".join(lines), parent=self)


def main():
    if "--reminders-only" in sys.argv:
        raise SystemExit(reminder_worker.run_headless())
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()