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
    out = _center_multiline_lines(out or [""], font)
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

    DATE_FIELD_KEYS = ("created_at", "contract_date", "sign_deadline", "deadline", "handover_date", "payment_deadline")

    HEADER_LABELS = [
        ("created_at", "Дата (ДД.ММ.ГГГГ)", "entry", None),
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
        ("exec_status", "Исполнение", "combo", db.EXEC_STATUS_OPTIONS),
        ("note", "Примечание", "text", None),
    ]