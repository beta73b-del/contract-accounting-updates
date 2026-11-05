# -*- coding: utf-8 -*-
"""Read-only board and competitor analytics: no production files or network."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db
import competitor_analysis as analysis

LITE = "Рутокен Lite 1010"
ECP = "Рутокен ЭЦП 3.0 3120"


def add(conn, competitor, inn, product, price, day, platform=None, qty=10):
    return db.insert_competitor_record(conn, {
        "competitor": competitor, "competitor_inn": inn, "product": product,
        "trade_type": "Аукцион", "platform": platform, "qty": qty,
        "unit_price": price, "purchase_date": day,
    })


def test_empty_board_always_has_two_models():
    conn = db.get_connection(":memory:")
    try:
        board = analysis.product_price_board(conn)
        assert list(board) == [LITE, ECP]
        assert board[LITE] == [] and board[ECP] == []
        assert analysis.competitor_profiles(board) == []
        stats = analysis.price_statistics([])
        assert stats["priced"] == 0 and stats["median"] is None
    finally:
        conn.close()


def test_board_reads_existing_records_without_mutating_database():
    conn = db.get_connection(":memory:")
    try:
        first = add(conn, "ООО А", "1234567890", "Рутокен 1010", 1300, "2026-10-01", "РТС-Тендер", 20)
        second = add(conn, "ООО А", "1234567890", ECP, 2200, "2026-09-22", "Сбербанк-АСТ")
        third = add(conn, "ООО Б", "9876543210", LITE, 1200, "2026-10-02", "РТС-Тендер")
        add(conn, "Другая организация", "1111111111", "JaCarta LT", 950, "2026-10-03", "ЕЭТП")
        changes_before = conn.total_changes
        board = analysis.product_price_board(conn)
        assert len(board[LITE]) == 2 and len(board[ECP]) == 1
        assert [r["id"] for r in board[LITE]] == [third, first]
        assert board[LITE][1]["product"] == LITE
        assert board[ECP][0]["id"] == second
        profiles = analysis.competitor_profiles(board)
        assert len(profiles) == 2
        assert conn.total_changes == changes_before, "Read-only analysis must never change SQLite"
    finally:
        conn.close()


def test_profile_separates_models_platforms_and_company_inns():
    conn = db.get_connection(":memory:")
    try:
        add(conn, "Компания А", "1234567890", LITE, 1300, "2026-10-01", "РТС-Тендер", 10)
        add(conn, "ООО Компания А", "1234567890", LITE, 1100, "2026-10-03", "РТС-Тендер", 1000)
        add(conn, "Компания А", "1234567890", ECP, 2100, "2026-10-04", "Сбербанк-АСТ", 30)
        add(conn, "Компания А", "1234567890", ECP, 2300, "2026-10-05", "ЕЭТП", 10)
        # Same legal name, different INN: strictly separate competitors.
        add(conn, "Компания А", "9999999999", LITE, 900, "2026-10-07", "ЕЭТП", 50)
        board = analysis.product_price_board(conn)
        profiles = analysis.competitor_profiles(board)
        assert len(profiles) == 2
        a = next(p for p in profiles if p["competitor_inn"] == "1234567890")
        assert a["observations"] == 4
        assert a["last_date"] == "2026-10-05"
        product_stats = {p["product"]: p for p in a["products"]}
        assert product_stats[LITE]["median"] == 1200
        assert product_stats[LITE]["average"] == 1200  # equal observation weights, not qty
        assert product_stats[ECP]["median"] == 2200
        assert len(a["platforms"]) == 3  # same competitor, different product/ETP
        rts = next(p for p in a["platforms"] if p["platform"] == "РТС-Тендер")
        assert rts["median"] == 1200 and rts["observations"] == 2
        other = next(p for p in profiles if p["competitor_inn"] == "9999999999")
        assert other["products"][0]["median"] == 900
    finally:
        conn.close()


def test_missing_values_and_historical_dates():
    conn = db.get_connection(":memory:")
    try:
        # Bypass the UI parser: older databases may contain DD.MM.YYYY.
        conn.execute(
            "INSERT INTO competitor_records(competitor, product, unit_price, purchase_date, platform) "
            "VALUES(?, ?, ?, ?, ?)",
            ("Компания Б", LITE, None, "05.10.2026", None)
        )
        add(conn, "Компания Б", None, LITE, 950, "2026-10-04", " ")
        conn.commit()
        board = analysis.product_price_board(conn)
        assert [r["purchase_date"] for r in board[LITE]] == ["05.10.2026", "2026-10-04"]
        stats = analysis.price_statistics(board[LITE])
        assert stats["observations"] == 2 and stats["priced"] == 1
        assert stats["median"] == 950
        profile = analysis.competitor_profiles(board)[0]
        assert profile["competitor_inn"] == ""
        assert any(x["platform"] == "Не указана" for x in profile["platforms"])
    finally:
        conn.close()


def test_invalid_price_is_not_used_in_statistics():
    entries = [
        {"unit_price": None}, {"unit_price": 0}, {"unit_price": -10},
        {"unit_price": float("nan")}, {"unit_price": float("inf")},
        {"unit_price": 1000}, {"unit_price": 1300},
    ]
    stats = analysis.price_statistics(entries)
    assert stats["observations"] == 7
    assert stats["priced"] == 2
    assert stats["minimum"] == 1000 and stats["maximum"] == 1300
    assert stats["median"] == 1150 and stats["average"] == 1150


def test_no_name_without_inn_is_not_falsely_attributed():
    assert analysis.competitor_identity({"competitor": "  ", "competitor_inn": None}) is None
    assert analysis.competitor_identity({"competitor": "ЁЖ", "competitor_inn": None}) == "name:еж"
    assert analysis.competitor_identity({"competitor": "Компания", "competitor_inn": "123-456"}) == "inn:123456"


if __name__ == "__main__":
    for test in (
        test_empty_board_always_has_two_models,
        test_board_reads_existing_records_without_mutating_database,
        test_profile_separates_models_platforms_and_company_inns,
        test_missing_values_and_historical_dates,
        test_invalid_price_is_not_used_in_statistics,
        test_no_name_without_inn_is_not_falsely_attributed,
    ):
        test()
        print("PASS", test.__name__)
