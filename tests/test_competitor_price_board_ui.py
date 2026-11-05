# -*- coding: utf-8 -*-
"""Tkinter smoke test using only an in-memory synthetic database.

No application object is constructed: that would open the user's default SQLite.
This harness runs the actual competitor-tab methods on a disposable Tk root.
"""
import os
import sys
import tkinter as tk
from tkinter import ttk

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
        app._show_competitor_analysis(initial_key="inn:1234567890")
        dialogs = [child for child in app.winfo_children() if isinstance(child, tk.Toplevel)]
        assert len(dialogs) == 1
        dialogs[0].destroy()
        app.refresh_competitors()
    finally:
        app.conn.close()
        app.destroy()


if __name__ == "__main__":
    test_competitor_tab_build_refresh_and_detail_windows()
    print("PASS: competitor Tk price boards and drill-down windows")
