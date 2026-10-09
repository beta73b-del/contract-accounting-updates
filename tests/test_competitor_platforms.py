"""Площадки конкурентов: независимые закупки, сравнение цен и безопасная миграция."""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db


def _offer(conn, name, inn, platform, qty, price, date):
    return db.insert_competitor_record(conn, {
        "competitor": name, "competitor_inn": inn, "product": "Рутокен Lite 1010",
        "trade_type": "Аукцион", "platform": platform,
        "qty": qty, "unit_price": price, "purchase_date": date,
    })


def test_independent_platforms_and_price_history():
    conn = db.get_connection(":memory:")
    first = _offer(conn, "ООО А", "1234567890", "РТС-Тендер", 150, 1250, "2026-09-01")
    second = _offer(conn, "А", "1234567890", "Сбербанк-АСТ", 250, 1200, "2026-09-03")
    third = _offer(conn, "А", "1234567890", "РТС-Тендер", 300, 1180, "2026-09-05")
    offers = {r["id"]: r for r in db.competitor_offer_analysis(conn)}
    assert len(offers) == 3
    assert (offers[first]["qty"], offers[second]["qty"], offers[third]["qty"]) == (150, 250, 300)
    assert offers[second]["price_change_pct"] is None  # другая ЭТП, не сравниваем
    assert offers[third]["previous_qty"] == 150
    assert round(offers[third]["price_change_pct"], 5) == -0.056
    update = dict(db.fetch_competitor_record_by_id(conn, second))
    update["platform"] = "ЕЭТП"
    db.update_competitor_record(conn, second, update)
    assert db.fetch_competitor_record_by_id(conn, second)["platform"] == "ЕЭТП"
    assert len(db.fetch_competitor_records(conn)) == 3
    conn.close()


def test_legacy_database_migration_with_safe_backup():
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "old.db")
        old = sqlite3.connect(path)
        old.execute("""CREATE TABLE competitor_records
            (id INTEGER PRIMARY KEY AUTOINCREMENT, competitor TEXT, competitor_inn TEXT,
             product TEXT, trade_type TEXT, qty REAL, unit_price REAL, purchase_date TEXT)""")
        old.execute("""INSERT INTO competitor_records
            (competitor, competitor_inn, product, trade_type, qty, unit_price, purchase_date)
            VALUES ('Тест', '1234567890', 'Рутокен Lite 1010', 'Аукцион', 150, 1250, '2026-09-01')""")
        old.commit()
        old.close()
        conn = db.get_connection(path)
        rows = db.fetch_competitor_records(conn)
        assert len(rows) == 1
        assert rows[0]["qty"] == 150 and rows[0]["unit_price"] == 1250
        assert rows[0]["competitor_inn"] == "1234567890"
        assert rows[0]["platform"] is None
        backup_path = path + ".before_competitor_platform.bak"
        assert os.path.isfile(backup_path)
        with sqlite3.connect(backup_path) as back:
            assert "platform" not in [r[1] for r in back.execute("PRAGMA table_info(competitor_records)")]
            assert back.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()
        conn = db.get_connection(path)
        assert len(db.fetch_competitor_records(conn)) == 1
        assert db.fetch_competitor_records(conn)[0]["platform"] is None
        conn.close()


if __name__ == "__main__":
    test_independent_platforms_and_price_history()
    test_legacy_database_migration_with_safe_backup()
    print("PASS: competitor platform analysis and legacy migration")
