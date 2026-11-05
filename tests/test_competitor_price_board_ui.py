# -*- coding: utf-8 -*-
"""Tkinter smoke test using only an in-memory synthetic database.

No application object is constructed: that would open the user's default SQLite.
This harness runs the actual competitor-tab methods on a disposable Tk root.
"""
import os
import sys
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db
import main

LITE, ECP = db.FIXED_PRODUCTS


class CompetitorHarness(tk.Tk):
    _build_competitors_tab = main.App._build_competitors_tab
    _show_competitor_expanded = main.App._show_competitor_expanded
    _fill_competitor_tree = main.App._fill_competitor_tree
    _remember_competitor_selection = main.App._remember_competitor_selection
    _selected_competitor_id = main.App._selected_competitor_id
    refresh_competitors = main.App.refresh_competitors
    _show_competitor_price_details = main.App._show_competitor_price_details
    _show_competitor_analysis = main.App._show_competitor_analysis
    _add_competitor = main.App._add_competitor
    _edit_competitor = main.App._edit_competitor
    _delete_competitor = main.App._delete_competitor

    def __init__(self):
        super().__init__()
        self.withdraw()
        self.conn = db.get_connection(":memory:")
        self.tab_competitors = ttk.Frame(self)
        self.tab_competitors.pack(fill="both", expand=True)
        self._build_competitors_tab()


def test_competitor_tab_build_refresh_and_detail_windows():
    try:
        app = CompetitorHarness()
    except tk.TclError as exc:
        if sys.platform.startswith("win"):
            raise
        print("SKIP: Tk display not available:", exc)
        return
    try:
        assert list(app.competitor_board_trees) == [LITE, ECP]
        # Колонки обоих разделов должны иметь одинаковый центр по данным и заголовкам.
        for model, tree in app.competitor_board_trees.items():
            for column in tree["columns"]:
                assert str(tree.heading(column, "anchor")) == "center", (model, column, "header")
                assert str(tree.column(column, "anchor")) == "center", (model, column, "values")
        app.refresh_competitors()
        toolbar_labels = {
            widget.cget("text") for widget in app.tab_competitors.winfo_children()[0].winfo_children()
            if isinstance(widget, ttk.Button)
        }
        assert {"Добавить запись", "Редактировать", "Удалить"}.issubset(toolbar_labels)
        assert "Обновить таблицы" not in toolbar_labels
        assert "Анализ конкурента" not in toolbar_labels
        product_buttons = [
            w for w in app.tab_competitors.winfo_children()
            if isinstance(w, ttk.Panedwindow)
        ][0]
        model_actions = [
            child for panel in product_buttons.panes()
            for child in app.nametowidget(panel).winfo_children()
            if isinstance(child, ttk.Frame)
            for widget in child.winfo_children()
            if isinstance(widget, ttk.Button)
            for child in [widget]
        ]
        analysis_buttons = [b for b in model_actions if "анализ конкурентов" in b.cget("text")]
        expanded_buttons = [b for b in model_actions if b.cget("text") == "Развернуть таблицу"]
        assert len(analysis_buttons) == 2 and len(expanded_buttons) == 2
        with patch.object(app, "_show_competitor_analysis") as analysis:
            analysis_buttons[0].invoke()
            analysis.assert_called_once_with(product_filter=LITE)
        with patch.object(app, "_show_competitor_expanded") as expanded:
            expanded_buttons[1].invoke()
            expanded.assert_called_once_with(ECP)
        assert "Записей: 0" in app.competitor_board_summaries[LITE].get()
        # Calls may be repeated while no records are present.
        app.refresh_competitors()
        record_id = db.insert_competitor_record(app.conn, {
            "competitor": "Тестовая компания", "competitor_inn": "1234567890",
            "product": LITE, "unit_price": 1275,
            "platform": "РТС-Тендер", "qty": 25,
            "purchase_date": "2026-10-15", "trade_type": "Аукцион",
        })
        db.insert_competitor_record(app.conn, {
            "competitor": "Тестовая компания", "competitor_inn": "1234567890",
            "product": ECP, "unit_price": 2300,
            "platform": "ЕЭТП", "qty": 5,
            "purchase_date": "2026-10-16", "trade_type": "Аукцион",
        })
        app.refresh_competitors()
        assert len(app.competitor_board_trees[LITE].get_children()) == 1
        assert len(app.competitor_board_trees[ECP].get_children()) == 1
        app.competitor_board_trees[LITE].selection_set(str(record_id))
        assert app._selected_competitor_id() is not None or app.competitor_board_trees[LITE].selection()
        app._show_competitor_price_details(LITE)
        dialogs = [child for child in app.winfo_children() if isinstance(child, tk.Toplevel)]
        assert len(dialogs) == 1
        dialogs[0].destroy()
        app._show_competitor_analysis(product_filter=LITE)
        dialogs = [child for child in app.winfo_children() if isinstance(child, tk.Toplevel)]
        assert len(dialogs) == 1
        # В расшифровке конкурента также центрируем строки всех таблиц.
        def nested_trees(widget):
            found = [widget] if isinstance(widget, ttk.Treeview) else []
            for child in widget.winfo_children():
                found.extend(nested_trees(child))
            return found
        detail_trees = nested_trees(dialogs[0])
        assert len(detail_trees) == 4, len(detail_trees)
        for tree in detail_trees:
            for column in tree["columns"]:
                assert str(tree.heading(column, "anchor")) == "center", column
                assert str(tree.column(column, "anchor")) == "center", column
        dialogs[0].destroy()
        app.refresh_competitors()
    finally:
        app.conn.close()
        app.destroy()


def test_manual_add_edit_delete_actions():
    try:
        app = CompetitorHarness()
    except tk.TclError as exc:
        if sys.platform.startswith("win"):
            raise
        print("SKIP: Tk display not available:", exc)
        return
    try:
        def new_entry(_parent, on_save, existing=None):
            assert existing is None
            on_save({
                "competitor": "Тест", "competitor_inn": "1234567890",
                "product": LITE, "platform": "РТС-Тендер",
                "trade_type": "Аукцион", "qty": 150, "unit_price": 1250,
                "purchase_date": "2026-10-15",
            }, None)

        with patch.object(main, "CompetitorDialog", side_effect=new_entry):
            app._add_competitor()
        rows = db.fetch_competitor_records(app.conn)
        assert len(rows) == 1 and rows[0]["qty"] == 150
        rid = int(rows[0]["id"])
        tree = app.competitor_board_trees[LITE]
        tree.selection_set(str(rid))
        app._remember_competitor_selection(LITE)

        def change_entry(_parent, on_save, existing=None):
            assert int(existing["id"]) == rid
            data = dict(existing)
            data["qty"] = 250
            data["unit_price"] = 1200
            on_save(data, rid)

        with patch.object(main, "CompetitorDialog", side_effect=change_entry):
            app._edit_competitor()
        rows = db.fetch_competitor_records(app.conn)
        assert len(rows) == 1 and int(rows[0]["id"]) == rid
        assert rows[0]["qty"] == 250 and rows[0]["unit_price"] == 1200
        tree.selection_set(str(rid))
        app._remember_competitor_selection(LITE)
        with patch.object(main.messagebox, "askyesno", return_value=True):
            app._delete_competitor()
        assert len(db.fetch_competitor_records(app.conn)) == 0
    finally:
        app.conn.close()
        app.destroy()


def test_expanded_view_many_rows_and_crud_without_wrong_selection():
    """Expanded mode uses full screen space; records remain in one SQLite DB."""
    try:
        app = CompetitorHarness()
    except tk.TclError as exc:
        if sys.platform.startswith("win"):
            raise
        print("SKIP: Tk display not available:", exc)
        return
    try:
        first_id = None
        for i in range(140):
            rid = db.insert_competitor_record(app.conn, {
                "competitor": "Тестовая организация", "competitor_inn": "1234567890",
                "product": LITE, "platform": "РТС-Тендер", "trade_type": "Аукцион",
                "qty": i + 1, "unit_price": 1250 + i,
                "purchase_date": "2026-10-15",
            })
            if first_id is None:
                first_id = rid
        db.insert_competitor_record(app.conn, {
            "competitor": "Вторая организация", "competitor_inn": "9876543210",
            "product": ECP, "platform": "ЕЭТП", "trade_type": "Аукцион",
            "qty": 20, "unit_price": 2300, "purchase_date": "2026-10-16",
        })
        app.refresh_competitors()
        changes_before = app.conn.total_changes
        app._show_competitor_expanded(LITE)
        win = app._competitor_expanded_window
        assert win is not None and win.winfo_exists()
        assert tuple(map(str, win.resizable())) == ("1", "1")
        if sys.platform.startswith("win"):
            assert win.state() == "zoomed", win.state()
        notebook = app._competitor_expanded_notebook
        assert notebook.index(notebook.select()) == 0
        lite_tree = app._competitor_expanded_trees[LITE]
        ecp_tree = app._competitor_expanded_trees[ECP]
        assert len(lite_tree.get_children()) == 140
        assert len(ecp_tree.get_children()) == 1
        assert set(str(lite_tree.grid_info()["sticky"])) == set("nsew")
        assert len(lite_tree["columns"]) == 5
        for tree in (lite_tree, ecp_tree):
            for column in tree["columns"]:
                assert str(tree.column(column, "anchor")) == "center"
        assert app.conn.total_changes == changes_before
        app._show_competitor_expanded(ECP)
        assert app._competitor_expanded_window is win
        assert notebook.index(notebook.select()) == 1

        app._competitor_selected_id = first_id
        buttons = {
            w.cget("text"): w for w in win.winfo_children()[0].winfo_children()
            if isinstance(w, ttk.Button)
        }
        assert {"Добавить запись", "Редактировать", "Удалить",
                "Открыть запись", "Полный экран (F11)", "Закрыть просмотр"} <= buttons.keys()
        ecp_tree.selection_remove(ecp_tree.selection())
        with patch.object(main.messagebox, "showinfo") as msg:
            with patch.object(app, "_edit_competitor") as edit:
                buttons["Редактировать"].invoke()
                msg.assert_called_once()
                edit.assert_not_called()

        notebook.select(0)
        lite_tree.selection_set(str(first_id))
        def edited_dialog(parent, on_save, existing=None):
            assert parent is win and int(existing["id"]) == first_id
            new = dict(existing)
            new["unit_price"] = 1599
            on_save(new, first_id)
        with patch.object(main, "CompetitorDialog", side_effect=edited_dialog):
            buttons["Редактировать"].invoke()
        assert len(lite_tree.get_children()) == 140
        assert int(app.conn.execute(
            "SELECT unit_price FROM competitor_records WHERE id=?", (first_id,)
        ).fetchone()[0]) == 1599
        assert str(first_id) in lite_tree.selection()

        def added_dialog(parent, on_save, existing=None):
            assert parent is win and existing is None
            on_save({
                "competitor": "Тест Б", "competitor_inn": "5555555555",
                "product": ECP, "platform": "Сбербанк-АСТ",
                "trade_type": "Аукцион", "qty": 18,
                "unit_price": 2000, "purchase_date": "2026-10-17",
            }, None)
        with patch.object(main, "CompetitorDialog", side_effect=added_dialog):
            buttons["Добавить запись"].invoke()
        assert len(ecp_tree.get_children()) == 2
        assert len(app.competitor_board_trees[ECP].get_children()) == 2

        with patch.object(main.messagebox, "askyesno", return_value=True):
            buttons["Удалить"].invoke()
        assert len(lite_tree.get_children()) == 139
        assert len(app.competitor_board_trees[LITE].get_children()) == 139
        assert len(ecp_tree.get_children()) == 2
        buttons["Закрыть просмотр"].invoke()
        assert app._competitor_expanded_window is None
        assert app._competitor_expanded_trees == {}
        app.refresh_competitors()
    finally:
        app.conn.close()
        app.destroy()


if __name__ == "__main__":
    test_competitor_tab_build_refresh_and_detail_windows()
    test_manual_add_edit_delete_actions()
    test_expanded_view_many_rows_and_crud_without_wrong_selection()
    print("PASS: competitor Tk price boards, CRUD and expanded 140-row view")
