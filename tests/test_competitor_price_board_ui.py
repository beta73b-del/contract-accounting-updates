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
        assert len(model_actions) == 2
        assert all("анализ конкурентов" in btn.cget("text") for btn in model_actions)
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


if __name__ == "__main__":
    test_competitor_tab_build_refresh_and_detail_windows()
    test_manual_add_edit_delete_actions()
    print("PASS: competitor Tk price boards and manual CRUD controls")
